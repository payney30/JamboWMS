"""
Backfill historical Fiix work orders into the new schema.

Source: the `RAW` JS array embedded in NJ_LOC_Work_Order_Dashboard.html —
this is the full 623-record superset (the BaseCamp and Program Team
dashboards are filtered subsets of the same data, confirmed by comparing
their `code` sets). We don't have the original pre-dashboard export
(wo_data.json) — it was ephemeral session data on whichever machine built
the dashboards — so this HTML file is the most complete source available.

What this does, per PHASE1_TECH_SPEC.md Section 5:
  "Historical Fiix WOs can optionally be backfilled into work_orders + a
  synthetic wo_status_history row (status_change, to_value = final status,
  changed_at = the WO's recorded date) so trend charts don't start at
  zero — acceptable to be lossy here since real history didn't exist
  before."

That's exactly what this script does: one WorkOrder row + exactly one
WOStatusHistory row per historical ticket, both dated to the ticket's
original `date` field. It deliberately does NOT try to reconstruct
reassignment history, multiple status transitions, or notes as separate
timestamped entries — that granularity was never captured in the Fiix
export, and inventing timestamps for it would be fabricating data, not
backfilling it.

KNOWN LOSSY BITS — read before you rely on downstream numbers:
  - For closed tickets, closed_at is set to the same `date` value used for
    created_at (that's the only timestamp the export has). Any
    "average time to close" metric computed over backfilled rows will
    read as ~0 for all of them. The daily opened/closed trend counts this
    exists to support are accurate; per-ticket duration is not.
  - Original requester name/email/phone were not part of this export.
    Backfilled rows get requester_name="Historical Fiix Import" and no
    contact info — they will never show up as "my submitted requests" in
    a future requester-facing status lookup, and that's expected.
  - Team/assignment is set directly (no reassignment audit trail is
    synthesized), since the export has no record of routing changes.

Idempotent: each backfilled row's external_ref is the original Fiix code
(e.g. "9348"). Re-running the script skips any code already present, so
it's safe to run more than once.

Usage:
    python backfill_fiix_history.py
    # or point it at a different copy of the dashboard HTML:
    FIIX_DASHBOARD_HTML=/path/to/other.html python backfill_fiix_history.py
"""
import datetime as dt
import json
import os
import re
import sys

from sqlalchemy import inspect

from app.database import SessionLocal, engine
from app import models, schemas

DASHBOARD_HTML_PATH = os.environ.get(
    "FIIX_DASHBOARD_HTML", "data/NJ_LOC_Work_Order_Dashboard.html"
)

UNASSIGNED_ASSET_NAME = "Unassigned (Historical Import)"
UNASSIGNED_ASSET_LOCATION_GROUP = "General/Other"

STATUS_MAP = {
    "Open": "Requested",
    "Requested": "Requested",
    "Assigned": "Assigned",
    "Work In Progress": "Work In Progress",
    "On Hold": "On Hold",
    "Closed, Completed": "Closed, Completed",
    "Closed, Incomplete": "Closed, Incomplete",
}

# Historical export used "NJ Items/parts" (lowercase p) — map case-
# insensitively onto the canonical WORK_TYPES casing rather than assuming
# the export's casing is authoritative.
_WORK_TYPE_LOOKUP = {w.lower(): w for w in schemas.WORK_TYPES}

_TRAILING_CODE_RE = re.compile(r"\s*\([^)]*\)\s*$")


def strip_trailing_code(asset_name: str) -> str:
    """'NJ Base Camp E (BC-E)' -> 'NJ Base Camp E' — the export includes
    the asset's short code in parens; our assets table doesn't."""
    return _TRAILING_CODE_RE.sub("", asset_name).strip()


def map_status(raw_status: str):
    return STATUS_MAP.get(raw_status)


def normalize_work_type(raw_type: str):
    raw_type = (raw_type or "").strip()
    if raw_type == "":
        return ""
    return _WORK_TYPE_LOOKUP.get(raw_type.lower())


def load_raw_records(html_path: str) -> list[dict]:
    with open(html_path, encoding="utf-8") as f:
        content = f.read()
    match = re.search(r"const RAW = (\[.*?\]);\n", content, re.S)
    if not match:
        raise ValueError(f"couldn't find a `const RAW = [...]` block in {html_path}")
    return json.loads(match.group(1))


def get_or_create_team(db, name: str) -> models.Team:
    team = db.query(models.Team).filter(models.Team.name == name).first()
    if team:
        return team
    team = models.Team(name=name)
    db.add(team)
    db.flush()
    return team


def get_or_create_unassigned_asset(db) -> models.Asset:
    asset = db.query(models.Asset).filter(models.Asset.name == UNASSIGNED_ASSET_NAME).first()
    if asset:
        return asset
    asset = models.Asset(name=UNASSIGNED_ASSET_NAME, location_group=UNASSIGNED_ASSET_LOCATION_GROUP)
    db.add(asset)
    db.flush()
    return asset


def resolve_asset(db, raw_asset_name: str, unassigned_asset: models.Asset):
    if not raw_asset_name:
        return unassigned_asset, True  # (asset, used_fallback)
    stripped = strip_trailing_code(raw_asset_name)
    asset = db.query(models.Asset).filter(models.Asset.name == stripped).first()
    if asset:
        return asset, False
    return unassigned_asset, True


def backfill(db, records: list[dict]) -> dict:
    """Returns a summary dict of counts for the final report."""
    summary = {
        "total": len(records),
        "created": 0,
        "skipped_already_imported": 0,
        "skipped_test_row": 0,
        "skipped_unmapped_status": 0,
        "skipped_unmapped_work_type": 0,
        "skipped_unmapped_priority": 0,
        "used_unassigned_asset_fallback": 0,
        "teams_created": [],
    }

    unassigned_asset = get_or_create_unassigned_asset(db)
    existing_refs = {
        r[0] for r in db.query(models.WorkOrder.external_ref).filter(
            models.WorkOrder.external_ref.isnot(None)
        ).all()
    }
    known_teams = {t.name for t in db.query(models.Team).all()}

    for rec in records:
        code = str(rec.get("code", "")).strip()
        description = (rec.get("description") or "").strip()

        if not code:
            continue
        if code in existing_refs:
            summary["skipped_already_imported"] += 1
            continue
        if description.upper().startswith("TEST."):
            summary["skipped_test_row"] += 1
            continue

        status = map_status(rec.get("status", ""))
        if status is None:
            summary["skipped_unmapped_status"] += 1
            continue

        work_type = normalize_work_type(rec.get("type", ""))
        if work_type is None:
            summary["skipped_unmapped_work_type"] += 1
            continue

        priority = rec.get("priority", "")
        # Enhancement backlog Phase 14 (PRD §13#15): urgency-tier rename.
        # This is a historical import of real, already-recorded Fiix
        # data — it was always written with the OLD priority names and
        # always will be, so it must validate against
        # schemas.LEGACY_PRIORITIES, not schemas.PRIORITIES (which now
        # means "valid for a NEW work order going forward" and no longer
        # includes these). Using the wrong tuple here would silently
        # skip every historical record.
        if priority not in schemas.LEGACY_PRIORITIES:
            summary["skipped_unmapped_priority"] += 1
            continue

        try:
            recorded_at = dt.datetime.fromisoformat(rec["date"])
        except (KeyError, ValueError):
            summary["skipped_unmapped_status"] += 1  # bucket with other data-quality skips
            continue

        asset, used_fallback = resolve_asset(db, rec.get("assets", ""), unassigned_asset)
        if used_fallback:
            summary["used_unassigned_asset_fallback"] += 1

        team_name = (rec.get("assignedUsers") or "").strip()
        assigned_team_id = None
        if team_name:
            if team_name not in known_teams:
                get_or_create_team(db, team_name)
                known_teams.add(team_name)
                summary["teams_created"].append(team_name)
            assigned_team_id = db.query(models.Team.id).filter(models.Team.name == team_name).scalar()

        wo = models.WorkOrder(
            wo_number=_next_wo_number(db),
            external_ref=code,
            requester_name="Historical Fiix Import",
            requester_email=None,
            requester_phone=None,
            asset_id=asset.id,
            work_type=work_type,
            description=description,
            priority=priority,
            status=status,
            assigned_team_id=assigned_team_id,
            created_at=recorded_at,
            updated_at=recorded_at,
            closed_at=recorded_at if status.startswith("Closed") else None,
        )
        db.add(wo)
        db.flush()  # get wo.id before writing history

        db.add(models.WOStatusHistory(
            work_order_id=wo.id,
            event_type="status_change",
            from_value=None,
            to_value=status,
            changed_by=None,
            changed_at=recorded_at,
        ))

        existing_refs.add(code)
        summary["created"] += 1

    db.commit()
    return summary


def _next_wo_number(db) -> str:
    """Mirrors app/crud.py's numbering so backfilled and live-created WOs
    never collide — both key off the same "highest wo_number issued"
    logic. Enhancement backlog Phase 4 (PRD §14#13): no "WO-" prefix
    anymore. Bug fix (PRD §14#21): computes the max in Python (strip
    non-digits, parse as int) rather than a SQL CAST — a CAST throws a
    hard error on Postgres for any pre-existing non-numeric wo_number
    value (e.g. legacy "WO-10001" rows), which a backfill run against a
    real, previously-used database is exactly likely to have. Keep this
    in sync with crud._next_wo_number."""
    numbers = [
        int(digits)
        for (raw,) in db.query(models.WorkOrder.wo_number).all()
        if (digits := "".join(ch for ch in (raw or "") if ch.isdigit()))
    ]
    next_id = (max(numbers) + 1) if numbers else 10001
    return str(max(next_id, 10001))


if __name__ == "__main__":
    if not inspect(engine).has_table("work_orders"):
        sys.exit("Schema not found. Run `alembic upgrade head` before backfilling (see README).")

    if not os.path.exists(DASHBOARD_HTML_PATH):
        sys.exit(
            f"Couldn't find {DASHBOARD_HTML_PATH}. Set FIIX_DASHBOARD_HTML to point "
            "at a copy of NJ_LOC_Work_Order_Dashboard.html."
        )

    records = load_raw_records(DASHBOARD_HTML_PATH)
    db = SessionLocal()
    try:
        summary = backfill(db, records)
    finally:
        db.close()

    print(f"source records:              {summary['total']}")
    print(f"created:                     {summary['created']}")
    print(f"already imported (skipped):  {summary['skipped_already_imported']}")
    print(f"TEST. rows skipped:          {summary['skipped_test_row']}")
    print(f"unmapped status skipped:     {summary['skipped_unmapped_status']}")
    print(f"unmapped work_type skipped:  {summary['skipped_unmapped_work_type']}")
    print(f"unmapped priority skipped:   {summary['skipped_unmapped_priority']}")
    print(f"used '{UNASSIGNED_ASSET_NAME}' fallback: {summary['used_unassigned_asset_fallback']}")
    if summary["teams_created"]:
        print(f"new teams auto-created: {sorted(set(summary['teams_created']))}")
    print()
    print("Reminder: closed_at on backfilled rows equals created_at (the export has")
    print("no separate close timestamp) — see the module docstring's KNOWN LOSSY BITS.")
