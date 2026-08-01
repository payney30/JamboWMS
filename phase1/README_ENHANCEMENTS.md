# Priority Rename Follow-Up Cleanup (Phase 18) — IMPORTANT: run the migration

Full cumulative state. New migration this round — this one changes
existing data, not just schema, so read this before deploying.

## What this does

Reverses the original "don't touch historic data" decision from the
urgency-tier rename (§13#15) — confirmed all 2026 data (including the
Fiix backfill import) is test data, not real history worth preserving
under the old labels. Per your direction: converts **all** work_orders
rows regardless of origin, and the Fiix backfill is confirmed done for
good.

**New migration (`f4a8d1c6e3b2`):**
1. Converts every `work_orders.priority` value old→new (same 1:1
   mapping as the original rename).
2. Converts `wo_status_history`'s `priority_change` audit rows the same
   way, so the audit trail stays consistent.
3. Narrows `ck_wo_priority` back down to the 5 new names only (data
   conversion happens first — narrowing the constraint before the data
   is converted would fail the migration outright, same lesson as the
   original widen migration in reverse).

**Code simplified back to a single naming system** — `SLA_HOURS`,
`_PRIORITY_RANK`, `URGENT_PRIORITIES`, the LOC triage priority
dropdown's "(legacy)" fallback, CSS chip rules, and the dashboards'
breakdown-chart order array all collapsed back down from
old+new-supporting to new-only. `schemas.LEGACY_PRIORITIES` removed
entirely.

## Two more bugs found while cleaning up

1. **`backfill_fiix_history.py`** stored the raw old-style priority
   value with no translation — harmless while the DB accepted both old
   and new names, but would have thrown a CHECK-constraint error if this
   "done for good" script were ever run again. Fixed to translate
   old→new before storing.
2. **`cutover_check.py`**'s priority breakdown comparison never
   normalized labels the way its status/work_type comparisons already
   did for the same reason (the backfill transforms values) — so it
   started reporting false mismatches (same counts, different current
   labels) the moment the backfill began translating old→new. Fixed
   using the same normalization pattern already in place for
   status/work_type.

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

**This migration changes existing data, not just schema** — recommend a
DB backup/snapshot before running it, standard practice for any data
migration, even a well-tested one.

## Verify after deploying

Check a WO created before this deploy — its priority should now show
one of the 5 new names (Immediate/Same Day/Next Day/2 Days/3 Days), not
an old one. Check its status history — any `priority_change` entries
should show new names on both sides too.

## Test status

**244 passing, 0 failing** — fully green. Removed one test that
verified a backward-compatibility guarantee this cleanup deliberately
eliminated; added two new ones (the DB now genuinely rejects an
old-style value even bypassing the API, and all 5 new names still work).
