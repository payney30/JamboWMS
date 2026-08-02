# Task Worker "Reset PIN" (Phase 24 follow-up)

Full cumulative state. No new migration this round.

## What's new

Closes a gap explicitly flagged when Task Worker creation first shipped
- a Dispatcher had no way to get a new PIN for a worker who forgot
theirs, short of deactivating and re-creating the account entirely.

**New endpoint:** POST /my-team/workers/{id}/reset-pin (tech-only, own
team scoped - same 404-not-403 pattern as the other worker-management
endpoints, so probing another team's worker ID doesn't confirm it's
real).

**New UI:** a "Reset PIN" button next to each worker in the My Workers
panel. Confirms first (the old PIN stops working immediately), then
shows the new PIN once in a blocking alert - same one-time-reveal
pattern as worker creation.

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
    alembic upgrade head

No new migration this round.

## Verify after deploying

1. In My Workers, click "Reset PIN" for a worker - confirm a
   confirmation prompt appears, then the new PIN shows once.
2. Confirm the worker's OLD PIN no longer logs them in.
3. Confirm the NEW PIN does log them in.

## Test status

**292 passing, 0 failing** - fully green. Added 3 new tests: successful
reset (old PIN invalidated, new PIN works), a Dispatcher can't reset
another team's worker's PIN, and LOC can't call this endpoint at all
(tech-only).
