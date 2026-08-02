# Notifications, Corrected: No Preference Selector (Phase 25, corrected)

Full cumulative state. No new migration this round. This supersedes
the notifications package from earlier today - do not deploy that one.

## What happened

The original build gated notifications on a "notify_preference" field
and, when testing showed nothing was sending, I added a checkbox UI to
the Submit WO form to let the requester choose email/text/both. That
was wrong - notify_preference was deliberately removed from the form
when POC contact was added, in favor of anchoring on the requester's
phone number (required) as the single notification identifier, with
email captured for the requester only. The checkbox UI has been fully
reverted.

## The actual root cause of "no SMS on submit"

notify_preference was never set by the real form (correctly, since it
shouldn't exist there) - and the original notification logic required
it to be set before sending anything. So notifications were silently
skipping every real submission, with everything else (Twilio config,
NOTIFICATIONS_ENABLED) completely correct.

## Corrected design

Per explicit decision: notifications now go out automatically by both
SMS and email, for every requester, whenever the corresponding contact
field exists on the WO - no selector, nothing to opt into.
app/notifications.py's notify_work_order_event no longer checks
notify_preference at all.

- Requester gets SMS (if phone present) and email (if email present).
- POC gets SMS too, if distinct from the requester and a phone number
  was given - POC can never be emailed (no POC email field exists in
  the data model at all).

## PRD updated

Added a prominent standing note at the top of Section 13 (Submit Order
Screen backlog) documenting this decision explicitly, so it doesn't
get reintroduced by accident in a future session: no preference
selector belongs on this form, phone is the anchor, and this was tried
once (the same day it was removed) and reverted before ever deploying.

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

No new migration this round. Your existing NOTIFICATIONS_ENABLED and
Twilio/SendGrid environment variables in Render don't need any changes
- only the application code and PRD changed.

## Verify after deploying

1. Confirm the Submit WO form has no notification-preference checkboxes
   or selector of any kind (should look exactly as it did before any of
   today's notification testing).
2. Submit a test WO with your Twilio-verified phone number and a real
   email address - confirm you get BOTH a text and an email, with no
   preference selection needed anywhere in the form.
3. Move it to Work In Progress, then close it - confirm a notification
   each time.
4. Test with a distinct POC phone number - confirm the POC also gets
   texted.

## Test status

**312 passing, 0 failing** - fully green. Updated the notification
test file to match the corrected always-on behavior (removed the
preference-based tests, added tests confirming both channels fire
automatically, and that each channel independently no-ops when its
corresponding contact field is missing).
