# SMS Phone Format Fix (E.164) - Notifications, continued

Full cumulative state. No new migration this round. This includes the
earlier "no preference selector" correction plus this new fix - if
you already deployed that one, this package is still safe to apply.

## Root cause of "email works, SMS doesn't"

Confirmed your hypothesis exactly. Every phone number in this app is
stored and displayed in the app's own format - "xxx-xxx-xxxx" (see
request.html's formatPhoneAsTyped) - never in the E.164 format
("+15551234567") Twilio's API requires for the "To" number.

send_sms was passing the raw stored string straight through to
Twilio. Twilio silently rejected every send as an API error - caught
by the existing try/except (so nothing crashed), just never
delivered. Email worked because SendGrid doesn't have this formatting
requirement.

## The fix

New _to_e164 helper in app/notifications.py, called before every
Twilio send. Normalizes:
- Bare 10-digit numbers (5550199000)
- Dashes/parens/spaces (555-019-9000, (555) 019-9000)
- 11-digit with leading country code (15550199000)
- Already-E.164 (+15550199000)

All become +15550199000. Scoped to US/Canada only, matching this
app's single-event scope.

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

No new migration. No env var changes needed.

## Verify after deploying

1. Submit a test WO with your Twilio-verified phone number.
2. Confirm you now receive the SMS (email should already have been
   working).
3. Check Render's logs around the submission time - you should see
   "SMS sent to +1..." rather than a caught exception.

## Test status

**314 passing, 0 failing** - fully green. Added dedicated tests for
_to_e164 covering all the input formats above, plus updated the
existing Twilio-call test to confirm the actual API call receives the
converted E.164 number, not the raw stored format.
