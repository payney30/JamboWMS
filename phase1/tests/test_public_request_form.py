"""
Tests for app/routers/public.py — the unauthenticated requester form
(PRD 4.1). No login/auth_headers fixture needed for any of these; that's
the point.
"""
import io

import pytest

from app import models, rate_limit


def _base_form(asset, **overrides):
    form = {
        "requester_name": "Scout Leader",
        "requester_email": "leader@example.com",
        "asset_id": str(asset.id),
        "work_type": "NJ Maintenance",
        "description": "Leaky faucet in the latrine block",
        "priority": "Medium",
        "website": "",  # honeypot, left blank like a real user
    }
    form.update(overrides)
    return form


def test_public_can_list_assets_without_auth(client, asset):
    resp = client.get("/public/assets")
    assert resp.status_code == 200
    names = [a["name"] for a in resp.json()]
    assert asset.name in names
    # thinner than the authenticated /assets response — no camp_letter
    assert "camp_letter" not in resp.json()[0]


def test_public_submission_creates_a_work_order(client, asset, db):
    resp = client.post("/public/work-orders", data=_base_form(asset))
    assert resp.status_code == 201
    wo_number = resp.json()["wo_number"]
    assert wo_number.startswith("WO-")

    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == wo_number).first()
    assert wo is not None
    assert wo.status == "Requested"
    assert wo.requester_name == "Scout Leader"


def test_public_submission_requires_name_and_description(client, asset):
    resp = client.post("/public/work-orders", data=_base_form(asset, requester_name="   "))
    assert resp.status_code == 400

    resp2 = client.post("/public/work-orders", data=_base_form(asset, description=""))
    assert resp2.status_code == 400


def test_public_submission_rejects_invalid_priority(client, asset):
    resp = client.post("/public/work-orders", data=_base_form(asset, priority="Whenever"))
    assert resp.status_code == 400


def test_public_submission_rejects_invalid_work_type(client, asset):
    resp = client.post("/public/work-orders", data=_base_form(asset, work_type="Not A Real Type"))
    assert resp.status_code == 400


def test_public_submission_accepts_blank_work_type(client, asset):
    resp = client.post("/public/work-orders", data=_base_form(asset, work_type=""))
    assert resp.status_code == 201


def test_honeypot_returns_fake_success_without_creating_a_record(client, asset, db):
    before = db.query(models.WorkOrder).count()
    resp = client.post("/public/work-orders", data=_base_form(asset, website="http://spam.example"))
    assert resp.status_code == 201
    assert resp.json()["wo_number"] == "WO-00000"
    after = db.query(models.WorkOrder).count()
    assert after == before  # nothing was actually created


def test_public_submission_rate_limited_after_max(client, asset):
    for _ in range(rate_limit.PUBLIC_WO_MAX_SUBMISSIONS):
        resp = client.post("/public/work-orders", data=_base_form(asset))
        assert resp.status_code == 201

    limited = client.post("/public/work-orders", data=_base_form(asset))
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


def test_public_submission_with_photo_attachment(client, asset, db):
    fake_image = io.BytesIO(b"\xff\xd8\xff\xe0not a real jpeg but has a jpeg content-type")
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset),
        files={"files": ("photo.jpg", fake_image, "image/jpeg")},
    )
    assert resp.status_code == 201
    wo_number = resp.json()["wo_number"]
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == wo_number).first()
    assert len(wo.attachments) == 1
    assert wo.attachments[0].file_url.startswith("/uploads/")


def test_public_submission_skips_non_image_files_silently(client, asset, db):
    fake_pdf = io.BytesIO(b"%PDF-1.4 not really a pdf")
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset),
        files={"files": ("doc.pdf", fake_pdf, "application/pdf")},
    )
    assert resp.status_code == 201  # submission still succeeds
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == resp.json()["wo_number"]).first()
    assert len(wo.attachments) == 0  # but the non-image file wasn't kept


def test_public_lookup_returns_status_with_matching_email(client, asset):
    create = client.post("/public/work-orders", data=_base_form(asset, requester_email="leader@example.com"))
    wo_number = create.json()["wo_number"]

    resp = client.get("/public/work-orders/lookup", params={"wo_number": wo_number, "email": "leader@example.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["wo_number"] == wo_number
    assert body["status"] == "Requested"


def test_public_lookup_case_insensitive_email(client, asset):
    create = client.post("/public/work-orders", data=_base_form(asset, requester_email="Leader@Example.com"))
    wo_number = create.json()["wo_number"]

    resp = client.get("/public/work-orders/lookup", params={"wo_number": wo_number, "email": "leader@example.com"})
    assert resp.status_code == 200


def test_public_lookup_404_on_email_mismatch(client, asset):
    create = client.post("/public/work-orders", data=_base_form(asset, requester_email="leader@example.com"))
    wo_number = create.json()["wo_number"]

    resp = client.get("/public/work-orders/lookup", params={"wo_number": wo_number, "email": "someone-else@example.com"})
    assert resp.status_code == 404


def test_public_lookup_404_on_unknown_wo_number(client, asset):
    resp = client.get("/public/work-orders/lookup", params={"wo_number": "WO-99999999", "email": "nobody@example.com"})
    assert resp.status_code == 404


def test_notify_preference_persists_and_is_visible_on_the_wo(client, asset, db):
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset, notify_preference="both", requester_phone="555-0100"),
    )
    assert resp.status_code == 201
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == resp.json()["wo_number"]).first()
    assert wo.notify_preference == "both"


def test_notify_preference_rejects_unknown_value(client, asset):
    resp = client.post("/public/work-orders", data=_base_form(asset, notify_preference="carrier_pigeon"))
    assert resp.status_code == 400


def test_notify_preference_text_requires_a_phone_number(client, asset):
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset, notify_preference="text", requester_phone=""),
    )
    assert resp.status_code == 400


def test_notify_preference_email_requires_an_email(client, asset):
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset, notify_preference="email", requester_email=""),
    )
    assert resp.status_code == 400


def test_notify_preference_is_optional(client, asset):
    resp = client.post("/public/work-orders", data=_base_form(asset, notify_preference=""))
    assert resp.status_code == 201
