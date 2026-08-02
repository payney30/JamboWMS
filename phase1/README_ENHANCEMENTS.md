# Task Team Assignment (Phase 21, §17#10) — the big one

Full cumulative state. New migration this round.

## What this is

The largest single feature in this whole backlog. A new lightweight
role — Task Worker — sitting below the existing team level:
Dispatchers (team leads) can now assign a specific work order to a
specific worker on their team, and that worker gets their own simple,
PIN-login queue with a one-tap Completed action.

Full spec and build write-up are in the PRD under §17 item #10 — this
README is a shorter practical summary.

## New migration

- `users.pin_hash` (nullable) + widened role constraint (adds
  `task_worker`)
- `work_orders.completion_latitude`/`completion_longitude` — the
  worker's own optional "here's where I actually dropped it" pin,
  separate from the requester's submission pin
- `wo_attachments.stage` — distinguishes a worker's completion photo
  from the requester's original submission photo(s)

**Did NOT need a new "assigned worker" column** — found during
implementation that `WorkOrder.assigned_person_id` (and the validation
that a person must belong to the team they're assigned into) already
existed in the backend, just never wired to any frontend.

## New surface area

- **`app/routers/task_workers.py`** — delegated worker management
  (`/my-team/workers`), scoped entirely to the calling Dispatcher's own
  team, no Admin involvement needed
- **New public endpoints** (`/public/worker-login/*`) — the PIN login
  flow, rate-limited the same way the phone-based status lookup is
- **New `static/worker.html`** — the whole worker-facing app: PIN
  login, own queue, WO detail (read-only), and the Completed action
  (optional note/photo/completion pin)
- **Dispatcher Console additions** (`technician.html`) — a "My
  Workers" panel and an "Assign to worker" dropdown in the WO detail
  drawer
- **QR code on printed WOs** (both LOC triage and Dispatcher Console) —
  scans to the pinned location, shown only when a WO has one

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
    #   tests/test_enhancement_phase1.py
    #   tests/test_enhancement_phase4.py
    #   tests/test_enhancement_phase5.py
    #   tests/test_enhancement_phase12.py
    #   tests/test_enhancement_phase15.py
    #   tests/test_enhancement_phase20.py
    #   tests/test_enhancement_phase21.py
    alembic upgrade head

## Verify after deploying

1. **As a Dispatcher** (log into `technician.html` as a `tech` user):
   open "My Workers," add a worker, confirm the PIN shows once in a
   blocking alert. Open a WO, assign it to that worker, save.
2. **As the worker**: go to `worker.html`, pick your team, pick your
   name, enter the PIN, confirm the assigned WO shows in your queue.
   Open it, confirm you can see the description/location/any submitted
   photos, mark it completed with an optional note/photo/pin.
3. Confirm the worker **cannot** see any other WO — try navigating
   directly to a different WO's URL/ID and confirm it's rejected.
4. Print a WO that has a pin (from either LOC triage or Dispatcher
   Console) and confirm a scannable QR code appears on the printout.

## Test status

**279 passing, 0 failing** — fully green. 22 new tests specifically for
this feature, including several negative/security cases (wrong team,
wrong PIN, deactivated worker, cross-role access attempts).

## What's still open in §17

Texting (§6-8, blocked on a provider decision — this is what would
eventually notify a worker of a new assignment), SKU matching (§5,
blocked on you providing the catalog), BOM import (§9, blocked on the
BOM data, and now dependent on this feature per the revised scope), the
inactionable/cancel analysis (§16), and the two dashboards' visual
alignment with the original 2026 dashboard (§17 item 17, cosmetic-only,
not scoped yet).
