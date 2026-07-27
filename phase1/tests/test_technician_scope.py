"""
Tests for the technician / fulfillment-team view's server-side scoping
(PRD 4.3: "each team sees only their queue"). This is a real permissions
boundary — a tech should not be able to see, mutate, or leak state about
another team's work orders just by editing query params or hitting the
API directly instead of the UI.
"""


def _create_and_assign(client, auth_headers, wo_payload, team_id):
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    assign = client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": team_id},
        headers=auth_headers,
    )
    assert assign.status_code == 200, assign.text
    return assign.json()


def test_tech_sees_only_own_team_in_list(
    client, auth_headers, tech_auth_headers, wo_payload, team, other_team
):
    _create_and_assign(client, auth_headers, wo_payload, team.id)
    _create_and_assign(client, auth_headers, wo_payload, other_team.id)

    resp = client.get("/work-orders", headers=tech_auth_headers)
    assert resp.status_code == 200
    wos = resp.json()
    assert len(wos) == 1
    assert wos[0]["assigned_team"]["id"] == team.id


def test_tech_cannot_widen_scope_via_team_id_query_param(
    client, auth_headers, tech_auth_headers, wo_payload, team, other_team
):
    _create_and_assign(client, auth_headers, wo_payload, other_team.id)

    # Tech tries to explicitly ask for the other team's queue by id.
    resp = client.get(f"/work-orders?team_id={other_team.id}", headers=tech_auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []  # server ignores the param and scopes to their own team


def test_tech_cannot_view_other_teams_wo_detail(
    client, auth_headers, tech_auth_headers, wo_payload, other_team
):
    wo = _create_and_assign(client, auth_headers, wo_payload, other_team.id)
    resp = client.get(f"/work-orders/{wo['id']}", headers=tech_auth_headers)
    assert resp.status_code == 403


def test_tech_can_view_and_update_own_teams_wo(
    client, auth_headers, tech_auth_headers, wo_payload, team
):
    wo = _create_and_assign(client, auth_headers, wo_payload, team.id)

    detail = client.get(f"/work-orders/{wo['id']}", headers=tech_auth_headers)
    assert detail.status_code == 200

    status_resp = client.post(
        f"/work-orders/{wo['id']}/status",
        json={"status": "Work In Progress", "note": None},
        headers=tech_auth_headers,
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "Work In Progress"


def test_tech_cannot_set_status_on_other_teams_wo(
    client, auth_headers, tech_auth_headers, wo_payload, other_team
):
    wo = _create_and_assign(client, auth_headers, wo_payload, other_team.id)
    resp = client.post(
        f"/work-orders/{wo['id']}/status",
        json={"status": "Work In Progress"},
        headers=tech_auth_headers,
    )
    assert resp.status_code == 403


def test_tech_cannot_set_triage_only_statuses(
    client, auth_headers, tech_auth_headers, wo_payload, team
):
    wo = _create_and_assign(client, auth_headers, wo_payload, team.id)
    for bad_status in ("Requested", "Assigned"):
        resp = client.post(
            f"/work-orders/{wo['id']}/status",
            json={"status": bad_status},
            headers=tech_auth_headers,
        )
        assert resp.status_code == 403, bad_status


def test_tech_can_add_work_note_but_not_instruction(
    client, auth_headers, tech_auth_headers, wo_payload, team
):
    wo = _create_and_assign(client, auth_headers, wo_payload, team.id)

    ok = client.post(
        f"/work-orders/{wo['id']}/notes",
        json={"note_text": "Parts on order", "note_type": "work_note"},
        headers=tech_auth_headers,
    )
    assert ok.status_code == 201

    blocked = client.post(
        f"/work-orders/{wo['id']}/notes",
        json={"note_text": "Do this next", "note_type": "instruction"},
        headers=tech_auth_headers,
    )
    assert blocked.status_code == 403


def test_tech_can_reassign_own_teams_wo_with_note(
    client, auth_headers, tech_auth_headers, wo_payload, team, other_team
):
    wo = _create_and_assign(client, auth_headers, wo_payload, team.id)
    resp = client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": other_team.id, "note": "Wrong team, this is an IT issue"},
        headers=tech_auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_team"]["id"] == other_team.id


def test_tech_cannot_reassign_other_teams_wo(
    client, auth_headers, tech_auth_headers, wo_payload, team, other_team
):
    wo = _create_and_assign(client, auth_headers, wo_payload, other_team.id)
    resp = client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": team.id, "note": "Taking this one"},
        headers=tech_auth_headers,
    )
    assert resp.status_code == 403


def test_tech_reassign_without_note_is_rejected(
    client, auth_headers, tech_auth_headers, wo_payload, team, other_team
):
    wo = _create_and_assign(client, auth_headers, wo_payload, team.id)
    resp = client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": other_team.id},
        headers=tech_auth_headers,
    )
    assert resp.status_code == 400


def test_assign_person_must_belong_to_target_team(
    client, auth_headers, wo_payload, team, other_team, tech_user
):
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    # tech_user belongs to `team`; assigning them while targeting other_team should fail.
    resp = client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": other_team.id, "person_id": tech_user.id},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_loc_is_not_scoped_by_team(
    client, auth_headers, wo_payload, team, other_team
):
    wo_a = _create_and_assign(client, auth_headers, wo_payload, team.id)
    wo_b = _create_and_assign(client, auth_headers, wo_payload, other_team.id)

    resp = client.get("/work-orders", headers=auth_headers)
    ids = {w["id"] for w in resp.json()}
    assert {wo_a["id"], wo_b["id"]} <= ids


def test_auth_me_returns_role_and_team(client, tech_auth_headers, team):
    resp = client.get("/auth/me", headers=tech_auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "tech"
    assert body["team"]["id"] == team.id
