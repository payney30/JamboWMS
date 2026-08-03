Viewer Roles + Main LOC Dashboard Parity (Phase 26)

Full cumulative state. New migration this round.

WHAT'S NEW

1. Two new roles: program_viewer and basecamp_viewer

Audience-scoped, read-only dashboard access. A program_viewer can log
into ONLY the Program Team dashboard; a basecamp_viewer only Base Camp
Ops. Admin-managed (create them from the Admin screen, same as
tech/leadership) - not delegated like Task Workers were.

Security note worth reading: this is enforced server-side, not just
hidden in the UI. Auditing every mutating WO endpoint found two
(change status, add note) that were only gated by "any authenticated
user" - meaning a naive read-only role could have changed a WO's
status via a direct API call with zero UI exposing that ability. Fixed
explicitly. Every WO list/detail endpoint and all three dashboard data
endpoints also force the caller's assigned reporting group server-side
- a program_viewer literally cannot widen their own view by editing
query parameters, even on purpose.

2. Main LOC dashboard (dashboard-main.html) brought up to parity

It had been left behind at an older version - a simple top-15 list,
no read-only detail view. Rebuilt from the same current template
Program/Basecamp use, scoped to see every reporting group (no
restriction). Gets the same decoupled KPI tiles, full sortable inbox,
and read-only row-click detail view those two already had.

3. LOC Triage now links directly to the Main LOC dashboard

A "View Dashboard" link in the LOC Triage header.

HOW TO APPLY

    cd JamboWMS/phase1
    git apply /path/to/CHANGES.diff
    (only add new files below if you haven't already from a prior round)
      alembic/versions/b7f3d1a9c2e4_add_locking_and_note_to_requester.py
      alembic/versions/c8e2f4a1b6d3_add_app_settings_table.py
      alembic/versions/d3f8a2c1e5b7_widen_priority_check_constraint.py
      alembic/versions/e7c4b9d2a1f6_add_geo_pin_drop.py
      alembic/versions/f4a8d1c6e3b2_convert_priority_data_narrow_constraint.py
      alembic/versions/a1b2c3d4e5f6_add_task_worker_role_and_assignment.py
      alembic/versions/b3c5d7e9f1a2_add_tasking_event_type.py
      alembic/versions/c4d6e8f0a2b4_add_viewer_roles.py
      tests/test_enhancement_phase1.py
      tests/test_enhancement_phase4.py
      tests/test_enhancement_phase5.py
      tests/test_enhancement_phase12.py
      tests/test_enhancement_phase15.py
      tests/test_enhancement_phase20.py
      tests/test_enhancement_phase21.py
      tests/test_enhancement_phase25.py
      tests/test_enhancement_phase26.py
    alembic upgrade head

New migration this round - widens the user role constraint to include
the two new roles.

VERIFY AFTER DEPLOYING

1. As an admin, create a user with role "Program Viewer" - confirm it
   only appears as an option for admin accounts, not LOC accounts.
2. Log in as that user at dashboard-program.html - confirm it works
   and shows only Program Areas data.
3. Try logging into dashboard-basecamp.html or LOC Triage with that
   same account - confirm it's rejected with a clear message.
4. Try widening the view via the browser's URL/query string directly
   (e.g. adding a different location_group param to a work-orders
   request) while logged in as that viewer - confirm the data returned
   still only reflects Program Areas.
5. Log in to dashboard-main.html as an LOC or admin account - confirm
   it now shows the same KPI tiles, sortable inbox, and read-only
   detail view Program/Basecamp already have, covering ALL work
   orders regardless of reporting group.
6. From LOC Triage, click "View Dashboard" - confirm it opens the Main
   LOC dashboard.

TEST STATUS

327 passing, 0 failing - fully green. Added 13 new tests specifically
targeting the security boundaries: scope-widening attempts via query
params, cross-scope direct WO-id access attempts, mutations attempted
even on in-scope work orders, and confirming the two new roles stay
independent of each other.
