# Public Management Dashboard (Phase 20, §17#15)

Full cumulative state. No new migration this round — new endpoints and
one new static page only.

## What's new this round

**New file:** `static/management-dashboard.html` — no login gate, no
`Authorization` header anywhere in it. Shareable link, viewable by
anyone with the URL.

Shows: KPI tiles (Total/Requested/Active/Closed/Completion Rate,
non-clickable), the requested open-vs-closed trend graph, and status/
priority/work-type/location breakdown bars. Auto-refreshes every 60s.
**No WO-level detail anywhere** — no inbox, no individual WO lookup, no
requester info of any kind.

**New backend endpoints** (`app/routers/public.py`):
- `GET /public/dashboard/kpis`
- `GET /public/dashboard/breakdowns`
- `GET /public/dashboard/trend?days=N` (bounded 1-90)

These reuse the exact same `crud.get_kpis`/`get_breakdowns`/
`get_daily_trend` functions the authenticated `/dashboard/*` router
already uses — none of the three have ever returned WO-level detail, so
the real decision here was "allow no login," not "build different,
more-restricted data." No filter params are exposed on any of the
three — fixed to the overall system view, nothing configurable, on
purpose (every filter could be used to slice toward something more
identifiable than a flat aggregate).

**Rate-limited per-IP**, same limiter the phone-based public status
lookup already uses — a public no-auth GET endpoint is exactly the kind
of thing that can get scraped.

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
    #   tests/test_enhancement_phase20.py
    alembic upgrade head

No new migration this round specifically.

## Verify after deploying

- Open `management-dashboard.html` in a private/incognito window (no
  logged-in session) — it should load and show data with zero sign-in.
- Confirm there's genuinely no way to see an individual WO, requester
  name, or description anywhere on the page.
- Hit `/public/dashboard/kpis` rapidly (or check the rate-limit test) to
  confirm the 429 response kicks in as expected.

## Test status

**257 passing, 0 failing** — fully green. Added 6 new tests: no-auth
access for all three endpoints, the `days` bound clamping both
directions, a check that KPIOut never contains WO-shaped keys, and
confirmation the rate limiter actually trips.

## What's left in §17

§17#1-4 and #10 (Fulfillment Worker / assignment hierarchy), #5 (SKU
matching, blocked on you providing the catalog), #6-8 (texting, blocked
on a provider decision), #9 (BOM import, blocked on the BOM data),
#16 (inactionable/cancel analysis — cheap, still open). Plus trivial PRD
housekeeping (§13#6, §14#22/#23).
