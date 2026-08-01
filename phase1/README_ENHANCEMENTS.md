# Program HQ / Contingent Ops HQ Dashboards (Phase 19, §17#14)

Full cumulative state. No new migration this round — only application
code changed (backend query logic + the two dashboard pages).

## What's new this round

Built out `dashboard-program.html` and `dashboard-basecamp.html` per
the finalized spec (PRD §17#14):

- **4 clickable KPI tiles** — Total WO, Requested, Active, Closed —
  replacing the richer LOC-triage-style tile row. Clicking one filters
  everything on the page consistently (KPIs, breakdowns, trend, and the
  inbox), not just the table underneath.
- **Full sortable read-only inbox**, replacing the old top-15-urgent
  "Needing attention" table. Click any column header to sort. No row is
  clickable to an edit view.
- **No aging/SLA info** — the old "Age" column is gone; nothing on this
  page computes or shows elapsed time or deadline status.
- **Location search upgraded** to the full hierarchical LocationPicker
  (same component LOC triage and the technician queue use), replacing
  the old broad location_group dropdown.

**Backend:** the inbox reuses `GET /work-orders` directly — no new
endpoint. `crud._apply_filters` (backing all `/dashboard/*` endpoints)
gained `exclude_closed`, `closed_only`, and `asset_id` support, which is
what makes the clickable tiles and location picker actually filter
everything consistently.

`dashboard-basecamp.html` was rebuilt directly from the transformed
`dashboard-program.html` (they're near-identical apart from 5
scope-specific strings) to guarantee the two stay in lockstep.

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

No new migration for this round specifically — the `alembic upgrade
head` above is only needed if you're catching up on migrations from
earlier rounds (locking, app_settings, priority constraint, pin-drop,
priority data conversion).

## Test status

**249 passing, 0 failing** — fully green. Added 5 new tests covering
the new filter capabilities (`exclude_closed`, `closed_only`,
`asset_id`) on the dashboard endpoints, plus confirmed the inbox
(`GET /work-orders?location_group=...`) returns the same result set as
the matching dashboard scope.

## What's NOT in this package

§17#15 — the separate public, no-sign-in-required management dashboard
(graphs/trends only, open/close graph). That's a bigger, separate piece
of work — new unauthenticated backend route(s) and a real decision
about what's safe to expose with zero auth. Not started yet.
