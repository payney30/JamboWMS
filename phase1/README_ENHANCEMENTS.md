# Dashboard Bug Fixes (Phase 19 follow-up) — 3 real issues fixed

Full cumulative state. No new migration this round.

## 1. Base Camp Ops dashboard showed no data — real bug, now fixed

The location-scope fix from the previous round used the string
`"Base Camp Ops"` — which doesn't exist anywhere in the real data. That
was descriptive shorthand from a `seed.py` *comment*, not an actual
branch label. The real value (confirmed against `data/name_to_branch.json`,
the authoritative file `seed.py` itself reads from) is `"Base Camps"`
(plural, no "Ops").

Fixed in `crud.py`, `dashboard-basecamp.html`, and every test fixture
that had the same wrong assumption baked in. **Added two regression
tests** that load the real `name_to_branch.json` and would fail loudly
if this class of bug ever recurs (a hardcoded reporting-group string
that doesn't match anything in the real data).

## 2. KPI tiles were changing value on every filter — wrong, now fixed

The 4 tiles (Total/Requested/Active/Closed) were using the same
filter-bar state as the inbox — so applying any filter silently
redefined what "Total" meant. Fixed: the tiles now always show the true
totals for the whole reporting group, completely decoupled from the
filter bar and quick-view state. Clicking a tile still filters the
inbox (and the breakdowns/trend below it, for consistency) — it just
never changes the tile numbers themselves anymore.

## 3. Clicking an inbox row did nothing — real gap, now fixed

"Everything view-only" had been over-applied to mean "rows aren't
clickable at all." Added a read-only detail slide-out — full WO
information (location, description, requester/POC, assigned team,
attachments, pinned-location map if present, notes, status history) —
with no inputs and no Save button anywhere in it, matching the actual
intent of "no editing."

## How to apply

    cd JamboWMS/phase1
    git apply /path/to/CHANGES.diff
    # (only add new files below if you haven't already from a prior round)
    #   alembic/versions/b7f3d1a9c2e4_add_locking_and_note_to_requester.py
    #   alembic/versions/c8e2f4a1b6d3_add_app_settings_table.py
    #   alembic/versions/d3f8a2c1e5b7_widen_priority_check_constraint.py
    #   alembic/versions/e7c4b9d2a1f6_add_geo_pin_drop.py
    #   alembic/versions/f4a8d1c6e3b2_convert_priority_data_narrow_constraint.py
    #   tests/test_enhancement_phase1.py
    #   tests/test_enhancement_phase4.py
    #   tests/test_enhancement_phase5.py
    #   tests/test_enhancement_phase12.py
    #   tests/test_enhancement_phase15.py
    alembic upgrade head

No new migration for this round specifically.

## Verify after deploying

1. Open the Base Camp Ops dashboard — it should now show data (KPI
   tiles, breakdowns, inbox all populated).
2. Apply any filter (status/priority/etc.) — the 4 top tiles should
   stay exactly the same; only the inbox/breakdowns/trend below should
   change.
3. Click a tile — the inbox should filter to that bucket; the tiles
   themselves still shouldn't change.
4. Click any row in the inbox — a read-only detail panel should slide
   out from the right.

## Test status

**251 passing, 0 failing** — fully green. Added 2 regression tests
specifically targeting the reporting-group-string bug class.
