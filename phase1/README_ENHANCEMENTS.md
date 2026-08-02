# Admin "New User" Password Text Fix

Full cumulative state. Only `static/admin.html` changed this round — no
backend/schema changes, no new migration.

## What changed

Confirmed there's genuinely no self-service password change anywhere in
the system (checked directly — no endpoint, no UI, for any role), and
that this is fine for a 2-week event: admin-reset already provides a
complete recovery path, and it's consistent with the same
externally-managed-credential approach already used for Task Workers
(PIN-based). Logged as a deliberate decision in the PRD (§8), not a gap.

Given that, the admin "New user" screen's text was actively misleading
— it called the generated password "temporary" and said self-service
change "isn't built yet," both implying a forced-change flow was coming
that isn't. Updated both places:

- The new-user form's hint text
- The password-reveal screen's warning banner

Both now describe the password as the account's actual, real credential
(not a placeholder) and point back to the reset flow as the intended way
to change it later, rather than implying something's still missing.

**Not changed:** the underlying API field name (`temporary_password` in
`PasswordResetResponse`/`UserCreateResponse`) — that's an internal
contract, not user-facing text, and renaming it wasn't part of what was
asked.

## How to apply

    cd JamboWMS/phase1
    git apply /path/to/CHANGES.diff

Only `static/admin.html` actually needs to change for this specific fix
— the rest of the diff is cumulative from earlier rounds you likely
already have.

## Test status

**282 passing, 0 failing** — unchanged, since this was a text-only
change.
