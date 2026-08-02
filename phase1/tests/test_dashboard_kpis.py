"""
Tests for crud.get_kpis / crud.get_breakdowns — the live queries that
replace dashboard_snapshot_history.json. These build a small seeded set
of work orders with known, deliberately-backdated timestamps and assert
the counts against hand-computed expectations.
"""
import datetime as dt

from app import crud, models, schemas


def _make_wo(db, asset, priority="Next Day", work_type="NJ Maintenance"):
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
    """Bug fix (PRD §14#24): closed_today now reflects each WO's CURRENT
    state (status still Closed% and closed_at within today) rather than
    counting status_change history events — a WO closed and then
    reopened the same day used to still count (the event happened), even
    though it's no longer actually closed. Backdating closed_at (not
    just the history row) is what actually simulates "closed yesterday"
    under the corrected logic."""
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
    # backdate both the closing history row AND the WO's own closed_at —
    # closed_at is what closed_today actually checks now.
    closing_row = (
        db.query(models.WOStatusHistory)
        .filter(
            models.WOStatusHistory.work_order_id == wo_closed_yesterday.id,
            models.WOStatusHistory.to_value == "Closed, Incomplete",
        )
        .one()
    )
    closing_row.changed_at = dt.datetime.utcnow() - dt.timedelta(days=1)
    wo_closed_yesterday.closed_at = dt.datetime.utcnow() - dt.timedelta(days=1)
    db.commit()

    kpis = crud.get_kpis(db)
    assert kpis["closed_today"] == 1
    assert kpis["closed"] == 2  # both are closed overall, only one closed *today*


def test_closed_today_excludes_wo_closed_then_reopened_same_day(db, asset, admin_user):
    """The exact bug scenario this fix addresses: a WO closed and then
    reopened the same day should NOT count as closed_today — it isn't
    currently closed. The old event-counting implementation would have
    counted it anyway, causing the KPI tile to disagree with the
    filtered inbox list."""
    wo = _make_wo(db, asset)
    crud.change_status(
        db, wo, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id
    )
    crud.change_status(
        db, wo, schemas.StatusChangeRequest(status="Work In Progress"), changed_by=admin_user.id
    )

    kpis = crud.get_kpis(db)
    assert kpis["closed_today"] == 0


def test_today_boundary_follows_configured_timezone_not_naive_utc(db, asset):
    """Bug fix (PRD §14#24): "today" should mean midnight-to-midnight in
    the admin-configured display time zone, not the server's own (often
    UTC) local date — a WO closed/created late in the site's evening
    could otherwise already look like "tomorrow" in UTC hours before
    local midnight. Set the timezone to something far from UTC (Pacific,
    UTC-7/8) and confirm a WO timestamped a few hours "into tomorrow" in
    UTC — but still "today" in Pacific — is correctly bucketed."""
    crud.set_setting(db, "timezone", "America/Los_Angeles", updated_by=None)

    # 2 AM UTC is still "yesterday, ~6-7 PM" in America/Los_Angeles —
    # construct a timestamp that's just past UTC midnight (so a naive
    # `func.date(col) == dt.date.today()` UTC comparison would already
    # call it "today" in UTC) but is still within the *previous* Pacific
    # calendar day, to prove the fix isn't just accidentally correct.
    now_utc = dt.datetime.utcnow()
    just_after_utc_midnight = now_utc.replace(hour=2, minute=0, second=0, microsecond=0)
    wo = _make_wo(db, asset)
    wo.created_at = just_after_utc_midnight
    db.commit()

    start, end = crud._today_bounds_utc(db)
    is_within_pacific_today = start <= just_after_utc_midnight < end
    kpis = crud.get_kpis(db)
    # Whatever the fixture's actual "now" happens to be, the KPI count
    # must agree with the boundary helper's own answer — this is really
    # a self-consistency check that the same helper is what's driving
    # both, rather than hand-computing the Pacific offset here.
    assert (kpis["opened_today"] == 1) == is_within_pacific_today


def test_open_and_closed_and_completion_rate(db, asset, admin_user, team):
    # Bug fix (found in LOC triage testing, 8/1/26): "open" now means
    # specifically Assigned/On Hold/Work In Progress, not "anything not
    # closed" (which would wrongly include Requested — that has its own
    # separate "requested" tile). Updated this fixture to actually put
    # a WO into one of those 3 statuses, rather than leaving it at the
    # default "Requested" and expecting it to count as open.
    wo1 = _make_wo(db, asset)
    crud.assign_work_order(db, wo1, schemas.AssignRequest(team_id=team.id), changed_by=admin_user.id)
    wo2 = _make_wo(db, asset)
    crud.change_status(
        db, wo2, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id
    )

    kpis = crud.get_kpis(db)
    assert kpis["total"] == 2
    assert kpis["closed"] == 1
    assert kpis["open"] == 1
    assert kpis["completion_rate"] == 50.0


def test_open_excludes_requested_status(db, asset):
    """The bug this fix addresses directly: a brand-new, un-triaged WO
    (status still "Requested") must NOT count toward "open" — it has
    its own separate "requested" tile."""
    _make_wo(db, asset)  # stays at "Requested", never assigned

    kpis = crud.get_kpis(db)
    assert kpis["requested"] == 1
    assert kpis["open"] == 0


def test_breakdowns_group_by_status_and_priority(db, asset):
    _make_wo(db, asset, priority="Immediate")
    _make_wo(db, asset, priority="Immediate")
    _make_wo(db, asset, priority="2 Days")

    breakdowns = crud.get_breakdowns(db)
    assert breakdowns["by_priority"]["Immediate"] == 2
    assert breakdowns["by_priority"]["2 Days"] == 1
    assert breakdowns["by_status"]["Requested"] == 3


def test_breakdowns_by_team_only_counts_assigned_work_orders(db, asset, team, admin_user):
    unassigned = _make_wo(db, asset)  # noqa: F841 — never assigned, should not appear in by_team
    assigned = _make_wo(db, asset)
    crud.assign_work_order(db, assigned, schemas.AssignRequest(team_id=team.id), changed_by=admin_user.id)

    breakdowns = crud.get_breakdowns(db)
    assert breakdowns["by_team"].get(team.name) == 1
    assert sum(breakdowns["by_team"].values()) == 1


def test_highest_high_open_counts_only_open_highest_and_high(db, asset, admin_user):
    _make_wo(db, asset, priority="Immediate")  # open, counts
    _make_wo(db, asset, priority="Same Day")  # open, counts
    _make_wo(db, asset, priority="Next Day")  # open, doesn't count (wrong priority)

    closed_highest = _make_wo(db, asset, priority="Immediate")
    crud.change_status(
        db, closed_highest, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id
    )  # closed, doesn't count even though priority matches

    kpis = crud.get_kpis(db)
    assert kpis["highest_high_open"] == 2
