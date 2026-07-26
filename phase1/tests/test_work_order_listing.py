"""
Tests for crud.list_work_orders' default ordering — PRD 4.2 calls for
"oldest + highest-priority first," matching the old dashboard's "needing
attention" table.
"""
import datetime as dt

from app import crud, schemas


def _make_wo(db, asset, priority, created_at):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority=priority
        ),
    )
    wo.created_at = created_at
    db.commit()
    return wo


def test_default_sort_is_priority_then_oldest_first(db, asset):
    now = dt.datetime.utcnow()
    # Deliberately created out of priority and chronological order.
    low_old = _make_wo(db, asset, "Low", now - dt.timedelta(hours=5))
    high_new = _make_wo(db, asset, "High", now - dt.timedelta(hours=1))
    highest_old = _make_wo(db, asset, "Highest", now - dt.timedelta(hours=4))
    highest_new = _make_wo(db, asset, "Highest", now - dt.timedelta(hours=2))
    medium = _make_wo(db, asset, "Medium", now - dt.timedelta(hours=3))

    ordered = crud.list_work_orders(db)
    ids_in_order = [wo.id for wo in ordered]

    expected = [highest_old.id, highest_new.id, high_new.id, medium.id, low_old.id]
    assert ids_in_order == expected
