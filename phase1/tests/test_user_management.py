"""
Tests for the admin user-management screen's backend (POST/GET/PATCH
/users, POST /users/{id}/reset-password). Two things matter most here:
the tech-requires-a-team rule, and the privilege-escalation gate that
keeps a non-admin 'loc' account from creating or touching admin/loc
accounts.
"""


def test_admin_can_create_tech_user(client, auth_headers, team):
    resp = client.post(
        "/users",
        json={"name": "New Tech", "email": "newtech@test.local", "role": "tech", "team_id": team.id},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["role"] == "tech"
    assert body["user"]["team"]["id"] == team.id
    assert body["temporary_password"]  # returned exactly once

    # the temp password actually works
    login = client.post(
        "/auth/login", data={"username": "newtech@test.local", "password": body["temporary_password"]}
    )
    assert login.status_code == 200


def test_tech_role_requires_a_team(client, auth_headers):
    resp = client.post(
        "/users",
        json={"name": "No Team Tech", "email": "noteam@test.local", "role": "tech"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_team_must_exist(client, auth_headers):
    resp = client.post(
        "/users",
        json={"name": "Ghost Team Tech", "email": "ghost@test.local", "role": "tech", "team_id": 99999},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_duplicate_email_rejected(client, auth_headers, admin_user, team):
    resp = client.post(
        "/users",
        json={"name": "Dupe", "email": admin_user.email, "role": "tech", "team_id": team.id},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_loc_can_create_tech_user(client, loc_user, team):
    login = client.post("/auth/login", data={"username": loc_user.email, "password": "test-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    resp = client.post(
        "/users",
        json={"name": "LOC-created Tech", "email": "loctech@test.local", "role": "tech", "team_id": team.id},
        headers=headers,
    )
    assert resp.status_code == 201


def test_loc_cannot_create_admin_user(client, loc_user, team):
    login = client.post("/auth/login", data={"username": loc_user.email, "password": "test-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    resp = client.post(
        "/users",
        json={"name": "Sneaky Admin", "email": "sneaky@test.local", "role": "admin"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_loc_cannot_create_loc_user(client, loc_user):
    login = client.post("/auth/login", data={"username": loc_user.email, "password": "test-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    resp = client.post(
        "/users",
        json={"name": "Another LOC", "email": "loc2@test.local", "role": "loc"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_tech_cannot_access_user_management(client, tech_auth_headers):
    resp = client.get("/users", headers=tech_auth_headers)
    assert resp.status_code == 403


def test_admin_can_deactivate_a_tech_user(client, auth_headers, tech_user):
    resp = client.patch(f"/users/{tech_user.id}", json={"is_active": False}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    login = client.post("/auth/login", data={"username": tech_user.email, "password": "test-password"})
    assert login.status_code == 401  # inactive accounts can't authenticate


def test_cannot_deactivate_own_account(client, auth_headers, admin_user):
    resp = client.patch(f"/users/{admin_user.id}", json={"is_active": False}, headers=auth_headers)
    assert resp.status_code == 400


def test_loc_cannot_deactivate_a_loc_account(client, loc_user, admin_user):
    login = client.post("/auth/login", data={"username": loc_user.email, "password": "test-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    resp = client.patch(f"/users/{admin_user.id}", json={"is_active": False}, headers=headers)
    assert resp.status_code == 403


def test_changing_role_to_tech_still_requires_a_team(client, auth_headers, loc_user):
    resp = client.patch(f"/users/{loc_user.id}", json={"role": "tech"}, headers=auth_headers)
    assert resp.status_code == 400


def test_reassigning_a_tech_to_a_new_team(client, auth_headers, tech_user, other_team):
    resp = client.patch(f"/users/{tech_user.id}", json={"team_id": other_team.id}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["team"]["id"] == other_team.id


def test_reset_password_invalidates_old_one_and_returns_new(client, auth_headers, tech_user):
    resp = client.post(f"/users/{tech_user.id}/reset-password", headers=auth_headers)
    assert resp.status_code == 200
    new_password = resp.json()["temporary_password"]
    assert new_password

    old_login = client.post(
        "/auth/login", data={"username": tech_user.email, "password": "test-password"}
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/auth/login", data={"username": tech_user.email, "password": new_password}
    )
    assert new_login.status_code == 200


def test_list_users_excludes_inactive_by_default(client, auth_headers, tech_user):
    client.patch(f"/users/{tech_user.id}", json={"is_active": False}, headers=auth_headers)
    resp = client.get("/users", headers=auth_headers)
    ids = {u["id"] for u in resp.json()}
    assert tech_user.id not in ids

    resp_all = client.get("/users?include_inactive=true", headers=auth_headers)
    ids_all = {u["id"] for u in resp_all.json()}
    assert tech_user.id in ids_all
