"""
Tests for the scoped dashboard queries backing PRD 4.4's three dashboards
(Main LOC / Program Team / Base Camp Ops) and the new trend + "needing
attention" endpoints that replace the old dashboard_snapshot_history.json
pipeline.
"""
import datetime as dt

from app import crud, models, schemas


def _asset(db, location_group="Branch A", camp_letter=None, name=None):
    a = models.Asset(
        name=name or f"Asset-{location_group}-{camp_letter}",
        location_group=location_group,
        camp_letter=camp_letter,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _make_wo(db, asset, priority="Medium"):
    return crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority=priority,
        ),
    )


def test_program_scope_only_counts_program_areas(db):
    program_asset = _asset(db, location_group="Program Areas")
    other_asset = _asset(db, location_group="Base Camps", camp_letter="C")
    _make_wo(db, program_asset)
    _make_wo(db, other_asset)

    kpis = crud.get_kpis(db, scope="program")
    assert kpis["total"] == 1


def test_basecamp_scope_only_counts_charlie_delta_echo(db):
    charlie = _asset(db, location_group="Base Camps", camp_letter="C")
    alpha = _asset(db, location_group="Base Camps", camp_letter="A")
    no_letter = _asset(db, location_group="Base Camps", camp_letter=None)
    _make_wo(db, charlie)
    _make_wo(db, alpha)
    _make_wo(db, no_letter)

    kpis = crud.get_kpis(db, scope="basecamp")
    assert kpis["total"] == 1


def test_main_scope_is_unfiltered(db):
    a = _asset(db, location_group="Program Areas")
    b = _asset(db, location_group="Base Camps", camp_letter="A")
    _make_wo(db, a)
    _make_wo(db, b)

    kpis = crud.get_kpis(db, scope="main")
    assert kpis["total"] == 2


def test_breakdowns_respect_scope(db):
    program_asset = _asset(db, location_group="Program Areas")
    other_asset = _asset(db, location_group="Base Camps", camp_letter="A")
    _make_wo(db, program_asset, priority="Highest")
    _make_wo(db, other_asset, priority="Highest")

    breakdowns = crud.get_breakdowns(db, scope="program")
    assert breakdowns["by_priority"]["Highest"] == 1
    assert breakdowns["by_location"] == {"Program Areas": 1}


def test_daily_trend_returns_requested_number_of_days(db, asset):
    _make_wo(db, asset)
    trend = crud.get_daily_trend(db, scope="main", days=7)
    assert len(trend) == 7
    assert trend[-1]["date"] == dt.date.today().isoformat()
    assert trend[-1]["opened"] == 1


def test_daily_trend_scoped_to_basecamp(db):
    charlie = _asset(db, location_group="Base Camps", camp_letter="C")
    alpha = _asset(db, location_group="Base Camps", camp_letter="A")
    _make_wo(db, charlie)
    _make_wo(db, alpha)

    trend = crud.get_daily_trend(db, scope="basecamp", days=3)
    assert trend[-1]["opened"] == 1


def test_needing_attention_only_open_highest_and_high_oldest_first(db, asset, admin_user):
    old_highest = _make_wo(db, asset, priority="Highest")
    old_highest.created_at = dt.datetime.utcnow() - dt.timedelta(hours=5)
    db.commit()

    new_high = _make_wo(db, asset, priority="High")

    _make_wo(db, asset, priority="Medium")  # excluded: wrong priority

    closed_highest = _make_wo(db, asset, priority="Highest")
    crud.change_status(
        db, closed_highest, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id
    )  # excluded: closed

    attention = crud.get_needing_attention(db, scope="main")
    ids = [w.id for w in attention]
    assert ids == [old_highest.id, new_high.id]


def test_needing_attention_respects_limit(db, asset):
    for _ in range(5):
        _make_wo(db, asset, priority="Highest")

    attention = crud.get_needing_attention(db, scope="main", limit=2)
    assert len(attention) == 2


def test_dashboard_router_scope_endpoints(client, auth_headers, asset):
    resp = client.get("/dashboard/kpis?scope=program", headers=auth_headers)
    assert resp.status_code == 200

    resp2 = client.get("/dashboard/trend?scope=main&days=5", headers=auth_headers)
    assert resp2.status_code == 200
    assert len(resp2.json()) == 5

    resp3 = client.get("/dashboard/attention?scope=main", headers=auth_headers)
    assert resp3.status_code == 200
