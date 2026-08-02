# Dispatcher Console Testing Round + CSV Export Fields (Phase 24)

Full cumulative state. No new migration this round — pure bug fixes,
schema field additions (existing tables, no new columns), and frontend
changes.

## Real bugs found and fixed

1. **Tasked worker never showed on queue cards.** assigned_person had
   only ever been added to WorkOrderDetail, never to
   WorkOrderListItem (what the queue/list endpoint actually returns).
   The frontend code was correct all along - the data just never
   arrived. Fixed by moving the field onto the shared base schema.

2. **No auto-refresh at all on Dispatcher Console.** Confirmed by direct
   inspection - every other screen had one, this didn't. A saved change
   only appeared after a manual reload. Added (30s, paused while the
   drawer's open, same as everywhere else).

3. **Status history didn't show who made a change**, even though LOC
   triage's did. Confirmed by comparing the two templates directly -
   simply missing changed_by_name. Fixed to match.

4. **Print output had orphaned form-field labels.** The print CSS only
   hid the actual form controls, not their label text - "Status
   Note (required when closing)," "Reassign - wrong team?," etc. all
   printed with nothing underneath. LOC triage already had this right;
   Dispatcher Console's narrower rule didn't. Fixed to match - per
   request, these sections (Status Note, Reassign, Task to worker, Add
   a work note) are now gone from print entirely; Notes, History, and
   everything else stays. Added the assigned worker's name to the print
   summary as data (was missing entirely before).

5. **Stat tiles (New/In progress/On hold/High Urgency) shrank with any
   filter applied.** They were computed from the already-filtered queue
   list. Fixed by fetching a separate, filter-independent team-wide
   dataset just for the tiles - same principle already used for the HQ
   dashboards' KPI tiles. Renamed "High priority open" -> "High Urgency."

## Enhancements added

- **PDF filename** - both LOC triage and Dispatcher Console now set
  document.title to "NJ WO Details #[WO number]" before printing, so
  browsers suggest that as the Save-as-PDF filename.
- **"Clear filters" button** on Dispatcher Console (every other
  filterable screen already had one).
- **"Filter by assigned worker" dropdown**, including a proper
  "Unassigned" option - added a real unassigned_person backend filter
  rather than an unreliable sentinel value.
- **CSV export fields**, both LOC triage and Dispatcher Console: Closed
  At, Assigned Worker, Requester Name/Phone, POC Name/Phone (blank when
  the requester is their own POC). Required adding these fields to the
  list endpoint's schema (WorkOrderListItem) - they only existed on
  the single-WO detail view before.

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

No new migration this round specifically.

## Verify after deploying

1. Task a worker on Dispatcher Console - confirm the queue card
   immediately shows "-> Worker Name."
2. Make a change without touching the page for 30+ seconds - confirm
   the queue refreshes on its own.
3. Check a WO's status history on Dispatcher Console - confirm each
   entry shows who made the change.
4. Print a WO with an assigned worker - confirm the Status Note/
   Reassign/Task-to-worker/Add-a-work-note sections are gone, the
   worker's name appears in the summary, and the suggested PDF filename
   is "NJ WO Details #[number]".
5. Apply a status filter on Dispatcher Console - confirm the stat tiles
   at the top do NOT change.
6. Try the new "Clear filters" button and the "filter by assigned
   worker" dropdown (including "Unassigned").
7. Export CSV from both LOC triage and Dispatcher Console - confirm the
   new columns (Closed At, Assigned Worker, Requester Phone, POC
   Name/Phone) are present and correct.

## Test status

**289 passing, 0 failing** - fully green. Added 4 new tests covering
the new list-endpoint fields and the unassigned_person filter.
