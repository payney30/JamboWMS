"""
Tests for crud.get_kpis / crud.get_breakdowns — the live queries that
replace dashboard_snapshot_history.json. These build a small seeded set
of work orders with known, deliberately-backdated timestamps and assert
the counts against hand-computed expectations.
"""
import datetime as dt

from app import crud, models, schemas


def _make_wo(db, asset, priority="Medium", work_type="NJ Maintenance"):
    return crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout",
            asset_id=asset.id,
            description="x",
            priority=priority,
            work_type=work_type,
        ),
    )


def test_opened_today_counts_only_todays_creations(db, asset):
    today_wo = _make_wo(db, asset)

    yesterday_wo = _make_wo(db, asset)
    yesterday_wo.created_at = dt.datetime.utcnow() - dt.timedelta(days=1)
    db.commit()

    kpis = crud.get_kpis(db)
    assert kpis["opened_today"] == 1
    assert kpis["total"] == 2


def test_closed_today_counts_only_todays_close_events(db, asset, admin_user):
    wo_closed_today = _make_wo(db, asset)
    crud.change_status(
        db, wo_closed_today,
        schemas.StatusChangeRequest(status="Closed, Completed"),
        changed_by=admin_user.id,
    )

    wo_closed_yesterday = _make_wo(db, asset)
    crud.change_status(
        db, wo_closed_yesterday,
        schemas.StatusChangeRequest(status="Closed, Incomplete"),
        changed_by=admin_user.id,
    )
    # backdate the closing history row directly, same way a real historical
    # backfill row would be inserted
    closing_row = (
        db.query(models.WOStatusHistory)
        .filter(
            models.WOStatusHistory.work_order_id == wo_closed_yesterday.id,
            models.WOStatusHistory.to_value == "Closed, Incomplete",
        )
        .one()
    )
    closing_row.changed_at = dt.datetime.utcnow() - dt.timedelta(days=1)
    db.commit()

    kpis = crud.get_kpis(db)
    assert kpis["closed_today"] == 1
    assert kpis["closed"] == 2  # both are closed overall, only one closed *today*


def test_open_and_closed_and_completion_rate(db, asset, admin_user):
    _make_wo(db, asset)  # stays open
    wo2 = _make_wo(db, asset)
    crud.change_status(
        db, wo2, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id
    )

    kpis = crud.get_kpis(db)
    assert kpis["total"] == 2
    assert kpis["closed"] == 1
    assert kpis["open"] == 1
    assert kpis["completion_rate"] == 50.0


def test_breakdowns_group_by_status_and_priority(db, asset):
    _make_wo(db, asset, priority="Highest")
    _make_wo(db, asset, priority="Highest")
    _make_wo(db, asset, priority="Low")

    breakdowns = crud.get_breakdowns(db)
    assert breakdowns["by_priority"]["Highest"] == 2
    assert breakdowns["by_priority"]["Low"] == 1
    assert breakdowns["by_status"]["Requested"] == 3


def test_breakdowns_by_team_only_counts_assigned_work_orders(db, asset, team, admin_user):
    unassigned = _make_wo(db, asset)  # noqa: F841 — never assigned, should not appear in by_team
    assigned = _make_wo(db, asset)
    crud.assign_work_order(db, assigned, schemas.AssignRequest(team_id=team.id), changed_by=admin_user.id)

    breakdowns = crud.get_breakdowns(db)
    assert breakdowns["by_team"].get(team.name) == 1
    assert sum(breakdowns["by_team"].values()) == 1


def test_highest_high_open_counts_only_open_highest_and_high(db, asset, admin_user):
    _make_wo(db, asset, priority="Highest")  # open, counts
    _make_wo(db, asset, priority="High")  # open, counts
    _make_wo(db, asset, priority="Medium")  # open, doesn't count (wrong priority)

    closed_highest = _make_wo(db, asset, priority="Highest")
    crud.change_status(
        db, closed_highest, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id
    )  # closed, doesn't count even though priority matches

    kpis = crud.get_kpis(db)
    assert kpis["highest_high_open"] == 2
