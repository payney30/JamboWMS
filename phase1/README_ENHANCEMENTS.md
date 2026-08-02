# LOC Triage End-to-End Testing Round (Phase 23)

Full cumulative state. No new migration this round — pure bug fixes and
frontend/label changes.

## Bug found: "Urgent Open" tile completely broken (real regression)

Clicking it always returned zero results. The frontend still sent the
pre-rename priority names (priority_in=Highest,High) — the priority
data migration converted everything to the new names weeks ago, but
this one hardcoded frontend string was never updated in sync. **This is
the most likely explanation for "tiles not filtering correctly."**
Fixed to Immediate,Same Day.

## Bug found: "Open/Active" tile didn't match the precise spec given

Was computed as "everything not closed," which silently included
"Requested" (not-yet-triaged WOs). Per the exact spec given during this
testing round:

- **Total** = all statuses
- **Requested** = submitted, not yet assigned/hold/in-process/complete
- **Open/Active** = specifically Assigned, On Hold, or Work In Progress
- **Urgent Open** = Immediate/Same Day priority, still open
- **Closed** = both Closed sub-statuses
- **Opened/Closed Today**, **Approaching/Past Deadline** — checked
  against spec, already correct, no change needed

Fixed get_kpis' open computation to the exact 3-status list, and
added a new status_in filter (mirrors the existing priority_in
pattern) so clicking the tile filters the inbox to the same 3 statuses
it counts.

**Verified against real historical data** via cutover_check.py: old
(buggy) value was 177, new (correct) value 170 — the 7-WO gap exactly
equals the Requested count, confirming this is the fix working as
intended, not a new bug. cutover_check.py updated to document this as
a deliberate difference from the old dashboard, same treatment as the
pre-existing "Closed Today" exclusion.

## Bug found: location picker didn't reset properly

After submitting a WO and the form resetting, the location picker
showed stale leftover search text (and possibly stale results/tree
content) instead of a clean search view. Fixed in the shared
location-picker.js component — applies everywhere it's used (Submit
WO, LOC triage, Dispatcher Console, both HQ dashboards), not just where
it was found.

## Investigated, no bug found

**"Handled By" dropdown showing only "Anyone."** Checked the role
gating (GET /users already allows both admin and loc) and the
frontend population logic end-to-end — both correct. Most likely the
test account's environment just had no other users yet. Worth
confirming on your end.

## Other fixes/changes this round

- **Inbox pagination raised** (200->2000, backend cap 500->2000) — best
  available explanation for "inbox didn't update for new WOs," though
  not confirmed as the sole cause.
- Renamed "Priority" -> "Urgency" (column header + filter label).
- Removed the redundant "Highest + High, open" quick-view chip (the
  Urgent Open KPI tile already does the same thing).
- Added the signed-in user's name/role to the LOC triage header.

## Related, NOT fixed this round (flagged for later)

The Program HQ/Contingent Ops HQ dashboards' "Active" tile (§17#14)
likely has the same "not closed vs. correct 3 statuses" bug — it was
built with the same pattern before this fix existed. Kept out of scope
for this round since it was specifically about LOC triage.

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

1. Click the "Urgent Open" tile — confirm it returns results (not zero).
2. Click "Open/Active" — confirm a brand-new "Requested" WO does NOT
   appear in the filtered results.
3. Compare the "Open/Active" tile's number against a manual count of
   Assigned + On Hold + Work In Progress WOs.
4. On Submit WO, select a location, submit, and confirm the location
   picker shows a clean search view (no stale text) if you submit
   another request.
5. Confirm the inbox column header now reads "Urgency," and the
   "Highest + High, open" chip is gone from the quick-view row.
6. Confirm your name and role show in the LOC triage header.

## Test status

**285 passing, 0 failing** — fully green, including the real historical
data cutover-validation test. Added 4 new tests directly covering the
status_in filter and the Open/Active semantics fix.
