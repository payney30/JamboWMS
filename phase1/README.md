# NJ LOC Work Order System — Phase 1

DB + status-history engine + LOC triage view. See `PHASE1_TECH_SPEC.md` for the
full design writeup this code implements.

Tested and working end-to-end (login → create WO → assign → close → dashboard
KPIs, including the "reassignment requires a note" guard).

## Local setup

```bash
pip install -r requirements-dev.txt   # includes requirements.txt + alembic/pytest/httpx

# Point these at your copy of the two hierarchy JSON files
# (already generated in the project from Asset_Hierarchy_Analysis.md)
export NAME_TO_BRANCH_PATH=/path/to/name_to_branch.json
export NAME_TO_CAMP_LETTER_PATH=/path/to/name_to_camp_letter.json

alembic upgrade head
# -> creates wo_system.db (SQLite) with the full schema

python seed.py
# -> loads assets/teams, prints an admin login (email + generated
#    password) — change the password after first login

uvicorn app.main:app --reload
# -> API + triage UI at http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000` in a browser and log in with the admin
credentials seed.py printed. The public requester form (no login) is at
`http://127.0.0.1:8000/request.html` — that's what anyone on site would use
to submit a work order.

If you only need the runtime deps (no dev tooling), `pip install -r
requirements.txt` is still fine — just install `alembic` separately before
running migrations.

## Schema changes: Alembic migrations

Schema is managed by Alembic now, not `Base.metadata.create_all()`. When you
change a model in `app/models.py`:

```bash
alembic revision --autogenerate -m "describe the change"
# review the generated file in alembic/versions/ — autogenerate is good at
# columns/tables but sometimes misses server-side defaults or renames vs
# drop+add; read the diff before trusting it
alembic upgrade head
```

`alembic/env.py` reads `DATABASE_URL` the same way `app/database.py` does, so
migrations run against whichever DB you're pointed at (sqlite for local dev,
postgres for staging/prod) without editing `alembic.ini` per environment.

`tests/test_migrations.py` runs `alembic upgrade head` against a throwaway
DB as part of the test suite, so a migration that doesn't apply cleanly
fails CI instead of failing at deploy time.

## Moving to Postgres (do this before the real event)

```bash
export DATABASE_URL="postgresql+psycopg2://user:pass@host:5432/wo_system"
alembic upgrade head   # creates the full schema via migrations
python seed.py         # seeds reference data
```

(You'll need `psycopg2-binary` installed for the postgres driver — it's not
in requirements.txt since local dev defaults to sqlite.)

This has been validated against a real local Postgres 16 instance, not just
SQLite: `alembic upgrade head` produced all 8 tables with every check
constraint translated correctly to native Postgres `CHECK` clauses,
`seed.py` loaded the same 637 assets / 6 teams, and the full create → assign
→ reroute-guard → close → dashboard-KPI flow behaved identically over HTTP.
The one thing worth knowing: SQLite doesn't enforce foreign keys unless you
turn a pragma on (the test suite does this explicitly in `conftest.py`),
while Postgres always enforces them — so the "failed history write rolls
back the whole mutation" guarantee is actually *more* solid on Postgres
than in local dev, not less.

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

75 tests, covering:
- **`tests/test_status_history_engine.py`** — the highest-risk file. Asserts
  every status/team/priority mutation writes exactly the right
  `wo_status_history` row(s), that reassignment uses `event_type =
  'reassignment'` not `status_change`, that `closed_at` is set only on the
  two closing statuses and cleared on reopen, that the "reassignment
  requires a note" guard actually blocks the write, and — the one that
  matters most — that a failed history write (forced via an FK violation)
  rolls back the work order mutation too, proving they share one
  transaction.
- **`tests/test_dashboard_kpis.py`** — the live queries that replaced
  `dashboard_snapshot_history.json`, checked against a seeded set of WOs
  with known (including backdated) timestamps.
- **`tests/test_work_order_api.py`** — the same flows through actual HTTP
  requests: login, full create → assign → close lifecycle, the
  reassignment-note guard as a client would hit it, role-based access.
- **`tests/test_migrations.py`** — `alembic upgrade head` against a fresh
  DB produces every expected table.
- **`tests/test_login_security.py`** — login lockout after repeated failures,
  that a correct password still works right up to the threshold, that
  lockout is scoped per-email (one account's lockout doesn't block another),
  and that CORS headers are absent by default for a cross-origin request.
- **`tests/test_work_order_listing.py`** — the default inbox sort (highest
  priority first, oldest first within a priority tier) against a deliberately
  scrambled set of work orders.
- **`tests/test_public_request_form.py`** — the requester form end to end:
  submission creates a WO, required-field and priority/work-type validation,
  the honeypot returns a fake success without touching the database, the
  per-IP rate limit trips after the configured max, photo attachments save
  and non-image files are silently skipped, status lookup only returns a
  result when the WO number and email both match (same 404 either way on a
  mismatch, so it can't be used to enumerate WO numbers or emails), and the
  `notify_preference` field: it persists, rejects unrecognized values,
  requires a phone number for `text`/`both` and an email for `email`/`both`,
  and is optional otherwise.
- **`tests/test_backfill_fiix_history.py`** — the historical import: status/
  work-type/priority mapping, the trailing-`(CODE)` asset-name stripping,
  the "Unassigned (Historical Import)" fallback for tickets with no
  resolvable asset, missing teams getting auto-created, `TEST.`-prefixed
  rows getting skipped, idempotent re-runs, WO-number collision safety
  against live-created WOs, and — run against the actual project data, not
  just synthetic fixtures — that every one of the ~623 real historical
  tickets is accounted for (created or explicitly skipped with a reason)
  and ends up with exactly one status-history row.

Each test gets its own throwaway SQLite file (`tmp_path` fixture) with
foreign keys enabled, so tests are fully isolated and can run in parallel.

## What's here

- `app/models.py` — SQLAlchemy models. `response_templates` is still a
  schema stub for a later phase; `wo_attachments` now has real endpoints
  (photos from the public requester form) — see `app/routers/public.py`.
- `app/crud.py` — **this is the important file.** Every mutation to
  status/team/priority writes a `wo_status_history` row in the same
  transaction. If you add a new mutable field later, decide up front whether
  it needs a history row before wiring it into a router.
- `app/routers/` — work orders, dashboard KPIs/breakdowns, auth, reference
  data, and `public.py` (the unauthenticated requester-facing endpoints)
- `app/auth.py` — email/password + JWT, role-gated (`loc`/`admin` for
  mutations, any authenticated role for reads)
- `static/index.html` — single-page LOC triage UI: filter bar (status,
  priority, work type, location, team, search) plus quick-view chips
  (Requested/Assigned/In Progress/On Hold/Highest+High-open/Closed), an inbox
  table sorted highest-priority-first-then-oldest by default (per the PRD),
  and a WO detail drawer with full field editing (description, location,
  work type, priority), status/assign controls, notes, and status history.
  Reuses the color/typography variables from the existing dashboard HTML so
  it doesn't look bolted-on. Handles session expiry, shows inline errors for
  the reassignment-note guard and rate-limited logins, and disables buttons
  mid-save to prevent double-submits.
- `static/request.html` — the public, no-login requester form (PRD 4.1):
  name/contact, a preferred-contact-method picker (email/text/both, with
  client- and server-side validation that you've actually given the
  contact info that preference needs), a location typeahead sourced from
  `/public/assets`, work type, description, a priority picker showing the
  exact criteria from `Work_Order_Priorties.pdf` inline per option (not a
  hover tooltip — doesn't work on mobile), up to 5 optional photos, a
  confirmation screen with the WO number, and an optional status-lookup
  panel (WO number + email, no login). Protected by a honeypot field and a
  per-IP submission rate limit — see `app/routers/public.py` and
  `app/rate_limit.py`.
- `seed.py` — loads assets from the hierarchy JSON, a starter team list, one
  admin user
- `backfill_fiix_history.py` — one-time import of the ~623 historical work
  orders embedded in `NJ_LOC_Work_Order_Dashboard.html`'s `RAW` array (the
  most complete source available — the original pre-dashboard export
  wasn't kept). Creates one `work_orders` row + exactly one synthetic
  `wo_status_history` row per ticket, both dated to the ticket's original
  timestamp, per the tech spec's "acceptable to be lossy here since real
  history didn't exist before." Idempotent — re-running skips anything
  already imported (tracked via the new `external_ref` column, which also
  makes the old Fiix ticket numbers searchable in the LOC triage view).
  Run it after `alembic upgrade head` and `seed.py`:
  ```bash
  python backfill_fiix_history.py
  ```
  Known lossy bits (documented in the script's module docstring): closed
  tickets get `closed_at` set equal to `created_at` since the export has
  no separate close timestamp — daily opened/closed trend counts are
  accurate, per-ticket duration is not. Backfilled rows get
  `requester_name="Historical Fiix Import"` with no contact info, since
  the export never had it.
- `alembic/` — schema migrations (see "Schema changes" below)
- `tests/` — pytest suite (see "Running the tests" below)

## What's NOT here (later phases per the PRD)

- Technician/team self-service queue view (Phase 3) — status changes and
  notes are already possible via API, just no dedicated UI restricted to a
  tech's own team queue yet
- Notifications / SLA alerting (Phase 4) — requesters can now state a
  preferred contact method (`notify_preference` on the WO, captured at
  submission), but no email/SMS provider is wired in yet, so nothing
  actually gets sent on submission, on close, or on escalation. The
  hookup points are called out in a TODO in `app/routers/public.py`:
  right after `crud.create_work_order` for submission, and in the
  status-change endpoint in `app/routers/work_orders.py` for close.
- Response-template suggestions (schema is ready, no endpoints)
- Saved/custom views beyond the fixed filter set, bulk actions,
  duplicate-flagging UI in the LOC triage view (explicitly Phase 1+ in the
  PRD)

## Security hardening

- **CORS** is closed by default (`app/main.py`) — the triage UI is served by
  this same app, so same-origin requests never need CORS headers at all. If
  a separate frontend origin needs to call the API directly, set
  `CORS_ALLOWED_ORIGINS` to a comma-separated list of exact origins, e.g.:
  ```bash
  export CORS_ALLOWED_ORIGINS="https://njloc.example.org,http://localhost:3000"
  ```
  Never set it to `"*"` once real requester data (names/emails/phones) is
  flowing through the API. Auth is via Bearer token, not cookies, so
  `allow_credentials` is off — no credentialed CORS needed.

- **Login lockout / rate limiting** (`app/rate_limit.py`) — after 5 failed
  attempts against one email within 15 minutes, that account is locked out
  for 15 minutes (`429 Too Many Requests` with a `Retry-After` header). A
  second, separate limit caps failed attempts per IP address regardless of
  which email they're aimed at, so someone can't dodge the per-email lockout
  by spraying many different emails from one place. This is in-memory and
  process-local — sized for the single-process deployment this app is built
  for. If this ever moves behind a load balancer with multiple workers,
  swap the module-level state for a shared store (e.g. Redis) so a lockout
  holds no matter which instance handles the next request.

## Known gaps to close before this is production-ready

- ~~CORS wide open~~ — done, see "Security hardening" above
- ~~No rate limiting / login lockout~~ — done, see "Security hardening" above
- ~~No automated tests~~ — done, see "Running the tests" above
- ~~`create_all()` → Alembic migration~~ — done, see "Schema changes" above
