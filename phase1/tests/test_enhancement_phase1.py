"""
Tests for enhancement backlog Phase 1 (NJ2026_Work_Order_System_PRD.md
§13#4, §14#1, §14#2, §14#5): WO locking, the combined /save endpoint, and
the requester-facing note field. Phone-anchored public lookup is covered
in tests/test_public_request_form.py; this file covers the authenticated
LOC/tech-facing surface.
"""
import datetime as dt

from app import models


def _create_wo(client, auth_headers, wo_payload):
    resp = client.post("/work-orders", json=wo_payload, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()


# ---- Locking (PRD §14#1) ----

def test_lock_then_unlock_round_trip(client, auth_headers, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)

    lock_resp = client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)
    assert lock_resp.status_code == 200
    body = lock_resp.json()
    assert body["locked"] is True
    assert body["locked_by"]["email"] == "admin@test.local"

    unlock_resp = client.post(f"/work-orders/{wo['id']}/unlock", headers=auth_headers)
    assert unlock_resp.status_code == 200
    assert unlock_resp.json()["locked"] is False


def test_locking_shows_up_on_list_and_detail(client, auth_headers, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)

    list_resp = client.get("/work-orders", headers=auth_headers)
    row = next(w for w in list_resp.json() if w["id"] == wo["id"])
    assert row["locked_by"]["email"] == "admin@test.local"
    assert row["locked_at"] is not None

    detail_resp = client.get(f"/work-orders/{wo['id']}", headers=auth_headers)
    assert detail_resp.json()["locked_by"]["email"] == "admin@test.local"


def test_second_user_cannot_lock_an_already_locked_wo(client, auth_headers, loc_user, db, wo_payload):
    from app.auth import hash_password
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)

    loc_resp = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    loc_headers = {"Authorization": f"Bearer {loc_resp.json()['access_token']}"}

    second_lock = client.post(f"/work-orders/{wo['id']}/lock", headers=loc_headers)
    assert second_lock.status_code == 409
    assert "Admin User" in second_lock.json()["detail"]


def test_locking_is_idempotent_for_the_same_user(client, auth_headers, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    first = client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)
    second = client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)
    assert first.status_code == 200
    assert second.status_code == 200


def test_second_user_cannot_release_a_lock_they_dont_hold(client, auth_headers, loc_user, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)

    loc_resp = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    loc_headers = {"Authorization": f"Bearer {loc_resp.json()['access_token']}"}

    unlock_attempt = client.post(f"/work-orders/{wo['id']}/unlock", headers=loc_headers)
    assert unlock_attempt.status_code == 403


def test_mutating_a_locked_wo_as_a_different_loc_user_is_rejected(client, auth_headers, loc_user, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)

    loc_resp = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    loc_headers = {"Authorization": f"Bearer {loc_resp.json()['access_token']}"}

    patch_resp = client.patch(
        f"/work-orders/{wo['id']}", json={"priority": "High"}, headers=loc_headers
    )
    assert patch_resp.status_code == 409


def test_stale_lock_is_treated_as_unlocked(client, auth_headers, loc_user, db, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)

    # Simulate a lock that's gone stale (backdate it past LOCK_TIMEOUT_MINUTES).
    wo_row = db.get(models.WorkOrder, wo["id"])
    wo_row.locked_at = dt.datetime.utcnow() - dt.timedelta(minutes=models.LOCK_TIMEOUT_MINUTES + 5)
    db.commit()

    loc_resp = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    loc_headers = {"Authorization": f"Bearer {loc_resp.json()['access_token']}"}

    second_lock = client.post(f"/work-orders/{wo['id']}/lock", headers=loc_headers)
    assert second_lock.status_code == 200


def test_admin_can_force_unlock_someone_elses_lock(client, auth_headers, loc_user, wo_payload):
    """auth_headers is already an admin user in this test suite's fixtures."""
    wo = _create_wo(client, auth_headers, wo_payload)

    loc_resp = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    loc_headers = {"Authorization": f"Bearer {loc_resp.json()['access_token']}"}
    client.post(f"/work-orders/{wo['id']}/lock", headers=loc_headers)

    force_unlock = client.post(f"/work-orders/{wo['id']}/unlock", headers=auth_headers)
    assert force_unlock.status_code == 200
    assert force_unlock.json()["locked"] is False


def test_tech_mutation_is_not_blocked_by_an_loc_lock(client, auth_headers, tech_auth_headers,
                                                       wo_payload, team):
    """Locking is scoped to loc/admin (PRD §14#1 was requested for the
    LOC triage screen); technician.html has no lock UI at all, so a tech
    updating their own queue shouldn't be blocked by an LOC user having
    the WO open in triage."""
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)
    client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)

    status_resp = client.post(
        f"/work-orders/{wo['id']}/status",
        json={"status": "Work In Progress"},
        headers=tech_auth_headers,
    )
    assert status_resp.status_code == 200


# ---- Combined save (PRD §14#2) ----

def test_save_endpoint_applies_details_status_assign_and_note_in_one_call(
    client, auth_headers, wo_payload, team
):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)

    resp = client.post(
        f"/work-orders/{wo['id']}/save",
        json={
            "priority": "High",
            "note_to_requester": "On our way.",
            "status": "Work In Progress",
            "team_id": team.id,
            "new_note_text": "Dispatched a tech.",
            "new_note_type": "internal",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["priority"] == "High"
    assert body["note_to_requester"] == "On our way."
    assert body["status"] == "Work In Progress"
    assert body["assigned_team"]["id"] == team.id
    assert any(n["note_text"] == "Dispatched a tech." for n in body["notes"])
    # save releases the lock
    assert body["locked_by"] is None


def test_save_endpoint_releases_the_lock(client, auth_headers, loc_user, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)
    client.post(f"/work-orders/{wo['id']}/save", json={"priority": "High"}, headers=auth_headers)

    loc_resp = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    loc_headers = {"Authorization": f"Bearer {loc_resp.json()['access_token']}"}
    second_lock = client.post(f"/work-orders/{wo['id']}/lock", headers=loc_headers)
    assert second_lock.status_code == 200


def test_save_endpoint_writes_one_history_row_per_mutated_field(
    client, auth_headers, wo_payload, team
):
    wo = _create_wo(client, auth_headers, wo_payload)
    resp = client.post(
        f"/work-orders/{wo['id']}/save",
        json={"priority": "High", "team_id": team.id},
        headers=auth_headers,
    )
    history = resp.json()["history"]
    event_types = sorted(h["event_type"] for h in history)
    # initial "Requested" row from creation, plus priority_change and
    # reassignment from this save — assigning a still-"Requested" WO also
    # auto-transitions it to "Assigned" (crud.assign_work_order), which is
    # its own status_change row, same as the granular /assign endpoint.
    assert event_types == ["priority_change", "reassignment", "status_change", "status_change"]


def test_save_endpoint_tech_cannot_edit_details(client, auth_headers, tech_auth_headers,
                                                  wo_payload, team):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    resp = client.post(
        f"/work-orders/{wo['id']}/save",
        json={"description": "tech trying to edit details"},
        headers=tech_auth_headers,
    )
    assert resp.status_code == 403


def test_save_endpoint_tech_cannot_set_disallowed_status(client, auth_headers, tech_auth_headers,
                                                            wo_payload, team):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    resp = client.post(
        f"/work-orders/{wo['id']}/save",
        json={"status": "Requested"},
        headers=tech_auth_headers,
    )
    assert resp.status_code == 403


def test_save_endpoint_respects_reassignment_note_requirement(
    client, auth_headers, wo_payload, team, other_team
):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/save", json={"team_id": team.id}, headers=auth_headers)

    resp = client.post(
        f"/work-orders/{wo['id']}/save",
        json={"team_id": other_team.id},  # no assign_note
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_save_endpoint_blocked_when_locked_by_someone_else(client, auth_headers, loc_user, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/lock", headers=auth_headers)

    loc_resp = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    loc_headers = {"Authorization": f"Bearer {loc_resp.json()['access_token']}"}
    resp = client.post(
        f"/work-orders/{wo['id']}/save", json={"priority": "High"}, headers=loc_headers
    )
    assert resp.status_code == 409


# ---- Note to requester (PRD §14#5) ----

def test_note_to_requester_editable_via_patch(client, auth_headers, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    resp = client.patch(
        f"/work-orders/{wo['id']}", json={"note_to_requester": "Parts on order."}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["note_to_requester"] == "Parts on order."


# ---- Enhancement backlog Phase 2 (PRD §14#9): notes icon in inbox ----

def test_note_to_requester_appears_on_inbox_list(client, auth_headers, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.patch(f"/work-orders/{wo['id']}", json={"note_to_requester": "On our way."}, headers=auth_headers)

    list_resp = client.get("/work-orders", headers=auth_headers)
    row = next(w for w in list_resp.json() if w["id"] == wo["id"])
    assert row["note_to_requester"] == "On our way."


def test_note_to_requester_absent_by_default_on_inbox_list(client, auth_headers, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    list_resp = client.get("/work-orders", headers=auth_headers)
    row = next(w for w in list_resp.json() if w["id"] == wo["id"])
    assert row["note_to_requester"] is None


# ---- Enhancement backlog Phase 3 (PRD §14#15): note author name ----

def test_note_includes_author_name(client, auth_headers, wo_payload):
    wo = _create_wo(client, auth_headers, wo_payload)
    resp = client.post(
        f"/work-orders/{wo['id']}/notes",
        json={"note_text": "Checked on it.", "note_type": "internal"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["author"]["name"] == "Admin User"

def test_status_history_includes_changed_by_name(client, auth_headers, wo_payload, team):
    wo = _create_wo(client, auth_headers, wo_payload)
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    detail = client.get(f"/work-orders/{wo['id']}", headers=auth_headers).json()
    reassignment_rows = [h for h in detail["history"] if h["event_type"] == "reassignment"]
    assert reassignment_rows[0]["changed_by_name"] == "Admin User"

    # The initial "Requested" row is system-generated (changed_by is None).
    initial_row = detail["history"][0]
    assert initial_row["changed_by_name"] is None
