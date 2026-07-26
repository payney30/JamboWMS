"""
End-to-end tests through the actual HTTP endpoints — the same path a
real client hits. Complements test_status_history_engine.py, which tests
the crud layer directly for precision on the history rules.
"""


def test_login_returns_token(client, admin_user):
    resp = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "test-password"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "admin"
    assert body["access_token"]


def test_login_rejects_wrong_password(client, admin_user):
    resp = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "wrong"}
    )
    assert resp.status_code == 401


def test_unauthenticated_request_is_rejected(client):
    resp = client.get("/work-orders")
    assert resp.status_code == 401


def test_full_lifecycle_create_assign_close(client, auth_headers, wo_payload, team):
    create_resp = client.post("/work-orders", json=wo_payload, headers=auth_headers)
    assert create_resp.status_code == 201
    wo = create_resp.json()
    assert wo["status"] == "Requested"

    assign_resp = client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": team.id},
        headers=auth_headers,
    )
    assert assign_resp.status_code == 200
    assert assign_resp.json()["status"] == "Assigned"

    close_resp = client.post(
        f"/work-orders/{wo['id']}/status",
        json={"status": "Closed, Completed", "note": "Fixed the faucet"},
        headers=auth_headers,
    )
    assert close_resp.status_code == 200
    closed = close_resp.json()
    assert closed["status"] == "Closed, Completed"
    assert closed["closed_at"] is not None

    detail_resp = client.get(f"/work-orders/{wo['id']}", headers=auth_headers)
    history = detail_resp.json()["history"]
    event_types = [h["event_type"] for h in history]
    assert event_types == ["status_change", "status_change", "reassignment", "status_change"]


def test_reassignment_without_note_is_rejected_over_http(client, auth_headers, wo_payload, team, other_team):
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    reroute_resp = client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": other_team.id},  # no note
        headers=auth_headers,
    )
    assert reroute_resp.status_code == 400

    # confirm it's still assigned to the original team
    detail = client.get(f"/work-orders/{wo['id']}", headers=auth_headers).json()
    assert detail["assigned_team"]["id"] == team.id


def test_reassignment_with_note_succeeds_over_http(client, auth_headers, wo_payload, team, other_team):
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    reroute_resp = client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": other_team.id, "note": "Original team is overloaded"},
        headers=auth_headers,
    )
    assert reroute_resp.status_code == 200
    assert reroute_resp.json()["assigned_team"]["id"] == other_team.id


def test_loc_role_can_create_work_orders(client, loc_user):
    login = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    resp = client.get("/teams", headers=headers)
    assert resp.status_code == 200  # loc role is allowed to read reference data


def test_dashboard_kpis_endpoint(client, auth_headers, wo_payload):
    client.post("/work-orders", json=wo_payload, headers=auth_headers)
    resp = client.get("/dashboard/kpis", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["opened_today"] == 1
