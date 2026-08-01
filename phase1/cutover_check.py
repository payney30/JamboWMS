"""
Cutover check: verify the new dashboard's numbers against the old static
dashboard's numbers, computed from the exact same underlying data.

This does NOT re-run the old dashboard's JS. It reimplements the old
dashboard's KPI/breakdown formulas in Python, straight from
dashboard_build_script.py (renderKPIs, countBy, countByTeam), and applies
them to the same RAW array the backfill script imports. Then it stands up
a fresh copy of the new system (migrate -> seed -> backfill) and pulls the
same numbers from crud.get_kpis / crud.get_breakdowns, and diffs the two.

Two of the old dashboard's numbers are NOT expected to match, and this
script says so explicitly rather than silently comparing them:

  - "Closed today" (CLOSED_TODAY): baked into the old dashboard as a
    literal constant (106) that a separate, undocumented script computed
    by diffing snapshots. It is not derivable from RAW at all. The new
    system computes this from real wo_status_history instead — that's
    the whole reason this project exists — so there is nothing to
    reproduce here, only to confirm the new number is internally
    consistent (see the note printed at the end).

  - Team breakdown: the old dashboard collapses blank/"Unassigned"
    assignedUsers into a synthetic "Inactionable" bucket and includes it
    in the chart. The new system's by_team breakdown only counts WOs that
    actually have a team assigned (see crud.get_breakdowns and the test
    test_breakdowns_by_team_only_counts_assigned_work_orders) — unassigned
    WOs simply don't appear there. Deliberate difference, not a bug:
    "Inactionable" was a workaround for a chart library that needed every
    slice accounted for, not a meaningful operational category, so it's
    not reproduced here either.

Usage:
    python cutover_check.py
    # or point it at a different copy of the dashboard HTML:
    FIIX_DASHBOARD_HTML=/path/to/other.html python cutover_check.py
"""
import datetime as dt
import os
import sys

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

import backfill_fiix_history as backfill_mod
import seed as seed_mod
from app import crud, models
from app.database import Base

DASHBOARD_HTML_PATH = os.environ.get(
    "FIIX_DASHBOARD_HTML", "data/NJ_LOC_Work_Order_Dashboard.html"
)


# ---------------------------------------------------------------------------
# Old dashboard's formulas, reimplemented from dashboard_build_script.py
# (function renderKPIs ~line 611, countBy ~line 379).
# ---------------------------------------------------------------------------

def old_dashboard_kpis(records: list[dict]) -> dict:
    total = len(records)
    open_ = sum(1 for r in records if not r["status"].startswith("Closed"))
    closed = sum(1 for r in records if r["status"].startswith("Closed"))
    highest_high_open = sum(
        1 for r in records
        if not r["status"].startswith("Closed") and r["priority"] in ("Highest", "High")
    )
    completion_rate = round((closed / total) * 100) if total else 0

    # asOf = the latest date in the dataset (dashboard_build_script.py
    # line 683-684: derived from the max of the sorted dates array).
    dates = [dt.datetime.fromisoformat(r["date"]) for r in records if r.get("date")]
    as_of = max(dates)
    day_start = dt.datetime(as_of.year, as_of.month, as_of.day)
    opened_today = sum(1 for d in dates if day_start <= d <= as_of)

    return {
        "total": total,
        "open": open_,
        "closed": closed,
        "highest_high_open": highest_high_open,
        "completion_rate": completion_rate,
        "opened_today": opened_today,
        "as_of": as_of,
    }


def old_dashboard_breakdowns(records: list[dict]) -> dict:
    """Same values the old dashboard's countBy() would produce, EXCEPT
    status and work_type are passed through the same normalization the
    backfill applies (map_status: "Open" -> "Requested"; normalize_work_type:
    "NJ Items/parts" -> "NJ Items/Parts"). Comparing raw-vs-normalized would
    show every "Open"/"Requested" and "NJ Items/parts"/"NJ Items/Parts" pair
    as a mismatch even though they represent the same records — that's a
    labeling difference the backfill deliberately introduces, not a data
    discrepancy worth flagging here."""
    def count_by(key, normalize=lambda v: v):
        m = {}
        for r in records:
            raw = r.get(key) or ""
            k = normalize(raw) if raw else "Unset"
            k = k if k else "Unset"
            m[k] = m.get(k, 0) + 1
        return m

    return {
        "by_status": count_by("status", backfill_mod.map_status),
        # Enhancement backlog Phase 18 (PRD §13#15 follow-up, 7/30/26):
        # same normalization treatment as by_status/by_work_type above,
        # for the same reason — the backfill now translates old priority
        # names to new ones on the way in (see backfill_fiix_history.py's
        # _LEGACY_PRIORITY_MAP), so comparing raw "Highest" against
        # stored "Immediate" would show every record as a mismatch even
        # though it's the same data, just relabeled. Not a discrepancy
        # worth flagging here, same as the status/work_type cases.
        "by_priority": count_by(
            "priority", lambda v: backfill_mod._LEGACY_PRIORITY_MAP.get(v, v)
        ),
        "by_work_type": count_by("type", backfill_mod.normalize_work_type),
    }


# ---------------------------------------------------------------------------
# New system: fresh DB, migrated + seeded + backfilled the same way an
# operator would run it, then queried through the real crud functions.
# ---------------------------------------------------------------------------

def build_new_system_db(records: list[dict], tmp_db_path: str):
    for var, default in [
        ("NAME_TO_BRANCH_PATH", "data/name_to_branch.json"),
        ("NAME_TO_CAMP_LETTER_PATH", "data/name_to_camp_letter.json"),
    ]:
        os.environ.setdefault(var, default)
        if not os.path.exists(os.environ[var]):
            sys.exit(f"{var} points to a missing file: {os.environ[var]}")

    engine = create_engine(f"sqlite:///{tmp_db_path}")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    seed_mod.seed_assets(db)
    seed_mod.seed_teams(db)
    backfill_mod.backfill(db, records)
    return db


def new_kpis_opened_on_date(db, as_of: dt.datetime) -> int:
    """The new system's get_kpis() always uses real wall-clock 'today',
    which isn't meaningful against historical data — so for this one
    comparison, query directly for the old dashboard's actual reference
    date (the last record's date) instead."""
    day_start = dt.datetime(as_of.year, as_of.month, as_of.day)
    return db.query(func.count(models.WorkOrder.id)).filter(
        models.WorkOrder.created_at >= day_start,
        models.WorkOrder.created_at <= as_of,
    ).scalar()


def compare(label, old_val, new_val, results: list):
    results.append((label, old_val, new_val, old_val == new_val))


def main():
    if not os.path.exists(DASHBOARD_HTML_PATH):
        sys.exit(f"Couldn't find {DASHBOARD_HTML_PATH}. Set FIIX_DASHBOARD_HTML.")

    records = backfill_mod.load_raw_records(DASHBOARD_HTML_PATH)
    old_kpis = old_dashboard_kpis(records)
    old_breakdowns = old_dashboard_breakdowns(records)

    tmp_db_path = "/tmp/cutover_check.db"
    if os.path.exists(tmp_db_path):
        os.remove(tmp_db_path)
    db = build_new_system_db(records, tmp_db_path)

    new_kpis = crud.get_kpis(db)
    new_breakdowns = crud.get_breakdowns(db)

    print("=" * 70)
    print(f"CUTOVER CHECK — {len(records)} historical records")
    print(f"as-of (latest record date in the dataset): {old_kpis['as_of']}")
    print("=" * 70)

    results = []
    compare("Total work orders", old_kpis["total"], new_kpis["total"], results)
    compare("Open / active", old_kpis["open"], new_kpis["open"], results)
    compare("Closed", old_kpis["closed"], new_kpis["closed"], results)
    compare("Highest+High, open", old_kpis["highest_high_open"], new_kpis["highest_high_open"], results)
    compare("Completion rate (%)", old_kpis["completion_rate"], round(new_kpis["completion_rate"]), results)
    compare(
        f"Opened on {old_kpis['as_of'].date()} (old 'opened today')",
        old_kpis["opened_today"],
        new_kpis_opened_on_date(db, old_kpis["as_of"]),
        results,
    )

    print(f"\n{'KPI':45} {'OLD':>8} {'NEW':>8}  MATCH")
    print("-" * 75)
    all_match = True
    for label, old_val, new_val, match in results:
        all_match = all_match and match
        flag = "OK" if match else "MISMATCH"
        print(f"{label:45} {str(old_val):>8} {str(new_val):>8}  {flag}")

    print()
    print("NOT compared (see module docstring for why):")
    print("  'Closed today' — old dashboard baked in a manual snapshot-diff value (106),")
    print("    not derivable from RAW at all. New system value from real history:",
          new_kpis["closed_today"])
    print("    (0 is the correct value here — nothing was closed on today's actual")
    print("    wall-clock date in a database seeded entirely from historical data.)")

    print()
    print(f"{'Breakdown':45} {'OLD':>8} {'NEW':>8}  MATCH")
    print("-" * 75)
    for group_key, old_map in [
        ("by_status", old_breakdowns["by_status"]),
        ("by_priority", old_breakdowns["by_priority"]),
        ("by_work_type", old_breakdowns["by_work_type"]),
    ]:
        new_map = new_breakdowns[group_key]
        for k in sorted(set(old_map) | set(new_map)):
            old_v = old_map.get(k, 0)
            new_v = new_map.get(k, 0)
            match = old_v == new_v
            all_match = all_match and match
            flag = "OK" if match else "MISMATCH"
            print(f"  {group_key}.{k:33} {old_v:>8} {new_v:>8}  {flag}")

    print()
    print("=" * 70)
    print("ALL COMPARABLE METRICS MATCH" if all_match else "MISMATCHES FOUND — see above")
    print("=" * 70)

    db.close()
    os.remove(tmp_db_path)
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(main())
