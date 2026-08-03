"""
Tests for enhancement backlog Phase 26 (NJ2026_Work_Order_System_PRD.md
§17 follow-up, 8/2/26): program_viewer/basecamp_viewer — audience-
scoped, read-only dashboard roles.
"""
import pytest

from app import crud, schemas, models
from app.auth import hash_password


def _make_viewer(db, role, name="Viewer"):
    u = models.User(
        name=name, email=f"{role}@test.local",
        password_hash=hash_password("test-password"), role=role,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _login(client, email):
    resp = client.post("/auth/login", data={"username": email, "password": "test-password"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _asset(db, location_group, name):
    a = models.Asset(name=name, location_group=location_group)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _wo(db, asset):
    return crud.create_work_order(
        db, schemas.WorkOrderCreate(
            requester_name="Scout", requester_phone="555-0100",
            asset_id=asset.id, description="x", priority="Next Day",
        ),
    )


# ---- Role creation / management ----

def test_admin_can_create_program_viewer(client, auth_headers):
    resp = client.post(
        "/users", json={"name": "Pat", "email": "pat@test.local", "role": "program_viewer"},
        headers=auth_headers,
    )
    assert resp.status_code == 201


def test_admin_can_create_basecamp_viewer(client, auth_headers):
    resp = client.post(
        "/users", json={"name": "Sam", "email": "sam@test.local", "role": "basecamp_viewer"},
        headers=auth_headers,
    )
    assert resp.status_code == 201


def test_loc_cannot_create_program_viewer(client, db):
    loc = models.User(name="LOC", email="loc2@test.local", password_hash=hash_password("test-password"), role="loc")
    db.add(loc); db.commit(); db.refresh(loc)
    loc_headers = _login(client, loc.email)
    resp = client.post(
        "/users", json={"name": "Pat", "email": "pat2@test.local", "role": "program_viewer"},
        headers=loc_headers,
    )
    assert resp.status_code == 403


# ---- Dashboard scope forced server-side, can't be widened by the client ----

def test_program_viewer_kpis_forced_to_program_scope_regardless_of_param(client, db):
    program_asset = _asset(db, "Program Areas", "Program Asset A")
    other_asset = _asset(db, "Medical", "Medical Asset A")
    _wo(db, program_asset)
    _wo(db, other_asset)

    viewer = _make_viewer(db, "program_viewer")
    headers = _login(client, viewer.email)

    # Even asking for scope=main (everything), the server forces "program".
    resp = client.get("/dashboard/kpis", params={"scope": "main"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_basecamp_viewer_kpis_forced_to_basecamp_scope(client, db):
    basecamp_asset = _asset(db, "Base Camps", "Base Camp Asset A")
    other_asset = _asset(db, "Medical", "Medical Asset B")
    _wo(db, basecamp_asset)
    _wo(db, other_asset)

    viewer = _make_viewer(db, "basecamp_viewer")
    headers = _login(client, viewer.email)

    resp = client.get("/dashboard/kpis", params={"scope": "program"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_program_viewer_work_orders_list_forced_to_program_location_group(client, db):
    program_asset = _asset(db, "Program Areas", "Program Asset B")
    other_asset = _asset(db, "Medical", "Medical Asset C")
    program_wo = _wo(db, program_asset)
    other_wo = _wo(db, other_asset)

    viewer = _make_viewer(db, "program_viewer")
    headers = _login(client, viewer.email)

    # Try to widen the view by passing a different location_group.
    resp = client.get("/work-orders", params={"location_group": "Medical"}, headers=headers)
    ids = {w["id"] for w in resp.json()}
    assert ids == {program_wo.id}
    assert other_wo.id not in ids


# ---- Direct WO-id viewing also scoped ----

def test_program_viewer_can_view_wo_in_scope(client, db):
    program_asset = _asset(db, "Program Areas", "Program Asset C")
    wo = _wo(db, program_asset)
    viewer = _make_viewer(db, "program_viewer")
    headers = _login(client, viewer.email)
    resp = client.get(f"/work-orders/{wo.id}", headers=headers)
    assert resp.status_code == 200


def test_program_viewer_cannot_view_wo_outside_scope(client, db):
    other_asset = _asset(db, "Medical", "Medical Asset D")
    wo = _wo(db, other_asset)
    viewer = _make_viewer(db, "program_viewer")
    headers = _login(client, viewer.email)
    resp = client.get(f"/work-orders/{wo.id}", headers=headers)
    assert resp.status_code == 403


# ---- Genuinely read-only: mutations blocked even within scope ----

def test_program_viewer_cannot_change_status_even_within_scope(client, db):
    program_asset = _asset(db, "Program Areas", "Program Asset D")
    wo = _wo(db, program_asset)
    viewer = _make_viewer(db, "program_viewer")
    headers = _login(client, viewer.email)
    resp = client.post(f"/work-orders/{wo.id}/status", json={"status": "Assigned"}, headers=headers)
    assert resp.status_code == 403


def test_program_viewer_cannot_add_note_even_within_scope(client, db):
    program_asset = _asset(db, "Program Areas", "Program Asset E")
    wo = _wo(db, program_asset)
    viewer = _make_viewer(db, "program_viewer")
    headers = _login(client, viewer.email)
    resp = client.post(
        f"/work-orders/{wo.id}/notes", json={"note_text": "hi", "note_type": "internal"}, headers=headers
    )
    assert resp.status_code == 403


def test_program_viewer_cannot_assign(client, db):
    program_asset = _asset(db, "Program Areas", "Program Asset F")
    wo = _wo(db, program_asset)
    viewer = _make_viewer(db, "program_viewer")
    headers = _login(client, viewer.email)
    resp = client.post(f"/work-orders/{wo.id}/assign", json={"team_id": 1}, headers=headers)
    assert resp.status_code == 403


def test_program_viewer_cannot_create_work_order(client, db):
    viewer = _make_viewer(db, "program_viewer")
    headers = _login(client, viewer.email)
    resp = client.post(
        "/work-orders",
        json={"requester_name": "Scout", "asset_id": 1, "description": "x", "priority": "Next Day"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_basecamp_viewer_scoped_independently_from_program_viewer(client, db):
    """Confirms the two roles don't accidentally share scope."""
    program_asset = _asset(db, "Program Areas", "Program Asset G")
    basecamp_asset = _asset(db, "Base Camps", "Base Camp Asset G")
    program_wo = _wo(db, program_asset)
    basecamp_wo = _wo(db, basecamp_asset)

    basecamp_viewer = _make_viewer(db, "basecamp_viewer")
    headers = _login(client, basecamp_viewer.email)

    resp = client.get(f"/work-orders/{program_wo.id}", headers=headers)
    assert resp.status_code == 403
    resp2 = client.get(f"/work-orders/{basecamp_wo.id}", headers=headers)
    assert resp2.status_code == 200
