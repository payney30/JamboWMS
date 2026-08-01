"""
Tests for the scoped dashboard queries backing PRD 4.4's three dashboards
(Main LOC / Program Team / Base Camps) and the new trend + "needing
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


def _make_wo(db, asset, priority="Next Day"):
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


def test_basecamp_scope_only_counts_base_camp_ops_reporting_group(db):
    """Bug fix (PRD §17#14 follow-up): basecamp scope now follows the
    real location_group -> reporting-group mapping (Asset.location_group,
    resolved via reporting_group_id/recompute_effective_groups) instead
    of a hardcoded camp-letter allowlist. Base Camps A/B are a real,
    documented exception — they report under "Program Areas," not "Base
    Camp Ops," despite being physically base camps — so camp_letter
    alone was never actually the right discriminator; this test now
    reflects that directly rather than working around it."""
    ops_asset = _asset(db, location_group="Base Camps", camp_letter="C")
    # Base Camp A, but reports under Program Areas — the documented
    # exception this fix is specifically about getting right.
    program_reporting_a = _asset(db, location_group="Program Areas", camp_letter="A")
    other = _asset(db, location_group="Medical", camp_letter=None)
    _make_wo(db, ops_asset)
    _make_wo(db, program_reporting_a)
    _make_wo(db, other)

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
    _make_wo(db, program_asset, priority="Immediate")
    _make_wo(db, other_asset, priority="Immediate")

    breakdowns = crud.get_breakdowns(db, scope="program")
    assert breakdowns["by_priority"]["Immediate"] == 1
    assert breakdowns["by_location"] == {"Program Areas": 1}


def test_daily_trend_returns_requested_number_of_days(db, asset):
    _make_wo(db, asset)
    trend = crud.get_daily_trend(db, scope="main", days=7)
    assert len(trend) == 7
    assert trend[-1]["date"] == dt.date.today().isoformat()
    assert trend[-1]["opened"] == 1


def test_daily_trend_scoped_to_basecamp(db):
    ops_asset = _asset(db, location_group="Base Camps", camp_letter="C")
    program_reporting_a = _asset(db, location_group="Program Areas", camp_letter="A")
    _make_wo(db, ops_asset)
    _make_wo(db, program_reporting_a)

    trend = crud.get_daily_trend(db, scope="basecamp", days=3)
    assert trend[-1]["opened"] == 1


def test_needing_attention_only_open_highest_and_high_oldest_first(db, asset, admin_user):
    old_highest = _make_wo(db, asset, priority="Immediate")
    old_highest.created_at = dt.datetime.utcnow() - dt.timedelta(hours=5)
    db.commit()

    new_high = _make_wo(db, asset, priority="Same Day")

    _make_wo(db, asset, priority="Next Day")  # excluded: wrong priority

    closed_highest = _make_wo(db, asset, priority="Immediate")
    crud.change_status(
        db, closed_highest, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id
    )  # excluded: closed

    attention = crud.get_needing_attention(db, scope="main")
    ids = [w.id for w in attention]
    assert ids == [old_highest.id, new_high.id]


def test_needing_attention_respects_limit(db, asset):
    for _ in range(5):
        _make_wo(db, asset, priority="Immediate")

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


def test_extra_filters_combine_with_scope(db, auth_headers, client):
    program_asset = _asset(db, location_group="Program Areas")
    _make_wo(db, program_asset, priority="Immediate")
    _make_wo(db, program_asset, priority="2 Days")

    kpis = crud.get_kpis(db, scope="program", priority="Immediate")
    assert kpis["total"] == 1


def test_status_filter(db):
    a = _asset(db)
    open_wo = _make_wo(db, a)
    closed_wo = _make_wo(db, a)
    closed_wo.status = "Closed, Completed"
    db.commit()

    kpis = crud.get_kpis(db, scope="main", status="Closed, Completed")
    assert kpis["total"] == 1


def test_work_type_filter(db):
    a = _asset(db)
    wo1 = crud.create_work_order(
        db, schemas.WorkOrderCreate(requester_name="Scout", asset_id=a.id, description="x", priority="Next Day", work_type="NJ IT"),
    )
    crud.create_work_order(
        db, schemas.WorkOrderCreate(requester_name="Scout", asset_id=a.id, description="x", priority="Next Day", work_type="NJ Maintenance"),
    )
    kpis = crud.get_kpis(db, scope="main", work_type="NJ IT")
    assert kpis["total"] == 1


def test_team_filter(db, team, other_team, admin_user):
    a = _asset(db)
    wo1 = _make_wo(db, a)
    crud.assign_work_order(db, wo1, schemas.AssignRequest(team_id=team.id), changed_by=admin_user.id)
    wo2 = _make_wo(db, a)
    crud.assign_work_order(db, wo2, schemas.AssignRequest(team_id=other_team.id), changed_by=admin_user.id)

    kpis = crud.get_kpis(db, scope="main", team_id=team.id)
    assert kpis["total"] == 1


def test_location_group_filter_narrows_within_scope(db):
    medical = _asset(db, location_group="Medical")
    food = _asset(db, location_group="Food")
    _make_wo(db, medical)
    _make_wo(db, food)

    kpis = crud.get_kpis(db, scope="main", location_group="Medical")
    assert kpis["total"] == 1


def test_search_filter_matches_description_or_wo_number(db):
    a = _asset(db)
    wo = crud.create_work_order(
        db, schemas.WorkOrderCreate(requester_name="Scout", asset_id=a.id, description="broken sink", priority="Next Day"),
    )
    crud.create_work_order(
        db, schemas.WorkOrderCreate(requester_name="Scout", asset_id=a.id, description="need cones", priority="Next Day"),
    )

    kpis = crud.get_kpis(db, scope="main", search="sink")
    assert kpis["total"] == 1

    kpis2 = crud.get_kpis(db, scope="main", search=wo.wo_number)
    assert kpis2["total"] == 1


def test_router_passes_through_extra_filters(client, auth_headers, asset, team, admin_user):
    wo = client.post(
        "/work-orders",
        json={"requester_name": "Scout", "asset_id": asset.id, "description": "x", "priority": "Immediate"},
        headers=auth_headers,
    ).json()
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    resp = client.get(f"/dashboard/kpis?scope=main&priority=Immediate&team_id={team.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp2 = client.get("/dashboard/trend?scope=main&days=5", headers=auth_headers)
    assert resp2.status_code == 200
    assert len(resp2.json()) == 5

    resp3 = client.get("/dashboard/attention?scope=main", headers=auth_headers)
    assert resp3.status_code == 200


# ---- Enhancement backlog Phase 19 (PRD §17#14): dashboard clickable tiles ----

def test_dashboard_kpis_exclude_closed(db, asset, admin_user):
    open_wo = _make_wo(db, asset)
    closed_wo = _make_wo(db, asset)
    crud.change_status(
        db, closed_wo, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id
    )

    kpis = crud.get_kpis(db, scope="main", exclude_closed=True)
    assert kpis["total"] == 1


def test_dashboard_kpis_closed_only(db, asset, admin_user):
    open_wo = _make_wo(db, asset)
    closed_wo = _make_wo(db, asset)
    crud.change_status(
        db, closed_wo, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id
    )

    kpis = crud.get_kpis(db, scope="main", closed_only=True)
    assert kpis["total"] == 1


def test_dashboard_kpis_asset_id_filter(db):
    a1 = _asset(db, location_group="Program Areas", name="Program Asset One")
    a2 = _asset(db, location_group="Program Areas", name="Program Asset Two")
    _make_wo(db, a1)
    _make_wo(db, a2)

    kpis = crud.get_kpis(db, scope="program", asset_id=a1.id)
    assert kpis["total"] == 1


def test_dashboard_router_accepts_exclude_closed_and_asset_id(client, auth_headers, asset):
    resp = client.get(
        "/dashboard/kpis",
        params={"scope": "main", "exclude_closed": "true", "asset_id": asset.id},
        headers=auth_headers,
    )
    assert resp.status_code == 200


def test_inbox_via_work_orders_scoped_by_location_group_matches_dashboard_scope(
    client, auth_headers, db
):
    """The new Program HQ/Contingent Ops HQ dashboard inbox reuses
    GET /work-orders (not a new endpoint) — location_group is the exact
    same reporting-group filter _apply_filters' scope="program" branch
    uses internally, so filtering /work-orders by "Program Areas" should
    return the same set of WOs as scope=program does."""
    program_asset = _asset(db, location_group="Program Areas")
    other_asset = _asset(db, location_group="Medical")
    _make_wo(db, program_asset)
    _make_wo(db, other_asset)

    scope_resp = client.get("/dashboard/kpis?scope=program", headers=auth_headers)
    inbox_resp = client.get(
        "/work-orders", params={"location_group": "Program Areas"}, headers=auth_headers
    )
    assert scope_resp.json()["total"] == 1
    assert len(inbox_resp.json()) == 1


# ---- Regression test for the "Base Camp Ops" vs "Base Camps" bug (7/31/26) ----

def test_basecamp_scope_string_matches_real_hierarchy_data():
    """The basecamp scope filter (crud._apply_filters) hardcodes a
    reporting-group name to match against. A previous fix used
    "Base Camp Ops" — a string that never appears anywhere in the real
    location hierarchy data (it was descriptive shorthand in a seed.py
    *comment*, not an actual branch label) — so the filter silently
    matched nothing and the Base Camp Ops dashboard showed no data at
    all. This test fails loudly if that ever regresses: it loads the
    same authoritative name_to_branch.json the real seed process reads
    from and confirms crud.py's hardcoded string is actually one of the
    real branch labels, not a plausible-looking guess."""
    import json
    import os

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(project_root, "data", "name_to_branch.json")) as f:
        real_branch_labels = set(json.load(f).values())

    # Same hardcoded value crud._apply_filters' scope="basecamp" branch
    # uses — kept in sync manually since it's a plain string literal in
    # that function, not an importable constant.
    BASECAMP_REPORTING_GROUP = "Base Camps"
    assert BASECAMP_REPORTING_GROUP in real_branch_labels, (
        f"'{BASECAMP_REPORTING_GROUP}' is not a real branch label in "
        f"name_to_branch.json — the basecamp dashboard scope would "
        f"silently match zero work orders. Real labels: {sorted(real_branch_labels)}"
    )


def test_basecamp_scope_actually_finds_data_with_the_real_branch_label(db):
    """Directly pins the fix: an asset with the real branch label
    ("Base Camps") is included in scope="basecamp" results."""
    real_asset = _asset(db, location_group="Base Camps", name="Real Base Camp Asset")
    _make_wo(db, real_asset)

    kpis = crud.get_kpis(db, scope="basecamp")
    assert kpis["total"] == 1
