"""
Tests for app/crud.py's status-history engine.

These exercise the crud functions directly against the isolated test
session (fixture: `db`), rather than through the HTTP layer, so failures
point straight at the mutation logic instead of routing/schema noise.
The reassignment-note guard and general request flow are additionally
covered end-to-end in test_work_order_api.py.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app import crud, models, schemas


def _history_rows(db, wo_id):
    return (
        db.query(models.WOStatusHistory)
        .filter(models.WOStatusHistory.work_order_id == wo_id)
        .order_by(models.WOStatusHistory.id)
        .all()
    )


def test_create_work_order_writes_exactly_one_history_row(db, asset):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout",
            asset_id=asset.id,
            description="Broken light",
            priority="Next Day",
        ),
    )
    rows = _history_rows(db, wo.id)
    assert len(rows) == 1
    assert rows[0].event_type == "status_change"
    assert rows[0].from_value is None
    assert rows[0].to_value == "Requested"


def test_first_assignment_writes_status_change_and_reassignment_rows(db, asset, team, admin_user):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    crud.assign_work_order(
        db, wo, schemas.AssignRequest(team_id=team.id), changed_by=admin_user.id
    )

    rows = _history_rows(db, wo.id)
    # 1 create + 1 status_change (Requested -> Assigned) + 1 reassignment = 3
    assert len(rows) == 3
    event_types = [r.event_type for r in rows]
    assert event_types == ["status_change", "status_change", "reassignment"]
    assert rows[1].from_value == "Requested"
    assert rows[1].to_value == "Assigned"
    assert rows[2].from_value is None  # no prior team
    assert rows[2].to_value == team.name
    assert wo.status == "Assigned"


def test_reassignment_uses_reassignment_event_type_not_status_change(db, asset, team, other_team, admin_user):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    crud.assign_work_order(db, wo, schemas.AssignRequest(team_id=team.id), changed_by=admin_user.id)
    crud.assign_work_order(
        db, wo,
        schemas.AssignRequest(team_id=other_team.id, note="Wrong team, rerouting"),
        changed_by=admin_user.id,
    )

    rows = _history_rows(db, wo.id)
    reassignment_rows = [r for r in rows if r.event_type == "reassignment"]
    assert len(reassignment_rows) == 2
    assert reassignment_rows[1].from_value == team.name
    assert reassignment_rows[1].to_value == other_team.name
    # status stays "Assigned" on reroute — no second status_change row for this step
    status_change_rows = [r for r in rows if r.event_type == "status_change"]
    assert len(status_change_rows) == 2  # create + first assignment only


def test_reassignment_to_different_team_requires_note(db, asset, team, other_team, admin_user):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    crud.assign_work_order(db, wo, schemas.AssignRequest(team_id=team.id), changed_by=admin_user.id)

    with pytest.raises(Exception) as exc_info:
        crud.assign_work_order(
            db, wo, schemas.AssignRequest(team_id=other_team.id), changed_by=admin_user.id
        )
    assert "note" in str(exc_info.value).lower()

    # and the reroute must not have partially applied
    db.refresh(wo)
    assert wo.assigned_team_id == team.id


def test_reassigning_to_same_team_does_not_require_note(db, asset, team, admin_user):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    crud.assign_work_order(db, wo, schemas.AssignRequest(team_id=team.id), changed_by=admin_user.id)
    # re-assigning the same team (e.g. changing the person) shouldn't be blocked
    crud.assign_work_order(
        db, wo, schemas.AssignRequest(team_id=team.id, person_id=None), changed_by=admin_user.id
    )
    assert wo.assigned_team_id == team.id


def test_priority_change_writes_priority_change_row(db, asset, admin_user):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    crud.update_work_order_fields(
        db, wo, schemas.WorkOrderUpdate(priority="Immediate"), changed_by=admin_user.id
    )
    rows = _history_rows(db, wo.id)
    priority_rows = [r for r in rows if r.event_type == "priority_change"]
    assert len(priority_rows) == 1
    assert priority_rows[0].from_value == "Next Day"
    assert priority_rows[0].to_value == "Immediate"
    assert wo.priority == "Immediate"


def test_priority_change_to_same_value_writes_no_row(db, asset, admin_user):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    crud.update_work_order_fields(
        db, wo, schemas.WorkOrderUpdate(priority="Next Day"), changed_by=admin_user.id
    )
    rows = _history_rows(db, wo.id)
    assert len(rows) == 1  # only the creation row


def test_editing_description_alone_writes_no_history_row(db, asset, admin_user):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    crud.update_work_order_fields(
        db, wo, schemas.WorkOrderUpdate(description="updated text"), changed_by=admin_user.id
    )
    rows = _history_rows(db, wo.id)
    assert len(rows) == 1
    assert wo.description == "updated text"


@pytest.mark.parametrize("closing_status", ["Closed, Completed", "Closed, Incomplete"])
def test_closing_status_sets_closed_at(db, asset, admin_user, closing_status):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    assert wo.closed_at is None
    crud.change_status(
        db, wo, schemas.StatusChangeRequest(status=closing_status), changed_by=admin_user.id
    )
    assert wo.closed_at is not None

    rows = _history_rows(db, wo.id)
    status_rows = [r for r in rows if r.event_type == "status_change"]
    assert status_rows[-1].to_value == closing_status
    assert status_rows[-1].from_value == "Requested"


@pytest.mark.parametrize(
    "non_closing_status", ["Assigned", "Work In Progress", "On Hold"]
)
def test_non_closing_status_leaves_closed_at_null(db, asset, admin_user, non_closing_status):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    crud.change_status(
        db, wo, schemas.StatusChangeRequest(status=non_closing_status), changed_by=admin_user.id
    )
    assert wo.closed_at is None


def test_reopening_a_closed_wo_clears_closed_at(db, asset, admin_user):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    crud.change_status(
        db, wo, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id
    )
    assert wo.closed_at is not None

    crud.change_status(
        db, wo, schemas.StatusChangeRequest(status="Work In Progress"), changed_by=admin_user.id
    )
    assert wo.closed_at is None


def test_invalid_status_is_rejected_and_writes_no_history_row(db, asset, admin_user):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    with pytest.raises(Exception):
        crud.change_status(
            db, wo, schemas.StatusChangeRequest(status="Not A Real Status"), changed_by=admin_user.id
        )
    rows = _history_rows(db, wo.id)
    assert len(rows) == 1  # only the creation row — the bad request never touched history
    assert wo.status == "Requested"  # unchanged


def test_every_mutating_field_produces_exactly_one_history_row_per_change(db, asset, team, other_team, admin_user):
    """
    Generic sweep across the four tracked fields (status, assigned_team_id,
    assigned_person_id via assign, priority): each individual mutation call
    should add exactly one new row to wo_status_history.
    """
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    count = len(_history_rows(db, wo.id))
    assert count == 1

    crud.assign_work_order(db, wo, schemas.AssignRequest(team_id=team.id), changed_by=admin_user.id)
    new_count = len(_history_rows(db, wo.id))
    assert new_count - count == 2  # status_change (Requested->Assigned) + reassignment
    count = new_count

    crud.update_work_order_fields(db, wo, schemas.WorkOrderUpdate(priority="Same Day"), changed_by=admin_user.id)
    new_count = len(_history_rows(db, wo.id))
    assert new_count - count == 1
    count = new_count

    crud.change_status(db, wo, schemas.StatusChangeRequest(status="Work In Progress"), changed_by=admin_user.id)
    new_count = len(_history_rows(db, wo.id))
    assert new_count - count == 1
    count = new_count

    crud.assign_work_order(
        db, wo,
        schemas.AssignRequest(team_id=other_team.id, note="rerouting for coverage"),
        changed_by=admin_user.id,
    )
    new_count = len(_history_rows(db, wo.id))
    # status is already "Work In Progress" (not "Requested"), so this reroute
    # writes only the reassignment row, no extra status_change
    assert new_count - count == 1


def test_failed_history_write_rolls_back_the_whole_mutation(db, asset, admin_user):
    """
    Forces a FK-constraint violation on the wo_status_history insert (an
    impossible changed_by user id) to prove the status mutation and its
    history row share one transaction: if the history write fails, the
    work order's status must not have changed either.
    """
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day"
        ),
    )
    nonexistent_user_id = 999_999

    with pytest.raises(IntegrityError):
        crud.change_status(
            db, wo,
            schemas.StatusChangeRequest(status="Closed, Completed"),
            changed_by=nonexistent_user_id,
        )

    db.rollback()  # the app's request-scoped session would be discarded; simulate that here

    fresh = db.get(models.WorkOrder, wo.id)
    assert fresh.status == "Requested"  # unchanged — the failed commit did not partially apply
    assert fresh.closed_at is None
    rows = _history_rows(db, wo.id)
    assert len(rows) == 1  # only the original creation row; the failed status_change never landed
