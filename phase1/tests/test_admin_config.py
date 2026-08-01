"""
Tests for app/routers/admin.py — the admin configuration screens (PRD
4.5): location hierarchy CRUD/reparenting (4.5a), reporting groups
(4.5b), request types (4.5c), and teams (4.5d). Everything here is
admin-only — a couple of tests confirm 'loc' (which CAN reach user
management) is correctly locked out of these.
"""
import pytest


def _login(client, email, password="test-password"):
    resp = client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---- Access control ----

def test_loc_cannot_reach_admin_endpoints(client, loc_user):
    headers = _login(client, loc_user.email)
    resp = client.get("/admin/assets", headers=headers)
    assert resp.status_code == 403


def test_tech_cannot_reach_admin_endpoints(client, tech_user):
    headers = _login(client, tech_user.email)
    resp = client.get("/admin/teams", headers=headers)
    assert resp.status_code == 403


def test_unauthenticated_cannot_reach_admin_endpoints(client):
    resp = client.get("/admin/reporting-groups")
    assert resp.status_code == 401


# ---- 4.5a: Location hierarchy ----

def test_create_and_list_asset(client, auth_headers):
    resp = client.post("/admin/assets", json={"name": "New Shower House"}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "New Shower House"
    assert body["parent_id"] is None
    assert body["depth"] == 0

    listed = client.get("/admin/assets", headers=auth_headers).json()
    assert any(a["name"] == "New Shower House" for a in listed)


def test_create_asset_duplicate_name_rejected(client, auth_headers, asset):
    resp = client.post("/admin/assets", json={"name": asset.name}, headers=auth_headers)
    assert resp.status_code == 400


def test_create_asset_with_missing_parent_404s(client, auth_headers):
    resp = client.post("/admin/assets", json={"name": "Orphan", "parent_id": 99999}, headers=auth_headers)
    assert resp.status_code == 404


def test_reparent_asset(client, auth_headers, db):
    from app import models
    parent = models.Asset(name="Branch Root", location_group="Branch Root")
    child = models.Asset(name="Leaf Node", location_group="Branch Root")
    db.add_all([parent, child])
    db.commit()
    db.refresh(parent)
    db.refresh(child)

    resp = client.patch(
        f"/admin/assets/{child.id}", json={"parent_id": parent.id}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["parent_id"] == parent.id
    assert body["depth"] == 1


def test_reparent_cannot_create_a_cycle(client, auth_headers, db):
    from app import models
    root = models.Asset(name="Root", location_group="Root")
    db.add(root)
    db.commit()
    db.refresh(root)
    child_resp = client.post(
        "/admin/assets", json={"name": "Child", "parent_id": root.id}, headers=auth_headers
    )
    child_id = child_resp.json()["id"]

    # Try to make Root a child of its own child — must be rejected.
    resp = client.patch(f"/admin/assets/{root.id}", json={"parent_id": child_id}, headers=auth_headers)
    assert resp.status_code == 400


def test_asset_cannot_be_its_own_parent(client, auth_headers, asset):
    resp = client.patch(f"/admin/assets/{asset.id}", json={"parent_id": asset.id}, headers=auth_headers)
    assert resp.status_code == 400


def test_deactivate_with_active_children_requires_cascade_confirm(client, auth_headers, db):
    from app import models
    parent = models.Asset(name="Parent With Kids", location_group="X")
    db.add(parent)
    db.commit()
    db.refresh(parent)
    client.post("/admin/assets", json={"name": "Kid", "parent_id": parent.id}, headers=auth_headers)

    resp = client.patch(f"/admin/assets/{parent.id}", json={"is_active": False}, headers=auth_headers)
    assert resp.status_code == 409

    resp2 = client.patch(
        f"/admin/assets/{parent.id}",
        json={"is_active": False, "cascade_deactivate": True},
        headers=auth_headers,
    )
    assert resp2.status_code == 200
    listed = {a["name"]: a for a in client.get("/admin/assets", headers=auth_headers).json()}
    assert listed["Kid"]["is_active"] is False


def test_asset_change_log_records_edits(client, auth_headers, asset):
    client.patch(f"/admin/assets/{asset.id}", json={"code": "NEW-CODE"}, headers=auth_headers)
    resp = client.get(f"/admin/assets/{asset.id}/history", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()
    assert any(e["field_changed"] == "code" and e["to_value"] == "NEW-CODE" for e in entries)


# ---- 4.5b: Reporting groups (inheritance) ----

def test_reporting_group_assignment_cascades_to_children(client, auth_headers, db):
    from app import models
    parent = models.Asset(name="Base Camp A", location_group="Base Camps")
    child = models.Asset(name="Base Camp A Shower House", location_group="Base Camps")
    db.add_all([parent, child])
    db.commit()
    db.refresh(parent)
    db.refresh(child)
    child.parent_id = parent.id
    db.commit()

    rg_resp = client.post("/admin/reporting-groups", json={"name": "Program Areas"}, headers=auth_headers)
    assert rg_resp.status_code == 201
    rg_id = rg_resp.json()["id"]

    client.patch(f"/admin/assets/{parent.id}", json={"reporting_group_id": rg_id}, headers=auth_headers)

    listed = {a["name"]: a for a in client.get("/admin/assets", headers=auth_headers).json()}
    assert listed["Base Camp A"]["effective_reporting_group"] == "Program Areas"
    # child inherits — no explicit override of its own, but its effective
    # value follows the parent's new assignment
    assert listed["Base Camp A Shower House"]["effective_reporting_group"] == "Program Areas"
    assert listed["Base Camp A Shower House"]["reporting_group_id"] is None


def test_child_override_blocks_inheritance(client, auth_headers, db):
    from app import models
    parent = models.Asset(name="Base Camp B", location_group="Base Camps")
    child = models.Asset(name="Base Camp B Motorpool", location_group="Base Camps")
    db.add_all([parent, child])
    db.commit()
    db.refresh(parent)
    db.refresh(child)
    child.parent_id = parent.id
    db.commit()

    program = client.post("/admin/reporting-groups", json={"name": "Program Areas"}, headers=auth_headers).json()
    logistics = client.post("/admin/reporting-groups", json={"name": "Logistics"}, headers=auth_headers).json()

    client.patch(f"/admin/assets/{parent.id}", json={"reporting_group_id": program["id"]}, headers=auth_headers)
    client.patch(f"/admin/assets/{child.id}", json={"reporting_group_id": logistics["id"]}, headers=auth_headers)

    listed = {a["name"]: a for a in client.get("/admin/assets", headers=auth_headers).json()}
    assert listed["Base Camp B"]["effective_reporting_group"] == "Program Areas"
    assert listed["Base Camp B Motorpool"]["effective_reporting_group"] == "Logistics"


def test_reporting_group_rename_updates_effective_display(client, auth_headers, db):
    from app import models
    node = models.Asset(name="Some Camp", location_group="Old Name")
    db.add(node)
    db.commit()
    db.refresh(node)

    rg = client.post("/admin/reporting-groups", json={"name": "Old Name"}, headers=auth_headers).json()
    client.patch(f"/admin/assets/{node.id}", json={"reporting_group_id": rg["id"]}, headers=auth_headers)
    client.patch(f"/admin/reporting-groups/{rg['id']}", json={"name": "New Name"}, headers=auth_headers)

    listed = {a["name"]: a for a in client.get("/admin/assets", headers=auth_headers).json()}
    assert listed["Some Camp"]["effective_reporting_group"] == "New Name"


def test_reporting_group_duplicate_name_rejected(client, auth_headers):
    client.post("/admin/reporting-groups", json={"name": "Medical"}, headers=auth_headers)
    resp = client.post("/admin/reporting-groups", json={"name": "Medical"}, headers=auth_headers)
    assert resp.status_code == 400


# ---- 4.5c: Request types ----

def test_create_request_type_and_use_it_on_public_submission(client, auth_headers, asset):
    resp = client.post("/admin/request-types", json={"name": "NJ Security"}, headers=auth_headers)
    assert resp.status_code == 201

    submit = client.post(
        "/public/work-orders",
        data={
            "requester_name": "A Scout",
            "requester_email": "a@example.com",
            "requester_phone": "555-0100",
            "asset_id": str(asset.id),
            "work_type": "NJ Security",
            "description": "Suspicious activity near the gate",
            "priority": "Same Day",
            "poc_is_requester": "true",
            "website": "",
        },
    )
    assert submit.status_code == 201, submit.text


def test_inactive_request_type_rejected_on_submission(client, auth_headers, asset):
    created = client.post("/admin/request-types", json={"name": "NJ Retired Type"}, headers=auth_headers).json()
    client.patch(f"/admin/request-types/{created['id']}", json={"is_active": False}, headers=auth_headers)

    submit = client.post(
        "/public/work-orders",
        data={
            "requester_name": "A Scout",
            "requester_email": "a@example.com",
            "requester_phone": "555-0100",
            "asset_id": str(asset.id),
            "work_type": "NJ Retired Type",
            "description": "Something",
            "priority": "Same Day",
            "poc_is_requester": "true",
            "website": "",
        },
    )
    assert submit.status_code == 400


def test_blank_work_type_still_valid_without_a_request_type_row(client, asset):
    submit = client.post(
        "/public/work-orders",
        data={
            "requester_name": "A Scout",
            "requester_email": "a@example.com",
            "requester_phone": "555-0100",
            "asset_id": str(asset.id),
            "work_type": "",
            "description": "Not sure what kind of issue this is",
            "priority": "Next Day",
            "poc_is_requester": "true",
            "website": "",
        },
    )
    assert submit.status_code == 201, submit.text


def test_request_type_duplicate_name_rejected(client, auth_headers):
    client.post("/admin/request-types", json={"name": "NJ Catering"}, headers=auth_headers)
    resp = client.post("/admin/request-types", json={"name": "NJ Catering"}, headers=auth_headers)
    assert resp.status_code == 400


# ---- 4.5d: Teams ----

def test_create_and_rename_team(client, auth_headers):
    created = client.post("/admin/teams", json={"name": "2026 Jamboree Security"}, headers=auth_headers)
    assert created.status_code == 201
    team_id = created.json()["id"]

    renamed = client.patch(f"/admin/teams/{team_id}", json={"name": "2026 Jamboree Safety & Security"}, headers=auth_headers)
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "2026 Jamboree Safety & Security"


def test_deactivate_team_with_open_wo_requires_confirm(client, auth_headers, db, asset):
    from app import models
    team = models.Team(name="Team With Open WO")
    db.add(team)
    db.commit()
    db.refresh(team)
    wo = models.WorkOrder(
        wo_number="90001", requester_name="X", asset_id=asset.id,
        description="test", priority="Next Day", status="Assigned", assigned_team_id=team.id,
    )
    db.add(wo)
    db.commit()

    resp = client.patch(f"/admin/teams/{team.id}", json={"is_active": False}, headers=auth_headers)
    assert resp.status_code == 409

    resp2 = client.patch(
        f"/admin/teams/{team.id}", json={"is_active": False, "confirm_deactivate": True}, headers=auth_headers
    )
    assert resp2.status_code == 200
    assert resp2.json()["is_active"] is False


def test_team_duplicate_name_rejected(client, auth_headers):
    client.post("/admin/teams", json={"name": "2026 Jamboree Aquatics"}, headers=auth_headers)
    resp = client.post("/admin/teams", json={"name": "2026 Jamboree Aquatics"}, headers=auth_headers)
    assert resp.status_code == 400
