"""
Tests for enhancement backlog Phase 21 (NJ2026_Work_Order_System_PRD.md
§17#10): Task Team assignment — delegated worker management, PIN login,
per-worker assignment scoping, and the worker "Completed" action.
"""
import pytest

from app import crud, schemas, models


# ---- Delegated worker management ----

def test_tech_can_create_worker_for_own_team(client, tech_user, tech_auth_headers):
    resp = client.post(
        "/my-team/workers", json={"name": "Alex"}, headers=tech_auth_headers
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["worker"]["name"] == "Alex"
    assert body["worker"]["team"]["id"] == tech_user.team_id
    assert len(body["pin"]) == 4 and body["pin"].isdigit()


def test_worker_created_scoped_to_creating_techs_team(client, tech_user, db, tech_auth_headers):
    resp = client.post(
        "/my-team/workers", json={"name": "Sam"}, headers=tech_auth_headers
    )
    worker_id = resp.json()["worker"]["id"]
    worker = db.get(models.User, worker_id)
    assert worker.role == "task_worker"
    assert worker.team_id == tech_user.team_id


def test_loc_cannot_create_task_workers(client, auth_headers):
    resp = client.post("/my-team/workers", json={"name": "Alex"}, headers=auth_headers)
    assert resp.status_code == 403


def test_list_my_workers_only_shows_own_team(client, tech_user, db, team, tech_auth_headers):
    client.post("/my-team/workers", json={"name": "Alex"}, headers=tech_auth_headers)

    other_team = models.Team(name="Other Team", is_active=True)
    db.add(other_team)
    db.commit()
    db.refresh(other_team)
    crud.create_task_worker(db, other_team.id, schemas.TaskWorkerCreate(name="NotMine"))

    resp = client.get("/my-team/workers", headers=tech_auth_headers)
    names = [w["name"] for w in resp.json()]
    assert "Alex" in names
    assert "NotMine" not in names


def test_deactivate_worker_only_from_own_team(client, tech_user, db, team, tech_auth_headers):
    other_team = models.Team(name="Other Team 2", is_active=True)
    db.add(other_team)
    db.commit()
    db.refresh(other_team)
    other_worker, _ = crud.create_task_worker(db, other_team.id, schemas.TaskWorkerCreate(name="NotMine"))

    resp = client.delete(f"/my-team/workers/{other_worker.id}", headers=tech_auth_headers)
    assert resp.status_code == 404  # not 403 — see router docstring on why


def test_deactivated_worker_stays_hidden_from_default_list(client, tech_user, db, tech_auth_headers):
    create_resp = client.post(
        "/my-team/workers", json={"name": "Jamie"}, headers=tech_auth_headers
    )
    worker_id = create_resp.json()["worker"]["id"]
    client.delete(f"/my-team/workers/{worker_id}", headers=tech_auth_headers)

    resp = client.get("/my-team/workers", headers=tech_auth_headers)
    assert worker_id not in [w["id"] for w in resp.json()]


# ---- Assignable team members (end-to-end testing 8/10/26) ----
# GET /my-team/assignable backs both the technician queue's worker
# filter and the drawer's "Task to worker" dropdown — see
# crud.list_assignable_team_members. Distinct from /my-team/workers
# above: includes 'tech' (Dispatcher) accounts too, not just
# task_workers, matching what crud.assign_work_order actually allows.

def test_assignable_includes_task_workers_and_dispatchers_on_own_team(
    client, tech_user, db, team, tech_auth_headers
):
    crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Jordan"))
    other_dispatcher = models.User(
        name="Other Dispatcher", email="other-dispatcher@test.local",
        password_hash="x", role="tech", team_id=team.id,
    )
    db.add(other_dispatcher)
    db.commit()

    resp = client.get("/my-team/assignable", headers=tech_auth_headers)
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()}
    assert "Jordan" in names  # task_worker
    assert "Other Dispatcher" in names  # tech
    assert tech_user.name in names  # the caller's own tech account, too


def test_assignable_excludes_other_teams(client, tech_user, db, team, tech_auth_headers):
    other_team = models.Team(name="Other Team 3", is_active=True)
    db.add(other_team)
    db.commit()
    db.refresh(other_team)
    crud.create_task_worker(db, other_team.id, schemas.TaskWorkerCreate(name="NotMyWorker"))
    other_team_tech = models.User(
        name="Not My Dispatcher", email="not-my-dispatcher@test.local",
        password_hash="x", role="tech", team_id=other_team.id,
    )
    db.add(other_team_tech)
    db.commit()

    resp = client.get("/my-team/assignable", headers=tech_auth_headers)
    names = {p["name"] for p in resp.json()}
    assert "NotMyWorker" not in names
    assert "Not My Dispatcher" not in names


def test_assignable_excludes_other_roles(client, tech_user, db, team, tech_auth_headers):
    """loc/leadership accounts on the same team_id (if that ever happens)
    still shouldn't show up here — only tech and task_worker are valid
    assignment targets per crud.assign_work_order's usage."""
    leadership_user = models.User(
        name="Leadership Viewer", email="leadership@test.local",
        password_hash="x", role="leadership", team_id=team.id,
    )
    db.add(leadership_user)
    db.commit()

    resp = client.get("/my-team/assignable", headers=tech_auth_headers)
    names = {p["name"] for p in resp.json()}
    assert "Leadership Viewer" not in names


def test_assignable_excludes_deactivated(client, tech_user, db, team, tech_auth_headers):
    worker, _ = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Deactivated"))
    crud.deactivate_task_worker(db, worker)

    resp = client.get("/my-team/assignable", headers=tech_auth_headers)
    names = {p["name"] for p in resp.json()}
    assert "Deactivated" not in names


def test_assignable_includes_role_field(client, tech_user, db, team, tech_auth_headers):
    crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Casey"))
    resp = client.get("/my-team/assignable", headers=tech_auth_headers)
    by_name = {p["name"]: p["role"] for p in resp.json()}
    assert by_name["Casey"] == "task_worker"
    assert by_name[tech_user.name] == "tech"


def test_loc_cannot_call_assignable_endpoint(client, auth_headers):
    resp = client.get("/my-team/assignable", headers=auth_headers)
    assert resp.status_code == 403


def test_assign_work_order_to_fellow_dispatcher(client, auth_headers, wo_payload, team, tech_user, db):
    """The scenario the endpoint above exists to support: assigning a WO
    to a 'tech' account, not just a task_worker — was already accepted
    by crud.assign_work_order (role-agnostic team_id check), just never
    reachable from the frontend without this list."""
    other_dispatcher = models.User(
        name="Fellow Dispatcher", email="fellow-dispatcher@test.local",
        password_hash="x", role="tech", team_id=team.id,
    )
    db.add(other_dispatcher)
    db.commit()
    db.refresh(other_dispatcher)

    create = client.post("/work-orders", json=wo_payload, headers=auth_headers)
    wo_id = create.json()["id"]
    resp = client.post(
        f"/work-orders/{wo_id}/assign",
        json={"team_id": team.id, "person_id": other_dispatcher.id},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_person"]["id"] == other_dispatcher.id


# ---- PIN login flow ----

def test_worker_login_teams_no_auth_required(client, team):
    resp = client.get("/public/worker-login/teams")
    assert resp.status_code == 200


def test_worker_login_workers_lists_no_pin_exposed(client, tech_user, db):
    worker, pin = crud.create_task_worker(db, tech_user.team_id, schemas.TaskWorkerCreate(name="Casey"))
    resp = client.get("/public/worker-login/workers", params={"team_id": tech_user.team_id})
    assert resp.status_code == 200
    body = resp.json()[0]
    assert "pin" not in body and "pin_hash" not in body


def test_worker_login_success(client, tech_user, db):
    worker, pin = crud.create_task_worker(db, tech_user.team_id, schemas.TaskWorkerCreate(name="Casey"))
    resp = client.post("/public/worker-login", json={"worker_id": worker.id, "pin": pin})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_worker_login_wrong_pin_rejected(client, tech_user, db):
    worker, pin = crud.create_task_worker(db, tech_user.team_id, schemas.TaskWorkerCreate(name="Casey"))
    wrong = "0000" if pin != "0000" else "1111"
    resp = client.post("/public/worker-login", json={"worker_id": worker.id, "pin": wrong})
    assert resp.status_code == 401


def test_worker_login_rejects_non_task_worker_id(client, tech_user):
    """A worker_id pointing at a real user who isn't a task_worker
    (e.g. the tech themselves) must not be usable via this flow, even
    with a guessed/blank pin."""
    resp = client.post("/public/worker-login", json={"worker_id": tech_user.id, "pin": "0000"})
    assert resp.status_code == 401


def test_deactivated_worker_cannot_log_in(client, tech_user, db):
    worker, pin = crud.create_task_worker(db, tech_user.team_id, schemas.TaskWorkerCreate(name="Casey"))
    crud.deactivate_task_worker(db, worker)
    resp = client.post("/public/worker-login", json={"worker_id": worker.id, "pin": pin})
    assert resp.status_code == 401


# ---- Per-worker assignment (existing backend capability, now exercised) ----

def test_assign_work_order_to_worker(client, auth_headers, wo_payload, team, tech_user, db):
    worker, _ = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()

    resp = client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": team.id, "person_id": worker.id},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_person"]["id"] == worker.id


def test_cannot_assign_worker_from_different_team(client, auth_headers, wo_payload, team, db):
    other_team = models.Team(name="Other Team 3", is_active=True)
    db.add(other_team)
    db.commit()
    db.refresh(other_team)
    other_worker, _ = crud.create_task_worker(db, other_team.id, schemas.TaskWorkerCreate(name="NotOnTeam"))
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()

    resp = client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": team.id, "person_id": other_worker.id},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ---- Worker's own scoped queue ----

def test_worker_sees_only_own_assigned_wos(client, auth_headers, wo_payload, team, tech_user, db):
    worker, pin = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    mine = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    not_mine = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{mine['id']}/assign", json={"team_id": team.id, "person_id": worker.id}, headers=auth_headers)
    client.post(f"/work-orders/{not_mine['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    login = client.post("/public/worker-login", json={"worker_id": worker.id, "pin": pin})
    worker_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.get("/work-orders", headers=worker_headers)
    ids = [w["id"] for w in resp.json()]
    assert mine["id"] in ids
    assert not_mine["id"] not in ids


def test_worker_cannot_view_wo_not_assigned_to_them(client, auth_headers, wo_payload, team, db):
    worker, pin = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    login = client.post("/public/worker-login", json={"worker_id": worker.id, "pin": pin})
    worker_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.get(f"/work-orders/{wo['id']}", headers=worker_headers)
    assert resp.status_code == 403


# ---- Completed button ----

def test_worker_can_complete_own_assigned_wo(client, auth_headers, wo_payload, team, db):
    worker, pin = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id, "person_id": worker.id}, headers=auth_headers)

    login = client.post("/public/worker-login", json={"worker_id": worker.id, "pin": pin})
    worker_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.post(f"/work-orders/{wo['id']}/complete", json={}, headers=worker_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "Closed, Completed"


def test_complete_accepts_optional_note_and_pin(client, auth_headers, wo_payload, team, db):
    worker, pin = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id, "person_id": worker.id}, headers=auth_headers)
    login = client.post("/public/worker-login", json={"worker_id": worker.id, "pin": pin})
    worker_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.post(
        f"/work-orders/{wo['id']}/complete",
        json={"note": "Dropped at the requested spot", "completion_latitude": 37.86, "completion_longitude": -81.13},
        headers=worker_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["completion_latitude"] == 37.86
    assert body["latitude"] is None  # confirmed separate from any submission pin


def test_worker_cannot_complete_wo_not_assigned_to_them(client, auth_headers, wo_payload, team, db):
    worker, pin = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)  # unassigned to a person

    login = client.post("/public/worker-login", json={"worker_id": worker.id, "pin": pin})
    worker_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.post(f"/work-orders/{wo['id']}/complete", json={}, headers=worker_headers)
    assert resp.status_code == 403


def test_loc_cannot_call_worker_complete_endpoint(client, auth_headers, wo_payload):
    """The 'simple Completed button' is task_worker-only — LOC/tech use
    the fuller status-change surface instead."""
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    resp = client.post(f"/work-orders/{wo['id']}/complete", json={}, headers=auth_headers)
    assert resp.status_code == 403


# ---- Completion photo attachment stage ----

def test_attachment_stage_defaults_to_submission(client, asset, db):
    resp = client.post(
        "/public/work-orders",
        data={
            "requester_name": "Scout", "requester_email": "scout@example.com",
            "requester_phone": "555-0100", "asset_id": str(asset.id),
            "description": "x", "priority": "Next Day", "website": "", "poc_is_requester": "true",
        },
    )
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == resp.json()["wo_number"]).first()
    crud.add_attachment(db, wo, "/uploads/test.jpg")
    att = db.query(models.WOAttachment).filter(models.WOAttachment.work_order_id == wo.id).first()
    assert att.stage == "submission"


def test_add_attachment_completion_stage(db, asset):
    wo = crud.create_work_order(
        db, schemas.WorkOrderCreate(requester_name="Scout", asset_id=asset.id, description="x", priority="Next Day")
    )
    att = crud.add_attachment(db, wo, "/uploads/done.jpg", stage="completion")
    assert att.stage == "completion"


# ---- Enhancement backlog Phase 22 (PRD §17#10 follow-up): tasking event ----

def test_worker_assignment_logs_distinct_tasking_event(client, auth_headers, wo_payload, team, db):
    worker, _ = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()

    client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": team.id, "person_id": worker.id},
        headers=auth_headers,
    )
    detail = client.get(f"/work-orders/{wo['id']}", headers=auth_headers).json()
    tasking_events = [h for h in detail["history"] if h["event_type"] == "tasking"]
    assert len(tasking_events) == 1
    assert tasking_events[0]["from_value"] == "Unassigned"
    assert tasking_events[0]["to_value"] == "Riley"


def test_tasking_worker_without_team_change_does_not_log_spurious_reassignment(
    client, auth_headers, wo_payload, team, db
):
    """Bug fix: assign_work_order used to unconditionally write a
    'reassignment' row on every call, including tasking a worker without
    changing teams — producing a "reassignment: TeamX -> TeamX" row that
    didn't reflect what actually happened. Fixed to only log reassignment
    when the team itself changes."""
    worker, _ = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    # First call sets the team (no prior team -> not a "change" in the
    # reroute sense, but assigned_team_id does go from None to team.id).
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    # Second call tasks a worker on the SAME team — should log only a
    # tasking event, no additional reassignment row.
    resp = client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": team.id, "person_id": worker.id},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    detail = client.get(f"/work-orders/{wo['id']}", headers=auth_headers).json()
    reassignment_events = [h for h in detail["history"] if h["event_type"] == "reassignment"]
    tasking_events = [h for h in detail["history"] if h["event_type"] == "tasking"]
    assert len(reassignment_events) == 1  # only from the first call (None -> team)
    assert len(tasking_events) == 1  # only from the second call


def test_untasking_worker_logs_tasking_event_to_unassigned(client, auth_headers, wo_payload, team, db):
    worker, _ = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id, "person_id": worker.id}, headers=auth_headers)

    resp = client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)
    assert resp.status_code == 200
    detail = client.get(f"/work-orders/{wo['id']}", headers=auth_headers).json()
    tasking_events = [h for h in detail["history"] if h["event_type"] == "tasking"]
    assert len(tasking_events) == 2
    assert tasking_events[-1]["from_value"] == "Riley"
    assert tasking_events[-1]["to_value"] == "Unassigned"


# ---- Enhancement backlog Phase 24 follow-up: reset a Task Worker's PIN ----

def test_tech_can_reset_own_teams_worker_pin(client, tech_auth_headers, tech_user, db):
    worker, old_pin = crud.create_task_worker(db, tech_user.team_id, schemas.TaskWorkerCreate(name="Riley"))

    resp = client.post(f"/my-team/workers/{worker.id}/reset-pin", headers=tech_auth_headers)
    assert resp.status_code == 200
    new_pin = resp.json()["pin"]
    assert new_pin != old_pin

    # Old PIN no longer works.
    old_login = client.post("/public/worker-login", json={"worker_id": worker.id, "pin": old_pin})
    assert old_login.status_code == 401
    # New PIN works.
    new_login = client.post("/public/worker-login", json={"worker_id": worker.id, "pin": new_pin})
    assert new_login.status_code == 200


def test_tech_cannot_reset_pin_for_another_teams_worker(client, tech_auth_headers, db):
    other_team = models.Team(name="Other Team Reset Test", is_active=True)
    db.add(other_team)
    db.commit()
    db.refresh(other_team)
    other_worker, _ = crud.create_task_worker(db, other_team.id, schemas.TaskWorkerCreate(name="NotMine"))

    resp = client.post(f"/my-team/workers/{other_worker.id}/reset-pin", headers=tech_auth_headers)
    assert resp.status_code == 404


def test_loc_cannot_reset_worker_pin(client, auth_headers, tech_user, db):
    worker, _ = crud.create_task_worker(db, tech_user.team_id, schemas.TaskWorkerCreate(name="Riley"))
    resp = client.post(f"/my-team/workers/{worker.id}/reset-pin", headers=auth_headers)
    assert resp.status_code == 403
