"""
Tests for enhancement backlog Phase 25 (NJ2026_Work_Order_System_PRD.md
§17#6-8): SMS + email notifications on WO status milestones.

Mocks send_sms/send_email and _run_in_background (made synchronous)
throughout — these tests check the *decision logic* (who gets
notified, when, via which channel), not actual Twilio/SendGrid
delivery, which needs real credentials and a live network call.
"""
from unittest.mock import patch

import pytest

from app import crud, schemas, notifications


def _make_wo(db, asset, poc_is_requester=True, poc_name=None, poc_phone=None):
    return crud.create_work_order(
        db, schemas.WorkOrderCreate(
            requester_name="Scout", requester_email="scout@example.com",
            requester_phone="555-0100", asset_id=asset.id, description="x",
            priority="Next Day",
            poc_is_requester=poc_is_requester, poc_name=poc_name, poc_phone=poc_phone,
        ),
    )


@pytest.fixture(autouse=True)
def _sync_background(monkeypatch):
    """Run "background" sends synchronously and with notifications
    force-enabled, so tests can assert on them deterministically
    without real threads or real credentials."""
    monkeypatch.setattr(notifications, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(notifications, "_run_in_background", lambda fn, *a, **kw: fn(*a, **kw))


# ---- Kill switch and configuration checks ----

def test_send_sms_noop_when_notifications_disabled(monkeypatch):
    monkeypatch.setattr(notifications, "NOTIFICATIONS_ENABLED", False)
    assert notifications.send_sms("555-0100", "hi") is False


def test_send_email_noop_when_notifications_disabled(monkeypatch):
    monkeypatch.setattr(notifications, "NOTIFICATIONS_ENABLED", False)
    assert notifications.send_email("a@example.com", "subj", "<p>hi</p>") is False


def test_send_sms_noop_when_twilio_not_configured(monkeypatch):
    monkeypatch.setattr(notifications, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", None)
    assert notifications.send_sms("555-0100", "hi") is False


def test_send_email_noop_when_sendgrid_not_configured(monkeypatch):
    monkeypatch.setattr(notifications, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(notifications, "SENDGRID_API_KEY", None)
    assert notifications.send_email("a@example.com", "subj", "<p>hi</p>") is False


def test_send_sms_calls_twilio_client(monkeypatch):
    monkeypatch.setattr(notifications, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550100")

    with patch("twilio.rest.Client") as MockClient:
        instance = MockClient.return_value
        result = notifications.send_sms("+15550199", "test body")
        assert result is True
        instance.messages.create.assert_called_once_with(
            to="+15550199", from_="+15550100", body="test body"
        )


def test_send_sms_returns_false_on_provider_error(monkeypatch):
    monkeypatch.setattr(notifications, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550100")

    with patch("twilio.rest.Client") as MockClient:
        MockClient.return_value.messages.create.side_effect = Exception("boom")
        assert notifications.send_sms("+15550199", "test body") is False


def test_send_email_calls_sendgrid_client(monkeypatch):
    monkeypatch.setattr(notifications, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(notifications, "SENDGRID_API_KEY", "key")
    monkeypatch.setattr(notifications, "SENDGRID_FROM_EMAIL", "graeme@cybersecurity4executives.com")
    monkeypatch.setattr(notifications, "SENDGRID_FROM_NAME", "JamboWMS")

    with patch("sendgrid.SendGridAPIClient") as MockClient:
        instance = MockClient.return_value
        result = notifications.send_email("scout@example.com", "Subject", "<p>hi</p>")
        assert result is True
        instance.send.assert_called_once()


def test_send_email_returns_false_on_provider_error(monkeypatch):
    monkeypatch.setattr(notifications, "NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(notifications, "SENDGRID_API_KEY", "key")
    monkeypatch.setattr(notifications, "SENDGRID_FROM_EMAIL", "graeme@cybersecurity4executives.com")

    with patch("sendgrid.SendGridAPIClient") as MockClient:
        MockClient.return_value.send.side_effect = Exception("boom")
        assert notifications.send_email("scout@example.com", "Subject", "<p>hi</p>") is False


# ---- notify_work_order_event decision logic ----
# Design correction (8/2/26): no opt-in preference selector — always
# notifies by both SMS and email whenever the corresponding contact
# info exists on the WO. See notify_work_order_event's docstring.

def test_sends_both_sms_and_email_automatically(db, asset):
    wo = _make_wo(db, asset)
    with patch.object(notifications, "send_sms") as sms, patch.object(notifications, "send_email") as email:
        notifications.notify_work_order_event(wo, "submitted")
        sms.assert_called_once()
        assert sms.call_args[0][0] == "555-0100"
        email.assert_called_once()
        assert email.call_args[0][0] == "scout@example.com"


def test_no_sms_when_phone_missing(db, asset):
    """Not guaranteed present for every creation path (e.g. the
    authenticated LOC/tech endpoint doesn't require it), even though
    the public Submit WO form does."""
    wo = crud.create_work_order(
        db, schemas.WorkOrderCreate(
            requester_name="Scout", requester_email="scout@example.com",
            asset_id=asset.id, description="x", priority="Next Day",
        ),
    )
    with patch.object(notifications, "send_sms") as sms, patch.object(notifications, "send_email") as email:
        notifications.notify_work_order_event(wo, "submitted")
        sms.assert_not_called()
        email.assert_called_once()


def test_no_email_when_email_missing(db, asset):
    wo = crud.create_work_order(
        db, schemas.WorkOrderCreate(
            requester_name="Scout", requester_phone="555-0100",
            asset_id=asset.id, description="x", priority="Next Day",
        ),
    )
    with patch.object(notifications, "send_sms") as sms, patch.object(notifications, "send_email") as email:
        notifications.notify_work_order_event(wo, "submitted")
        sms.assert_called_once()
        email.assert_not_called()


def test_distinct_poc_also_texted(db, asset):
    wo = _make_wo(
        db, asset,
        poc_is_requester=False, poc_name="Helen", poc_phone="555-0200",
    )
    with patch.object(notifications, "send_sms") as sms:
        notifications.notify_work_order_event(wo, "submitted")
        assert sms.call_count == 2
        called_numbers = {c[0][0] for c in sms.call_args_list}
        assert called_numbers == {"555-0100", "555-0200"}


def test_poc_never_emailed_no_field_exists(db, asset):
    """POC has no email field in the data model at all — only ever
    reachable by SMS."""
    wo = _make_wo(
        db, asset,
        poc_is_requester=False, poc_name="Helen", poc_phone="555-0200",
    )
    with patch.object(notifications, "send_email") as email:
        notifications.notify_work_order_event(wo, "submitted")
        email.assert_called_once()
        # Only the requester's email — never a POC email, since none exists.
        assert email.call_args[0][0] == "scout@example.com"


def test_invalid_event_name_is_noop(db, asset):
    wo = _make_wo(db, asset)
    with patch.object(notifications, "send_sms") as sms, patch.object(notifications, "send_email") as email:
        notifications.notify_work_order_event(wo, "bogus")
        sms.assert_not_called()
        email.assert_not_called()


# ---- Trigger points actually fire from crud.py ----

def test_submission_triggers_notification(db, asset):
    with patch.object(notifications, "notify_work_order_event") as mock_notify:
        wo = _make_wo(db, asset)
        mock_notify.assert_called_once_with(wo, "submitted")


def test_wip_transition_triggers_notification(db, asset, admin_user):
    wo = _make_wo(db, asset)
    with patch.object(notifications, "notify_work_order_event") as mock_notify:
        crud.change_status(db, wo, schemas.StatusChangeRequest(status="Work In Progress"), changed_by=admin_user.id)
        mock_notify.assert_called_once_with(wo, "in_progress")


def test_closed_transition_triggers_notification(db, asset, admin_user):
    wo = _make_wo(db, asset)
    with patch.object(notifications, "notify_work_order_event") as mock_notify:
        crud.change_status(
            db, wo, schemas.StatusChangeRequest(status="Closed, Completed", note="done"), changed_by=admin_user.id
        )
        mock_notify.assert_called_once_with(wo, "closed")


def test_noop_status_reselect_does_not_retrigger(db, asset, admin_user):
    """Re-saving the same status (e.g. an unrelated edit that happens
    to resend the current status) must not re-send a notification."""
    wo = _make_wo(db, asset)
    crud.change_status(db, wo, schemas.StatusChangeRequest(status="Assigned"), changed_by=admin_user.id)
    with patch.object(notifications, "notify_work_order_event") as mock_notify:
        crud.change_status(db, wo, schemas.StatusChangeRequest(status="Assigned"), changed_by=admin_user.id)
        mock_notify.assert_not_called()


def test_worker_complete_triggers_notification(client, auth_headers, wo_payload, team, db):
    worker, pin = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Riley"))
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id, "person_id": worker.id}, headers=auth_headers)
    login = client.post("/public/worker-login", json={"worker_id": worker.id, "pin": pin})
    worker_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    with patch.object(notifications, "notify_work_order_event") as mock_notify:
        client.post(f"/work-orders/{wo['id']}/complete", json={}, headers=worker_headers)
        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][1] == "closed"


def test_save_work_order_status_transition_triggers_notification(client, auth_headers, wo_payload, team):
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()

    with patch.object(notifications, "notify_work_order_event") as mock_notify:
        client.post(
            f"/work-orders/{wo['id']}/save",
            json={"team_id": team.id, "status": "Work In Progress"},
            headers=auth_headers,
        )
        # Called once for the WIP transition — team assignment doesn't
        # trigger a notification event of its own.
        events = [c.args[1] for c in mock_notify.call_args_list]
        assert events == ["in_progress"]
