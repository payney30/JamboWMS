"""
Tests for enhancement backlog Phase 4 (NJ2026_Work_Order_System_PRD.md
§14#13, §14#16, §14#17, §15#1): WO-number prefix removal + numeric sort,
"work orders I've handled" search, and the admin-configurable time zone
setting.
"""
from app import models


def _create_wo(client, auth_headers, wo_payload):
    resp = client.post("/work-orders", json=wo_payload, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()


# ---- WO number format (PRD §14#13) ----

def test_wo_number_has_no_prefix(client, auth_headers, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    assert wo["wo_number"].isdigit()
    assert not wo["wo_number"].startswith("WO")


def test_wo_numbers_increment_numerically(client, auth_headers, wo_payload):
    wo1 = _create_wo(client, auth_headers, wo_payload)
    wo2 = _create_wo(client, auth_headers, wo_payload)
    assert int(wo2["wo_number"]) == int(wo1["wo_number"]) + 1


def test_wo_number_generation_survives_legacy_prefixed_data(client, auth_headers, wo_payload, db):
    """Bug fix (PRD §14#21): a database that already has "WO-"-prefixed
    work orders from before the prefix-removal fix (§14#13) was deployed
    must not break new-WO creation. This is a real scenario for any
    previously-used deployment, not a hypothetical — creating a WO used
    to 500 outright with this data present, because the prior fix for
    §14#19 used a SQL CAST that only fails on Postgres (SQLite tolerates
    it silently, which is why the test suite didn't catch it the first
    time). See app/crud.py:_next_wo_number for the full story."""
    from app import models
    db.add(models.WorkOrder(
        wo_number="WO-99999", requester_name="Legacy", asset_id=wo_payload["asset_id"],
        work_type="", description="pre-existing legacy WO", priority="Medium", status="Requested",
    ))
    db.commit()

    resp = client.post("/work-orders", json=wo_payload, headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["wo_number"].isdigit()


# ---- "Handled by" search (PRD §14#17) ----

def test_handled_by_finds_wo_via_status_change(client, auth_headers, loc_user, wo_payload, team):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    loc_resp = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    loc_headers = {"Authorization": f"Bearer {loc_resp.json()['access_token']}"}
    client.post(
        f"/work-orders/{wo['id']}/status", json={"status": "Work In Progress"}, headers=loc_headers
    )

    resp = client.get("/work-orders", params={"handled_by": loc_user.id}, headers=auth_headers)
    assert resp.status_code == 200
    ids = [w["id"] for w in resp.json()]
    assert wo["id"] in ids


def test_handled_by_finds_wo_via_note_only(client, auth_headers, loc_user, wo_payload):
    """A user who only added a note (never changed status/team) should
    still show up as having "handled" the WO."""
    wo = _create_wo(client, auth_headers, wo_payload)

    loc_resp = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    loc_headers = {"Authorization": f"Bearer {loc_resp.json()['access_token']}"}
    client.post(
        f"/work-orders/{wo['id']}/notes",
        json={"note_text": "Checked in on this.", "note_type": "internal"},
        headers=loc_headers,
    )

    resp = client.get("/work-orders", params={"handled_by": loc_user.id}, headers=auth_headers)
    assert resp.status_code == 200
    ids = [w["id"] for w in resp.json()]
    assert wo["id"] in ids


def test_handled_by_excludes_untouched_wos(client, auth_headers, loc_user, wo_payload):
    _create_wo(client, auth_headers, wo_payload)  # untouched by loc_user
    resp = client.get("/work-orders", params={"handled_by": loc_user.id}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_handled_by_is_not_the_same_as_currently_assigned(client, auth_headers, loc_user, wo_payload, team):
    """A WO currently assigned to a team shouldn't show up for a user's
    "handled by me" filter just because of that assignment — only if
    they personally show up in the history/notes trail."""
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)
    # assigned by auth_headers' user (admin), not loc_user
    resp = client.get("/work-orders", params={"handled_by": loc_user.id}, headers=auth_headers)
    assert resp.json() == []


# ---- Settings (PRD §15#1) ----

def test_public_settings_returns_default_timezone(client):
    resp = client.get("/public/settings")
    assert resp.status_code == 200
    assert resp.json()["timezone"] == "America/New_York"


def test_admin_can_get_and_set_timezone(client, auth_headers):
    resp = client.put("/admin/settings", json={"timezone": "America/Los_Angeles"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["timezone"] == "America/Los_Angeles"

    get_resp = client.get("/admin/settings", headers=auth_headers)
    assert get_resp.json()["timezone"] == "America/Los_Angeles"

    # public endpoint reflects the change too
    public_resp = client.get("/public/settings")
    assert public_resp.json()["timezone"] == "America/Los_Angeles"


def test_settings_reject_invalid_timezone(client, auth_headers):
    resp = client.put("/admin/settings", json={"timezone": "Not/A_Real_Zone"}, headers=auth_headers)
    assert resp.status_code == 400


def test_non_admin_cannot_set_timezone(client, loc_user):
    loc_resp = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    loc_headers = {"Authorization": f"Bearer {loc_resp.json()['access_token']}"}
    resp = client.put("/admin/settings", json={"timezone": "UTC"}, headers=loc_headers)
    assert resp.status_code == 403


def test_settings_persist_across_requests(client, auth_headers, db):
    client.put("/admin/settings", json={"timezone": "UTC"}, headers=auth_headers)
    row = db.get(models.AppSetting, "timezone")
    assert row is not None
    assert row.value == "UTC"
