# SMS + Email Notifications (Phase 25)

Full cumulative state. No new migration this round.

## What this is

Submission / Work In Progress / Closed notifications via Twilio (SMS)
and SendGrid (email), to the requester and, if a distinct one exists,
the POC. Worker-assignment notification (the 4th trigger discussed
earlier) was explicitly descoped, not deferred - internal to the team,
the requester doesn't need it.

## New dependencies

    twilio==9.10.9
    sendgrid==6.12.5

Added to requirements.txt - Render will install these automatically on
next deploy.

## New environment variables (see render.yaml)

**Secrets - set these in Render's dashboard (Environment tab), NOT in
render.yaml:**
- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN
- TWILIO_FROM_NUMBER
- SENDGRID_API_KEY

**Not secrets - already filled in in render.yaml:**
- SENDGRID_FROM_EMAIL = graeme@cybersecurity4executives.com
- SENDGRID_FROM_NAME = JamboWMS
- SENDGRID_REPLY_TO = marketing@cybersecurity4executives.com

**Kill switch - defaults to disabled:**
- NOTIFICATIONS_ENABLED = "false" (set to "true" in Render's dashboard
  once ready to actually send real messages)

## Built for easy swap-out later

Everything reads from environment variables at call time - nothing
hardcoded or cached at import. Switching from the trial Twilio/SendGrid
accounts to production ones later is purely an env var update in
Render's dashboard, no code change or redeploy needed. A full provider
swap (different SMS/email vendor entirely) would only mean rewriting
the two send functions in app/notifications.py - every call site
elsewhere in the app stays untouched either way.

## How it works

- **Fire-and-forget**: every send happens on a background thread and
  never raises - a WO submission or status change is never delayed or
  failed by a slow or down notification provider.
- **Trigger points**: work order submission, status change to Work In
  Progress, status change to Closed (either sub-status), including the
  worker's "Completed" button path.
- **Respects notify_preference**: only sends if the requester opted in
  (email / text / both) at submission - no preference on file means no
  notification.
- **POC gets texted too** (if distinct from the requester and a phone
  number was given) - but never emailed, since the data model has no
  POC email field at all.
- **Guarded against duplicate sends**: re-saving the same status, or a
  worker double-tapping "Mark completed," won't re-send a notification
  that already went out.

## SendGrid setup note

Domain authentication for cybersecurity4executives.com hit a DNS
mismatch during setup (a stale CNAME record in Squarespace from an
earlier authentication attempt). Single Sender Verification was used
in the meantime so this build wasn't blocked on DNS propagation -
worth finishing domain authentication when you have a few minutes, but
not required for sending to work.

## How to apply

    cd JamboWMS/phase1
    git apply /path/to/CHANGES.diff
    # (only add new files below if you haven't already from a prior round)
    #   alembic/versions/b7f3d1a9c2e4_add_locking_and_note_to_requester.py
    #   alembic/versions/c8e2f4a1b6d3_add_app_settings_table.py
    #   alembic/versions/d3f8a2c1e5b7_widen_priority_check_constraint.py
    #   alembic/versions/e7c4b9d2a1f6_add_geo_pin_drop.py
    #   alembic/versions/f4a8d1c6e3b2_convert_priority_data_narrow_constraint.py
    #   alembic/versions/a1b2c3d4e5f6_add_task_worker_role_and_assignment.py
    #   alembic/versions/b3c5d7e9f1a2_add_tasking_event_type.py
    #   tests/test_enhancement_phase1.py
    #   tests/test_enhancement_phase4.py
    #   tests/test_enhancement_phase5.py
    #   tests/test_enhancement_phase12.py
    #   tests/test_enhancement_phase15.py
    #   tests/test_enhancement_phase20.py
    #   tests/test_enhancement_phase21.py
    #   tests/test_enhancement_phase25.py
    alembic upgrade head

No new migration this round.

**After deploying, set the 4 secret env vars in Render's dashboard**
(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER,
SENDGRID_API_KEY) - the app will run fine without them (notifications
just silently no-op), but nothing will actually send until they're set
AND NOTIFICATIONS_ENABLED is flipped to "true".

## Verify after deploying and enabling

1. Set NOTIFICATIONS_ENABLED=true and the 4 secrets in Render.
2. Submit a test WO with your own (Twilio-trial-verified) phone number
   and notify_preference set to "both" - confirm you get both a text
   and an email.
3. Move it to Work In Progress - confirm a second notification.
4. Close it - confirm a third.
5. Try a WO with a distinct POC phone number - confirm the POC also
   gets texted (never emailed).
6. Try re-saving the same status without changing it - confirm NO
   duplicate notification.

Remember: on a Twilio trial account, SMS only works to phone numbers
you've manually verified in the Twilio console first.

## Test status

**313 passing, 0 failing** - fully green. Added 21 new tests, all
mocked (no real Twilio/SendGrid calls needed) - provider-call
correctness, the kill switch, the email/text/both decision logic, the
POC-texted-never-emailed behavior, and that every actual crud.py
trigger point fires (or correctly doesn't, for no-op transitions).
