"""
Tests for backfill_fiix_history.py.

Uses a small synthetic dataset for most cases (fast, exercises every edge
case deliberately) plus one test against the real project file to catch
anything the synthetic cases don't (encoding quirks, unexpected values,
actual scale).
"""
import os

import pytest

import backfill_fiix_history as backfill_mod
from app import models


# --- pure helper functions -------------------------------------------------

def test_strip_trailing_code():
    assert backfill_mod.strip_trailing_code("NJ Base Camp E (BC-E)") == "NJ Base Camp E"
    assert backfill_mod.strip_trailing_code("Jamboree 2026 (SBR-NJ)") == "Jamboree 2026"
    assert backfill_mod.strip_trailing_code("No Parens Here") == "No Parens Here"


def test_map_status():
    assert backfill_mod.map_status("Open") == "Requested"
    assert backfill_mod.map_status("Closed, Completed") == "Closed, Completed"
    assert backfill_mod.map_status("Some Unknown Status") is None


def test_normalize_work_type():
    assert backfill_mod.normalize_work_type("NJ Items/parts") == "NJ Items/Parts"
    assert backfill_mod.normalize_work_type("NJ Maintenance") == "NJ Maintenance"
    assert backfill_mod.normalize_work_type("") == ""
    assert backfill_mod.normalize_work_type("Not A Real Type") is None


# --- backfill() against a small synthetic dataset ---------------------------

def _rec(**overrides):
    base = {
        "code": "1001",
        "description": "Broken light in the mess hall",
        "assignedUsers": "2026 Jamboree Maintenance (Repairs and General Needs)",
        "priority": "High",
        "assets": "NJ Base Camp E (BC-E)",
        "date": "2026-07-15T10:30:00",
        "status": "Closed, Completed",
        "type": "NJ Maintenance",
        "locGroup": "Base Camps",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def base_camp_e_asset(db):
    a = models.Asset(name="NJ Base Camp E", location_group="Base Camps")
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_backfill_creates_wo_and_one_history_row(db, base_camp_e_asset):
    summary = backfill_mod.backfill(db, [_rec()])
    assert summary["created"] == 1

    wo = db.query(models.WorkOrder).filter(models.WorkOrder.external_ref == "1001").first()
    assert wo is not None
    assert wo.status == "Closed, Completed"
    assert wo.requester_name == "Historical Fiix Import"
    assert wo.asset_id == base_camp_e_asset.id

    history = db.query(models.WOStatusHistory).filter(models.WOStatusHistory.work_order_id == wo.id).all()
    assert len(history) == 1
    assert history[0].event_type == "status_change"
    assert history[0].to_value == "Closed, Completed"


def test_backfill_sets_closed_at_for_closed_status_only(db, base_camp_e_asset):
    summary = backfill_mod.backfill(db, [
        _rec(code="1001", status="Closed, Completed"),
        _rec(code="1002", status="Assigned"),
    ])
    assert summary["created"] == 2

    closed_wo = db.query(models.WorkOrder).filter(models.WorkOrder.external_ref == "1001").first()
    open_wo = db.query(models.WorkOrder).filter(models.WorkOrder.external_ref == "1002").first()
    assert closed_wo.closed_at is not None
    assert closed_wo.closed_at == closed_wo.created_at  # documented lossy behavior
    assert open_wo.closed_at is None


def test_backfill_maps_open_status_to_requested(db, base_camp_e_asset):
    backfill_mod.backfill(db, [_rec(code="1001", status="Open")])
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.external_ref == "1001").first()
    assert wo.status == "Requested"


def test_backfill_normalizes_work_type_casing(db, base_camp_e_asset):
    backfill_mod.backfill(db, [_rec(code="1001", type="NJ Items/parts")])
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.external_ref == "1001").first()
    assert wo.work_type == "NJ Items/Parts"


def test_backfill_skips_test_prefixed_descriptions(db, base_camp_e_asset):
    summary = backfill_mod.backfill(db, [_rec(description="TEST. ignore me")])
    assert summary["created"] == 0
    assert summary["skipped_test_row"] == 1


def test_backfill_skips_unmapped_status(db, base_camp_e_asset):
    summary = backfill_mod.backfill(db, [_rec(status="Some Made Up Status")])
    assert summary["created"] == 0
    assert summary["skipped_unmapped_status"] == 1


def test_backfill_skips_unmapped_work_type(db, base_camp_e_asset):
    summary = backfill_mod.backfill(db, [_rec(type="Not A Real Type")])
    assert summary["created"] == 0
    assert summary["skipped_unmapped_work_type"] == 1


def test_backfill_skips_unmapped_priority(db, base_camp_e_asset):
    summary = backfill_mod.backfill(db, [_rec(priority="Meh")])
    assert summary["created"] == 0
    assert summary["skipped_unmapped_priority"] == 1


def test_backfill_falls_back_to_unassigned_asset_for_missing_asset(db):
    summary = backfill_mod.backfill(db, [_rec(assets="")])
    assert summary["created"] == 1
    assert summary["used_unassigned_asset_fallback"] == 1
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.external_ref == "1001").first()
    assert wo.asset.name == backfill_mod.UNASSIGNED_ASSET_NAME


def test_backfill_falls_back_to_unassigned_asset_for_unresolvable_name(db):
    summary = backfill_mod.backfill(db, [_rec(assets="Some Totally Unknown Place (XYZ)")])
    assert summary["used_unassigned_asset_fallback"] == 1


def test_backfill_creates_missing_teams(db, base_camp_e_asset):
    summary = backfill_mod.backfill(db, [_rec(assignedUsers="Brand New Team Nobody Seeded")])
    assert "Brand New Team Nobody Seeded" in summary["teams_created"]
    team = db.query(models.Team).filter(models.Team.name == "Brand New Team Nobody Seeded").first()
    assert team is not None
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.external_ref == "1001").first()
    assert wo.assigned_team_id == team.id


def test_backfill_leaves_team_unassigned_when_blank(db, base_camp_e_asset):
    backfill_mod.backfill(db, [_rec(assignedUsers="")])
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.external_ref == "1001").first()
    assert wo.assigned_team_id is None


def test_backfill_is_idempotent(db, base_camp_e_asset):
    first = backfill_mod.backfill(db, [_rec()])
    assert first["created"] == 1

    second = backfill_mod.backfill(db, [_rec()])
    assert second["created"] == 0
    assert second["skipped_already_imported"] == 1

    total = db.query(models.WorkOrder).filter(models.WorkOrder.external_ref == "1001").count()
    assert total == 1


def test_backfilled_wo_numbers_dont_collide_with_live_wos(db, base_camp_e_asset):
    from app import crud, schemas
    live = crud.create_work_order(db, schemas.WorkOrderCreate(
        requester_name="Live Scout", asset_id=base_camp_e_asset.id,
        description="a live-created WO", priority="Medium",
    ))
    backfill_mod.backfill(db, [_rec(code="1001")])
    backfilled = db.query(models.WorkOrder).filter(models.WorkOrder.external_ref == "1001").first()
    assert live.wo_number != backfilled.wo_number


# --- against the real project data ------------------------------------------

REAL_DASHBOARD_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "NJ_LOC_Work_Order_Dashboard.html"
)


@pytest.mark.skipif(not os.path.exists(REAL_DASHBOARD_PATH), reason="project dashboard file not present")
def test_backfill_against_real_dashboard_data(db):
    """Loads the actual embedded historical dataset and backfills it into
    an isolated test database, sanity-checking the aggregate numbers
    rather than asserting on individual records (which would just
    hardcode today's data into the test)."""
    records = backfill_mod.load_raw_records(REAL_DASHBOARD_PATH)
    assert len(records) > 500  # sanity: this should be the ~623-record superset

    summary = backfill_mod.backfill(db, records)

    accounted_for = (
        summary["created"]
        + summary["skipped_already_imported"]
        + summary["skipped_test_row"]
        + summary["skipped_unmapped_status"]
        + summary["skipped_unmapped_work_type"]
        + summary["skipped_unmapped_priority"]
    )
    assert accounted_for == summary["total"]
    assert summary["created"] > 0

    total_wos = db.query(models.WorkOrder).count()
    assert total_wos == summary["created"]

    # every created WO should have exactly one status_change history row
    from sqlalchemy import func
    mismatched = (
        db.query(models.WorkOrder.id)
        .outerjoin(models.WOStatusHistory)
        .group_by(models.WorkOrder.id)
        .having(func.count(models.WOStatusHistory.id) != 1)
        .count()
    )
    assert mismatched == 0

    # running it again against the same records should create nothing new
    rerun = backfill_mod.backfill(db, records)
    assert rerun["created"] == 0
    assert rerun["skipped_already_imported"] == summary["created"]
