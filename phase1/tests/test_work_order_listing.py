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
    low_old = _make_wo(db, asset, "2 Days", now - dt.timedelta(hours=5))
    high_new = _make_wo(db, asset, "Same Day", now - dt.timedelta(hours=1))
    highest_old = _make_wo(db, asset, "Immediate", now - dt.timedelta(hours=4))
    highest_new = _make_wo(db, asset, "Immediate", now - dt.timedelta(hours=2))
    medium = _make_wo(db, asset, "Next Day", now - dt.timedelta(hours=3))

    ordered = crud.list_work_orders(db)
    ids_in_order = [wo.id for wo in ordered]

    expected = [highest_old.id, highest_new.id, high_new.id, medium.id, low_old.id]
    assert ids_in_order == expected


def test_opened_today_survives_a_low_limit_even_when_sorted_past_it(db, asset):
    """Regression test for the actual 'Opened Today shows 1, inbox shows 0'
    bug: the quick view used to be applied client-side against a page
    capped by `limit`. With the default priority-then-oldest sort, a
    brand-new *Low* priority WO sorts dead last — behind any older
    same-or-higher-priority WOs — so a small limit could return a page
    that never contained it at all, and the client-side filter had
    nothing to find. The fix moves this filter into SQL so pagination is
    applied *after* filtering, not before."""
    now = dt.datetime.utcnow()
    # Twelve older Highest-priority WOs, backdated by full days so they're
    # reliably NOT "today" no matter what time this test happens to run —
    # they just need to sort ahead of the new Low-priority one below.
    for i in range(12):
        _make_wo(db, asset, "Immediate", now - dt.timedelta(days=2, hours=i))

    todays_low_priority_wo = _make_wo(db, asset, "2 Days", now)

    # A limit far smaller than 12 — the old client-side-filter approach
    # would never even see todays_low_priority_wo in the fetched page.
    results = crud.list_work_orders(db, opened_today=True, limit=5)
    assert [w.id for w in results] == [todays_low_priority_wo.id]


def test_closed_today_filter(db, asset, admin_user):
    wo = _make_wo(db, asset, "Next Day", dt.datetime.utcnow())
    crud.change_status(db, wo, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id)

    other_closed_yesterday = _make_wo(db, asset, "Next Day", dt.datetime.utcnow() - dt.timedelta(days=2))
    crud.change_status(db, other_closed_yesterday, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id)
    other_closed_yesterday.closed_at = dt.datetime.utcnow() - dt.timedelta(days=1)
    db.commit()

    results = crud.list_work_orders(db, closed_today=True)
    assert [w.id for w in results] == [wo.id]


def test_exclude_closed_and_closed_only_filters(db, asset, admin_user):
    open_wo = _make_wo(db, asset, "Next Day", dt.datetime.utcnow())
    closed_wo = _make_wo(db, asset, "Next Day", dt.datetime.utcnow())
    crud.change_status(db, closed_wo, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id)

    open_results = crud.list_work_orders(db, exclude_closed=True)
    assert [w.id for w in open_results] == [open_wo.id]

    closed_results = crud.list_work_orders(db, closed_only=True)
    assert [w.id for w in closed_results] == [closed_wo.id]


def test_priority_in_filter(db, asset):
    highest = _make_wo(db, asset, "Immediate", dt.datetime.utcnow())
    high = _make_wo(db, asset, "Same Day", dt.datetime.utcnow())
    _make_wo(db, asset, "Next Day", dt.datetime.utcnow())

    results = crud.list_work_orders(db, priority_in=["Immediate", "Same Day"])
    ids = {w.id for w in results}
    assert ids == {highest.id, high.id}


def test_status_in_filter(db, asset, admin_user, team):
    """Bug fix (found in LOC triage testing, 8/1/26): backs the
    "Open/Active" tile's click-to-filter — Assigned/On Hold/Work In
    Progress specifically, not "everything not closed" (which would
    wrongly include "Requested")."""
    requested = _make_wo(db, asset, "Next Day", dt.datetime.utcnow())
    assigned = _make_wo(db, asset, "Next Day", dt.datetime.utcnow())
    crud.assign_work_order(db, assigned, schemas.AssignRequest(team_id=team.id), changed_by=admin_user.id)
    closed = _make_wo(db, asset, "Next Day", dt.datetime.utcnow())
    crud.change_status(
        db, closed, schemas.StatusChangeRequest(status="Closed, Completed"), changed_by=admin_user.id
    )

    results = crud.list_work_orders(db, status_in=["Assigned", "On Hold", "Work In Progress"])
    ids = {w.id for w in results}
    assert ids == {assigned.id}
    assert requested.id not in ids
    assert closed.id not in ids


def test_router_status_in_param(client, auth_headers, asset, team):
    requested = client.post(
        "/work-orders",
        json={"requester_name": "Scout", "asset_id": asset.id, "description": "x", "priority": "Next Day"},
        headers=auth_headers,
    ).json()
    assigned = client.post(
        "/work-orders",
        json={"requester_name": "Scout", "asset_id": asset.id, "description": "x", "priority": "Next Day"},
        headers=auth_headers,
    ).json()
    client.post(f"/work-orders/{assigned['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    resp = client.get(
        "/work-orders", params={"status_in": "Assigned,On Hold,Work In Progress"}, headers=auth_headers
    )
    ids = {w["id"] for w in resp.json()}
    assert ids == {assigned["id"]}
    assert requested["id"] not in ids


def test_router_opened_today_param(client, auth_headers, asset):
    now_wo = client.post(
        "/work-orders",
        json={"requester_name": "Scout", "asset_id": asset.id, "description": "x", "priority": "2 Days"},
        headers=auth_headers,
    ).json()

    resp = client.get("/work-orders?opened_today=true", headers=auth_headers)
    assert resp.status_code == 200
    ids = [w["id"] for w in resp.json()]
    assert now_wo["id"] in ids


def test_router_priority_in_param(client, auth_headers, asset):
    highest = client.post(
        "/work-orders",
        json={"requester_name": "Scout", "asset_id": asset.id, "description": "x", "priority": "Immediate"},
        headers=auth_headers,
    ).json()
    medium = client.post(
        "/work-orders",
        json={"requester_name": "Scout", "asset_id": asset.id, "description": "x", "priority": "Next Day"},
        headers=auth_headers,
    ).json()

    resp = client.get("/work-orders", params={"priority_in": "Immediate,Same Day"}, headers=auth_headers)
    ids = [w["id"] for w in resp.json()]
    assert highest["id"] in ids
    assert medium["id"] not in ids


# ---- Enhancement backlog Phase 24 (found in Dispatcher/CSV testing, 8/1/26) ----

def test_list_endpoint_exposes_requester_and_poc_fields(client, auth_headers, asset):
    """Bug fix: these were only ever on WorkOrderDetail, so the CSV
    exports (which use the list endpoint) couldn't include them without
    a detail request per row."""
    resp = client.post(
        "/work-orders",
        json={
            "requester_name": "Scout Leader", "requester_phone": "555-0100",
            "asset_id": asset.id, "description": "x", "priority": "Next Day",
            "poc_is_requester": False, "poc_name": "Camp Director", "poc_phone": "555-0200",
        },
        headers=auth_headers,
    )
    wo_id = resp.json()["id"]

    listed = client.get("/work-orders", headers=auth_headers).json()
    row = next(w for w in listed if w["id"] == wo_id)
    assert row["requester_name"] == "Scout Leader"
    assert row["requester_phone"] == "555-0100"
    assert row["poc_is_requester"] is False
    assert row["poc_name"] == "Camp Director"
    assert row["poc_phone"] == "555-0200"


def test_list_endpoint_exposes_closed_at(client, auth_headers, wo_payload):
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    listed = client.get("/work-orders", headers=auth_headers).json()
    row = next(w for w in listed if w["id"] == wo["id"])
    assert row["closed_at"] is None

    client.post(f"/work-orders/{wo['id']}/status", json={"status": "Closed, Completed", "note": "done"}, headers=auth_headers)
    listed2 = client.get("/work-orders", headers=auth_headers).json()
    row2 = next(w for w in listed2 if w["id"] == wo["id"])
    assert row2["closed_at"] is not None


def test_list_endpoint_exposes_assigned_person(client, auth_headers, wo_payload, team, db):
    worker, _ = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id, "person_id": worker.id}, headers=auth_headers)

    listed = client.get("/work-orders", headers=auth_headers).json()
    row = next(w for w in listed if w["id"] == wo["id"])
    assert row["assigned_person"]["name"] == "Riley"


def test_unassigned_person_filter(client, auth_headers, wo_payload, team, db):
    worker, _ = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    tasked = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{tasked['id']}/assign", json={"team_id": team.id, "person_id": worker.id}, headers=auth_headers)
    not_tasked = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{not_tasked['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    resp = client.get("/work-orders", params={"unassigned_person": "true", "team_id": team.id}, headers=auth_headers)
    ids = {w["id"] for w in resp.json()}
    assert not_tasked["id"] in ids
    assert tasked["id"] not in ids
