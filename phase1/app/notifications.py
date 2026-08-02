"""
Enhancement backlog Phase 25 (NJ2026_Work_Order_System_PRD.md §17#6-8):
SMS + email notifications on WO status milestones — submission, moved
to Work In Progress, and closed. (Worker-tasking notification was
explicitly descoped, 8/2/26 — internal to the team, the requester
doesn't need those details.)

Sent automatically by both SMS and email whenever the corresponding
contact info exists — there is no opt-in preference selector.
(Correction, 8/2/26: an earlier "notify_preference" field was removed
from the Submit WO form when POC contact was added, in favor of always
notifying by both channels — see notify_work_order_event's docstring.)

Design goal: swapping from the trial Twilio/SendGrid accounts to
production ones — or even swapping providers entirely later — should
only ever mean updating environment variables (or, for a provider
swap, rewriting the two send_* functions below), never touching any
call site elsewhere in the app. Everything reads from env vars; nothing
is hardcoded.

Fire-and-forget by design: a WO submission or status change must never
fail, or even slow down, because a notification send failed or the
provider is slow. Every send happens on a background thread, and every
public function here catches and logs its own errors rather than
raising — callers (crud.py) never need a try/except around these.

Kill switch: NOTIFICATIONS_ENABLED must be exactly "true" (case-
insensitive) or nothing sends, regardless of whether credentials are
configured. Defaults to disabled — a freshly-cloned/staging environment
without this explicitly set should never accidentally text or email
real people.
"""
import logging
import os
import threading

logger = logging.getLogger("notifications")

NOTIFICATIONS_ENABLED = os.environ.get("NOTIFICATIONS_ENABLED", "false").strip().lower() == "true"

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL")
SENDGRID_FROM_NAME = os.environ.get("SENDGRID_FROM_NAME", "JamboWMS")
SENDGRID_REPLY_TO = os.environ.get("SENDGRID_REPLY_TO")


def _sms_configured() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER)


def _email_configured() -> bool:
    return bool(SENDGRID_API_KEY and SENDGRID_FROM_EMAIL)


def send_sms(to_phone: str, body: str) -> bool:
    """Best-effort, synchronous — always called from a background
    thread (see _run_in_background below), never directly from a
    request path. Returns True/False rather than raising."""
    if not NOTIFICATIONS_ENABLED:
        logger.info("notifications disabled; skipping SMS to %s", to_phone)
        return False
    if not to_phone:
        return False
    if not _sms_configured():
        logger.warning("SMS requested but Twilio is not fully configured; skipping")
        return False
    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(to=to_phone, from_=TWILIO_FROM_NUMBER, body=body)
        logger.info("SMS sent to %s", to_phone)
        return True
    except Exception:
        # Trial-account note: Twilio trial numbers can only text phone
        # numbers verified in the Twilio console — an unverified
        # recipient will land here as a caught exception, not a crash.
        logger.exception("Failed to send SMS to %s", to_phone)
        return False


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Same best-effort contract as send_sms."""
    if not NOTIFICATIONS_ENABLED:
        logger.info("notifications disabled; skipping email to %s", to_email)
        return False
    if not to_email:
        return False
    if not _email_configured():
        logger.warning("Email requested but SendGrid is not fully configured; skipping")
        return False
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content

        message = Mail(
            from_email=Email(SENDGRID_FROM_EMAIL, SENDGRID_FROM_NAME),
            to_emails=To(to_email),
            subject=subject,
            html_content=Content("text/html", html_body),
        )
        if SENDGRID_REPLY_TO:
            message.reply_to = Email(SENDGRID_REPLY_TO)
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        sg.send(message)
        logger.info("Email sent to %s", to_email)
        return True
    except Exception:
        # Domain-auth note: SendGrid will reject sends from an unverified
        # sender/domain — that also lands here, not as a crash.
        logger.exception("Failed to send email to %s", to_email)
        return False


def _run_in_background(fn, *args, **kwargs):
    threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True).start()


# ---- Message content ----
# Enhancement backlog Phase 25 (PRD §17#8, "type-specific text message
# content"): starting with one clear, generic template per event rather
# than per-work-type variants — no specific wording was given to build
# against, and a single well-written template beats several vague ones.
# Genuinely easy to branch on wo.work_type here later without touching
# any call site, if/when specific wording is decided.

def _sms_body(event: str, wo_number: str, description: str, status: str) -> str:
    snippet = (description or "").strip()
    if len(snippet) > 80:
        snippet = snippet[:77] + "..."
    if event == "submitted":
        return f'NJ2026 Work Order #{wo_number} received: "{snippet}". We will update you as it progresses.'
    if event == "in_progress":
        return f"NJ2026 Work Order #{wo_number} is now being worked on."
    if event == "closed":
        return f"NJ2026 Work Order #{wo_number} has been closed ({status})."
    return f"NJ2026 Work Order #{wo_number} update: {status}"


def _email_subject(event: str, wo_number: str) -> str:
    if event == "submitted":
        return f"Work Order #{wo_number} received"
    if event == "in_progress":
        return f"Work Order #{wo_number} is being worked on"
    if event == "closed":
        return f"Work Order #{wo_number} closed"
    return f"Work Order #{wo_number} update"


def _email_html(event: str, wo_number: str, description: str, status: str,
                 note_to_requester: str | None) -> str:
    if event == "submitted":
        lead = "has been received. We will text/email you as it progresses."
    elif event == "in_progress":
        lead = "is now being worked on."
    elif event == "closed":
        lead = f"has been closed (<strong>{status}</strong>)."
    else:
        lead = f"status is now <strong>{status}</strong>."
    html = (
        f"<p>Your NJ2026 work order <strong>#{wo_number}</strong> {lead}</p>"
        f"<p><strong>Description:</strong> {description}</p>"
    )
    if note_to_requester:
        html += f"<p><strong>Note:</strong> {note_to_requester}</p>"
    return html


def notify_work_order_event(wo, event: str) -> None:
    """Fire-and-forget entry point, called from crud.py at submission
    and at the Work In Progress / Closed status transitions. Returns
    immediately — the calling request is never delayed or failed by a
    notification issue, even if a provider is slow or down.

    Reads every field it needs off `wo` *before* spawning the
    background thread, rather than passing the ORM object itself
    across threads — touching a SQLAlchemy object from a different
    thread than the one that owns its Session is a real footgun
    (DetachedInstanceError at best, silently stale data at worst), so
    nothing in the background thread ever touches `wo` directly.

    event: 'submitted' | 'in_progress' | 'closed'

    Design correction (8/2/26): there is no opt-in preference selector
    — an earlier "notify_preference" (email/text/both/none) field was
    deliberately removed from the Submit WO form when POC contact was
    added, in favor of always notifying by both SMS and email (per
    explicit decision) whenever the corresponding contact info exists.
    The requester phone/email fields are enforced as required by the
    public submission form itself (app/routers/public.py), but this
    still checks each independently before sending — WOs created
    through other paths (the authenticated LOC/tech endpoint, bulk
    import) aren't guaranteed to have both.
    """
    if not NOTIFICATIONS_ENABLED:
        return
    if event not in ("submitted", "in_progress", "closed"):
        return

    wo_number = wo.wo_number
    description = wo.description
    status = wo.status
    note_to_requester = wo.note_to_requester
    requester_email = wo.requester_email
    requester_phone = wo.requester_phone
    # POC has no email field at all (models.WorkOrder never captured
    # one) — a distinct POC can only ever get texted, never emailed.
    poc_phone = None if wo.poc_is_requester else wo.poc_phone

    def _send():
        body = _sms_body(event, wo_number, description, status)
        if requester_phone:
            send_sms(requester_phone, body)
        if poc_phone:
            send_sms(poc_phone, body)
        if requester_email:
            subject = _email_subject(event, wo_number)
            html = _email_html(event, wo_number, description, status, note_to_requester)
            send_email(requester_email, subject, html)

    _run_in_background(_send)
