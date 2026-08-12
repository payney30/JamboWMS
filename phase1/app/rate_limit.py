"""
In-memory rate limiting / lockout for both the LOC login endpoint and the
public requester-submission endpoint.

Login gets two layers, both process-local (a plain dict guarded by a lock
— no external store):

1. Per-email lockout: after EMAIL_MAX_ATTEMPTS failed logins against one
   account within EMAIL_WINDOW_SECONDS, that email is locked out for
   EMAIL_LOCKOUT_SECONDS. Stops repeated password guessing against a
   single known account.
2. Per-IP rate limit: caps total failed attempts from one IP within
   IP_WINDOW_SECONDS, regardless of which email they're aimed at. Slows
   down an attacker trying many different emails from one place, which
   the per-email lockout alone wouldn't catch.

The public form gets its own, simpler per-IP limiter (no lockout, no
email tracking — there's no account to lock) since spam/abuse protection
there is about capping submission volume, not guarding credentials.

This is intentionally simple and sized for the single-process deployment
this app is built for (an ~2-week event, one uvicorn worker). It does NOT
share state across multiple processes/workers. If this ever moves behind
a load balancer with multiple app instances, replace the module-level
dicts below with a shared store (e.g. Redis) so a lockout applies
consistently no matter which instance handles the next request —
otherwise an attacker can just get routed to a fresh instance with no
memory of their prior failures.
"""
import threading
import time
from collections import defaultdict
from typing import Optional

EMAIL_MAX_ATTEMPTS = 5
EMAIL_WINDOW_SECONDS = 15 * 60
EMAIL_LOCKOUT_SECONDS = 15 * 60

IP_MAX_ATTEMPTS = 20
IP_WINDOW_SECONDS = 5 * 60

# Separate limiter for the public, no-login requester form (app/routers/
# public.py): this has nothing to do with login attempts, so it gets its
# own counter rather than sharing the login IP bucket above. Sized to let
# one legitimate person submit a handful of requests without friction
# while still stopping a flood.
#
# Bug fix (end-to-end testing 8/10/26): 8 per 10 minutes was too tight
# for real use, not just demo rehearsal — this limit is per-IP, and at
# an event like this, "one IP" often means an entire camp area sharing
# one Wi-Fi access point or hotspot. A handful of genuinely unrelated
# people submitting real work orders from the same shared connection
# could exhaust this bucket collectively, blocking legitimate
# submissions that have nothing to do with each other. Raised well past
# what a single person filling out the form by hand would ever hit,
# while staying far below what an automated flood would aim for.
PUBLIC_WO_MAX_SUBMISSIONS = 30
PUBLIC_WO_WINDOW_SECONDS = 10 * 60

# Enhancement backlog Phase 1 (PRD §13#4): the phone-anchored status
# lookup has no second factor (see routers/public.py docstring), so it
# gets its own, slightly more generous limiter than submissions — normal
# use is "check status a few times," but this caps how fast someone could
# try many phone numbers against the endpoint.
PUBLIC_LOOKUP_MAX_ATTEMPTS = 20
PUBLIC_LOOKUP_WINDOW_SECONDS = 10 * 60

_lock = threading.Lock()
_email_failures: dict[str, list[float]] = defaultdict(list)
_email_locked_until: dict[str, float] = {}
_ip_failures: dict[str, list[float]] = defaultdict(list)
_public_wo_submissions: dict[str, list[float]] = defaultdict(list)
_public_lookup_attempts: dict[str, list[float]] = defaultdict(list)


def _prune(timestamps: list[float], window_seconds: float, now: float) -> list[float]:
    return [t for t in timestamps if now - t < window_seconds]


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def check_locked(email: str, ip: str) -> Optional[float]:
    """
    Returns the number of seconds until the caller may try again, or
    None if they're clear to attempt a login right now. Must be called
    (and honored) BEFORE checking the password, so a locked-out account
    doesn't leak whether a guessed password would otherwise have been
    correct.
    """
    now = time.time()
    email = _normalize_email(email)
    with _lock:
        locked_until = _email_locked_until.get(email)
        if locked_until is not None:
            if locked_until > now:
                return locked_until - now
            # lockout has expired — clear it and the attempts that caused it
            del _email_locked_until[email]
            _email_failures[email] = []

        ip_recent = _prune(_ip_failures[ip], IP_WINDOW_SECONDS, now)
        _ip_failures[ip] = ip_recent
        if len(ip_recent) >= IP_MAX_ATTEMPTS:
            oldest = min(ip_recent)
            return IP_WINDOW_SECONDS - (now - oldest)

    return None


def record_failure(email: str, ip: str) -> None:
    now = time.time()
    email = _normalize_email(email)
    with _lock:
        recent = _prune(_email_failures[email], EMAIL_WINDOW_SECONDS, now)
        recent.append(now)
        _email_failures[email] = recent
        if len(recent) >= EMAIL_MAX_ATTEMPTS:
            _email_locked_until[email] = now + EMAIL_LOCKOUT_SECONDS

        _ip_failures[ip].append(now)


def record_success(email: str) -> None:
    """Clear any accumulated failure count for this email on a successful login."""
    email = _normalize_email(email)
    with _lock:
        _email_failures.pop(email, None)
        _email_locked_until.pop(email, None)


def check_public_submission_limit(ip: str) -> Optional[float]:
    """Returns seconds until the caller may submit again, or None if clear."""
    now = time.time()
    with _lock:
        recent = _prune(_public_wo_submissions[ip], PUBLIC_WO_WINDOW_SECONDS, now)
        _public_wo_submissions[ip] = recent
        if len(recent) >= PUBLIC_WO_MAX_SUBMISSIONS:
            oldest = min(recent)
            return PUBLIC_WO_WINDOW_SECONDS - (now - oldest)
    return None


def record_public_submission(ip: str) -> None:
    with _lock:
        _public_wo_submissions[ip].append(time.time())


def check_public_lookup_limit(ip: str) -> Optional[float]:
    """Enhancement backlog Phase 1 (PRD §13#4). Returns seconds until the
    caller may look up a WO by phone again, or None if clear."""
    now = time.time()
    with _lock:
        recent = _prune(_public_lookup_attempts[ip], PUBLIC_LOOKUP_WINDOW_SECONDS, now)
        _public_lookup_attempts[ip] = recent
        if len(recent) >= PUBLIC_LOOKUP_MAX_ATTEMPTS:
            oldest = min(recent)
            return PUBLIC_LOOKUP_WINDOW_SECONDS - (now - oldest)
    return None


def record_public_lookup(ip: str) -> None:
    with _lock:
        _public_lookup_attempts[ip].append(time.time())


def reset_all() -> None:
    """Test-only: clear all in-memory state between test cases."""
    with _lock:
        _email_failures.clear()
        _email_locked_until.clear()
        _ip_failures.clear()
        _public_wo_submissions.clear()
        _public_lookup_attempts.clear()
