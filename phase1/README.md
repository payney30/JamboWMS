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

`psycopg2-binary` is in `requirements.txt` now — it used to be left out
since local dev defaults to SQLite, but it's needed the moment `DATABASE_URL`
points at Postgres, which is exactly the point of deploying this for testing.

`app/database.py` and `alembic/env.py` both normalize a `postgres://` URL
(what most managed-Postgres platforms hand back) to the
`postgresql+psycopg2://` scheme SQLAlchemy actually needs — you can paste a
platform-provided `DATABASE_URL` in as-is without editing it.

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

## Deploying for testing/validation

Reference data (`data/name_to_branch.json`, `data/name_to_camp_letter.json`,
`data/NJ_LOC_Work_Order_Dashboard.html`) is bundled in the repo now, so
`seed.py` and `backfill_fiix_history.py` work out of the box with no env
vars — no more pointing at `/mnt/project` paths that only existed in one
sandbox. Override with `NAME_TO_BRANCH_PATH` / `NAME_TO_CAMP_LETTER_PATH` /
`FIIX_DASHBOARD_HTML` if you ever need a different source.

### Before you deploy anywhere real

- **`JWT_SECRET`** — the app now refuses to start against a non-SQLite
  database while still using the fallback dev secret (`app/main.py`).
  Generate a real one and set it as an environment variable on whatever
  platform you use: `openssl rand -hex 32`. Never put this in code or
  commit it.
- **`DATABASE_URL`** — point it at your managed Postgres instance.
- **`CORS_ALLOWED_ORIGINS`** — leave unset if the platform serves both the
  API and the static frontend from one service (the default setup, and
  what `render.yaml` assumes). Only set it if a separate frontend origin
  needs to call the API directly.
- **Uploaded photos are NOT durable by default.** `UPLOAD_DIR` defaults to
  a local `uploads/` folder (see `app/routers/public.py`), which most PaaS
  platforms wipe on every redeploy — their filesystem is ephemeral unless
  you attach a persistent volume. For a short testing/validation window
  this is a known, acceptable gap; before the real event, either attach a
  volume at `UPLOAD_DIR` or move to object storage (S3-compatible) — the
  module docstring in `app/routers/public.py` has the exact swap-in point.

### Picking a platform

For a fast path to something real people can test against, with minimal
server administration, a cloud PaaS with a managed Postgres add-on is the
easiest starting point — you're not locked into it for the real event,
this just gets people clicking around soonest. A plain VM or an on-site/
local-network machine both work too if you already know that's how the
real deployment will run; they just take more setup (HTTPS, process
supervision, DB backups all become your job).

**Render** — `render.yaml` at the repo root defines a web service +
managed Postgres wired together automatically (`DATABASE_URL` gets set
for you via `fromDatabase`, `JWT_SECRET` is auto-generated via
`generateValue`). From the Render dashboard: New → Blueprint → point at
this repo. Confirm the plan names in `render.yaml` still exist in Render's
current pricing before deploying — plan slugs change. After the first
deploy, run `python backfill_fiix_history.py` once via Render's dashboard
shell (or a one-off job) to load historical data — `seed.py` does **not**
need a manual run anymore; see the callout below.

**Railway / other Heroku-style platforms** — `Procfile` at the repo root
defines the start command (`alembic upgrade head && python seed.py &&
python check_location_hierarchy.py && uvicorn ...`), which Railway
auto-detects for Python apps built via Nixpacks. Add a Postgres plugin
from Railway's dashboard; it injects `DATABASE_URL` automatically. Set
`JWT_SECRET` yourself in the Railway dashboard's environment variables
(no auto-generate equivalent to Render's `generateValue`). One caveat
worth knowing: Railway's own docs currently flag their managed Postgres
HA offering as experimental and explicitly not for production databases
yet — worth checking their current docs before committing to it as the
database for anything beyond short-lived testing.

> **`seed.py` and `check_location_hierarchy.py` now run automatically on
> every deploy** (chained into the start command in both `Procfile` and
> `render.yaml`, right after `alembic upgrade head`). `seed.py` is
> idempotent — it upserts assets/teams/admin by name rather than
> duplicating them — so this is safe on every deploy, not just the first.
> This closes a real bug: an earlier migration (`5160d845f250`) added
> `assets.parent_id`, but a migration only adds columns, it doesn't
> populate them — only `seed.py`'s second pass does. Because `seed.py`
> was a manual one-time step back then, production ran the migration
> without a follow-up seed, every asset's `parent_id` stayed `NULL`, and
> both location pickers silently rendered a flat list instead of a tree.
> `check_location_hierarchy.py` now fails the deploy loudly if that ever
> happens again (see that script's docstring, and
> `tests/test_check_location_hierarchy.py`). `backfill_fiix_history.py` is
> the one script that's still a genuine one-time step — it's already
> idempotent about not re-importing the same historical WOs, but there's
> no reason to run it on every deploy.

Whichever platform: after the first successful deploy, run
`python backfill_fiix_history.py` once (dashboard shell, one-off job, or
`ssh`/exec into the running container, depending on platform), then log
in with the admin credentials `seed.py` printed in the deploy log and
change that password immediately — the printed password only ever
appears in that one log line.

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

77 tests, covering:
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
- **`tests/test_cutover_check.py`** — runs the actual `cutover_check.py`
  against the real project data as part of the suite, so a future schema
  or backfill change that breaks parity with the old dashboards' numbers
  fails here instead of only being caught by someone remembering to run
  the script by hand.

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
  contact info that preference needs), a hierarchical location picker
  (`static/location-picker.js`, shared with the LOC triage UI) sourced
  from `/public/locations/tree` — type to search, or browse the full
  branch → camp → subcamp tree — work type, description, a priority picker showing the
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
- `cutover_check.py` — verifies the new dashboard's numbers against the old
  static dashboard's numbers, computed from the exact same 623 historical
  records. Doesn't re-run the old dashboard's JS — reimplements its KPI/
  breakdown formulas straight from `dashboard_build_script.py` in Python,
  then stands up a fresh migrated+seeded+backfilled copy of the new system
  and diffs `crud.get_kpis`/`crud.get_breakdowns` against it. Currently:
  **every comparable metric matches exactly** (total, open, closed,
  highest+high open, completion rate, opened-on-the-data's-last-day, and
  every status/priority/work-type breakdown value). Two things are
  explicitly *not* compared, with the reason printed when you run it:
  "closed today" (the old dashboard baked in a manually-computed
  snapshot-diff constant that isn't derivable from the raw data at all —
  this project exists specifically to replace that with real tracking) and
  the team breakdown (the old dashboard folded unassigned WOs into a
  synthetic "Inactionable" bucket; the new one just doesn't count them,
  which is the more honest behavior). Run it yourself:
  ```bash
  python cutover_check.py
  ```
  Finding this check surfaced: `crud.get_kpis` was missing the
  "Highest+High, open" KPI entirely, even though the PRD lists it as part
  of the same KPI set as the old dashboards. Added — see `app/crud.py` and
  the `highest_high_open` field on `KPIOut`.
- `check_location_hierarchy.py` — deploy-time safety check, chained into
  the start command right after `seed.py`. Compares the number of
  root-level (`parent_id IS NULL`) assets against the number of top-level
  branches the source hierarchy file actually defines; fails loudly if the
  DB looks flat instead of nested. Exists specifically to catch the
  "migration added a column but nobody re-ran `seed.py`" failure mode —
  see its module docstring for the production incident that motivated it.
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
- ~~Hardcoded dev JWT secret could silently ship to a real deployment~~ —
  done: the app now refuses to start against a non-SQLite database while
  still using the fallback secret (`app/main.py`) — see "Deploying for
  testing/validation" above
- **Uploaded photos aren't durable across redeploys** on most PaaS
  platforms (ephemeral filesystem, no volume attached by default) — fine
  for a short testing window, needs a volume or object storage before the
  real event. See "Deploying for testing/validation" above.
- No email/SMS provider wired in yet for `notify_preference` — see
  "Notifications / SLA alerting" above
