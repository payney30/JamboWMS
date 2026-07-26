# Phase 1 Technical Spec
## DB + Status-History Engine + LOC Triage View

**Scope per PRD Section 10 phasing:** this phase replaces the snapshot-file hack with a real database and gives the LOC a live triage interface. No requester-facing form yet (Phase 2), no technician self-service queue yet (Phase 3), no notifications/SLA alerting yet (Phase 4).

**Definition of done:** an LOC staffer can log in, see every work order in one live inbox, create/edit/assign/prioritize/close a WO, add notes, and have every change reflected in a real-time dashboard — with zero manual export/rebuild steps.

---

## 1. Database Schema

Postgres. DDL below is the Phase 1 subset of the full PRD data model (Section 5) — `wo_attachments` and `response_templates` are stubbed as empty tables so Phase 2/3 don't require a schema migration, but no upload/template features ship yet.

```sql
CREATE TABLE teams (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,       -- e.g. "2026 Jamboree Maintenance (Repairs and General Needs)"
    is_active     BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE users (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('loc', 'tech', 'leadership', 'admin')),
    team_id       INTEGER REFERENCES teams(id),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE assets (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,        -- matches Asset_Hierarchy_Analysis.md node names
    location_group TEXT NOT NULL,              -- one of the 9 branches (name_to_branch.json value)
    camp_letter   TEXT                         -- nullable, only set for Base Camps assets
);

CREATE TABLE work_orders (
    id               SERIAL PRIMARY KEY,
    wo_number        TEXT NOT NULL UNIQUE,      -- human-facing, e.g. "WO-10042"
    requester_name   TEXT NOT NULL,
    requester_email  TEXT,
    requester_phone  TEXT,
    asset_id         INTEGER NOT NULL REFERENCES assets(id),
    work_type        TEXT NOT NULL CHECK (work_type IN ('NJ IT','NJ Items/Parts','NJ Maintenance','NJ Transportation','')),
    description      TEXT NOT NULL,
    priority         TEXT NOT NULL CHECK (priority IN ('Highest','High','Medium','Low','Lowest')),
    status           TEXT NOT NULL DEFAULT 'Requested'
                       CHECK (status IN ('Requested','Assigned','Work In Progress','On Hold',
                                          'Closed, Completed','Closed, Incomplete')),
    assigned_team_id INTEGER REFERENCES teams(id),
    assigned_person_id INTEGER REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at        TIMESTAMPTZ
);
CREATE INDEX idx_wo_status ON work_orders(status);
CREATE INDEX idx_wo_priority ON work_orders(priority);
CREATE INDEX idx_wo_team ON work_orders(assigned_team_id);
CREATE INDEX idx_wo_created ON work_orders(created_at);

CREATE TABLE wo_notes (
    id             SERIAL PRIMARY KEY,
    work_order_id  INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    author_id      INTEGER REFERENCES users(id),
    note_text      TEXT NOT NULL,
    note_type      TEXT NOT NULL CHECK (note_type IN ('internal','instruction','work_note')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_notes_wo ON wo_notes(work_order_id);

CREATE TABLE wo_status_history (
    id             SERIAL PRIMARY KEY,
    work_order_id  INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    event_type     TEXT NOT NULL CHECK (event_type IN ('status_change','reassignment','priority_change')),
    from_value     TEXT,
    to_value       TEXT NOT NULL,
    changed_by     INTEGER REFERENCES users(id),
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_history_wo ON wo_status_history(work_order_id);
CREATE INDEX idx_history_changed_at ON wo_status_history(changed_at);

-- Stubbed for Phase 2/3, not used yet:
CREATE TABLE wo_attachments (
    id             SERIAL PRIMARY KEY,
    work_order_id  INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    uploaded_by    INTEGER REFERENCES users(id),
    file_url       TEXT NOT NULL,
    uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE response_templates (
    id                 SERIAL PRIMARY KEY,
    text               TEXT NOT NULL,
    category           TEXT NOT NULL CHECK (category IN ('triage_note','work_note','closing_resolution')),
    work_type          TEXT,
    use_count          INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
    created_from_wo_id INTEGER REFERENCES work_orders(id),
    source             TEXT NOT NULL CHECK (source IN ('seeded','usage_promoted','admin_authored')),
    created_by         INTEGER REFERENCES users(id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         INTEGER REFERENCES users(id),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Trigger rule (enforced in the app layer, not SQL):** every write to `work_orders.status`, `.assigned_team_id`, or `.priority` must be paired, in the same transaction, with an insert into `wo_status_history`. This is the core rule the whole phasing decision hinges on — no dashboard number is trustworthy if this is skipped once.

---

## 2. Status-History Engine

This is the piece that permanently retires `dashboard_snapshot_history.json` and the daily-diffing hack.

Rules:
- Any endpoint that mutates `status`, `assigned_team_id`, `assigned_person_id`, or `priority` writes to `wo_status_history` in the same DB transaction (not a background job — if the history write fails, the whole mutation rolls back).
- Reassignment (`assigned_team_id` change) is logged as `event_type = 'reassignment'`, not folded into `status_change`, so "how often does X team's work get rerouted to Y" is a direct query, not an inference.
- `work_orders.closed_at` is set exactly when `status` transitions to `'Closed, Completed'` or `'Closed, Incomplete'` — read from `wo_status_history`, not wall-clock at request time, so it can't drift from the audit trail.
- "Opened today" / "closed today" become:
  ```sql
  -- opened today
  SELECT count(*) FROM work_orders WHERE created_at::date = current_date;
  -- closed today
  SELECT count(*) FROM wo_status_history
  WHERE event_type = 'status_change'
    AND to_value IN ('Closed, Completed','Closed, Incomplete')
    AND changed_at::date = current_date;
  ```
  Both are simple, live, indexed queries — no snapshot file, no diffing job.

---

## 3. API (FastAPI)

Phase 1 only needs LOC-facing endpoints (no public requester endpoint yet).

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/login` | LOC/admin login, returns JWT |
| GET | `/work-orders` | List with filters: `status`, `priority`, `team_id`, `work_type`, `location_group`, `search`, sortable, paginated |
| POST | `/work-orders` | Create a WO (LOC manual entry — stand-in for the Phase-2 public form) |
| GET | `/work-orders/{id}` | Full WO detail incl. notes + status history |
| PATCH | `/work-orders/{id}` | Edit priority/location/work_type/description |
| POST | `/work-orders/{id}/assign` | Set team (+ optional person) → writes `reassignment` history |
| POST | `/work-orders/{id}/status` | Change status → writes `status_change` history, sets `closed_at` if terminal |
| POST | `/work-orders/{id}/notes` | Add a timestamped note |
| GET | `/dashboard/kpis` | Total/open/closed/completion-rate/opened-today/closed-today |
| GET | `/dashboard/breakdowns` | Status/priority/work-type/location/team counts for charts |
| GET | `/teams`, `/assets` | Reference data for dropdowns |

All mutating endpoints require an authenticated LOC/admin user (`role IN ('loc','admin')`).

---

## 4. LOC Triage View (UI)

Single authenticated page, three regions:

1. **Filter bar** — status, priority, work type, team, location, free-text search (reuses the exact filter set already proven in the current dashboards).
2. **Inbox table** — sorted oldest + highest-priority first by default; matches the current "needing attention" table. Row click opens the WO detail panel.
3. **WO detail panel** (drawer or modal) — full field edit, status/priority/assign controls, note thread, status-history timeline. This is the net-new interaction the current static dashboard can't do at all.

Visual language: reuse the existing dashboard CSS (brand colors, KPI cards, status dot/chip conventions already built in `dashboard_build_script.py`) so this doesn't look like a bolted-on tool.

Out of scope for Phase 1: saved/custom views beyond the fixed filter set, bulk actions, duplicate-flagging UI — these are called out in the PRD as Phase 1+ nice-to-haves but aren't required for the "kill the snapshot hack" goal.

---

## 5. Migration / Seeding

- `assets` table is seeded once from `name_to_branch.json` + `name_to_camp_letter.json` (already generated by `parse_asset_hierarchy.py` from `Asset_Hierarchy_Analysis.md`) — no need to re-derive this logic, just load the existing JSON.
- `teams` seeded from the distinct `assignedUsers` values already appearing in the current Fiix export data.
- Historical Fiix WOs can optionally be backfilled into `work_orders` + a synthetic `wo_status_history` row (`status_change`, `to_value = final status`, `changed_at = the WO's recorded date`) so trend charts don't start at zero — acceptable to be lossy here since real history didn't exist before.

---

## 6. Ticket Breakdown

1. **DB schema + migrations** — Postgres, Alembic, tables above.
2. **Seed script** — assets from JSON, teams from historical data.
3. **Auth** — login endpoint, JWT, role check dependency.
4. **Work order CRUD + status-history engine** — the transactional write rule is the highest-risk piece; needs its own tests (assert every status/team/priority mutation produces exactly one history row).
5. **Dashboard KPI/breakdown endpoints** — direct SQL per Section 2 above.
6. **LOC triage frontend** — filter bar, inbox table, detail panel.
7. **Backfill script** — historical Fiix data → `work_orders` + synthetic history.
8. **Cutover check** — run new dashboard KPIs side-by-side against the current static dashboard on the same data for one day to confirm numbers match before relying on it live.
