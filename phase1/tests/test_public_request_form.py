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
        "requester_phone": "555-0100",
        "asset_id": str(asset.id),
        "work_type": "NJ Maintenance",
        "description": "Leaky faucet in the latrine block",
        "priority": "Medium",
        "website": "",  # honeypot, left blank like a real user
        # poc_is_requester defaults to "true" server-side if omitted, but
        # tests set it explicitly here so the base form matches what the
        # real form always sends.
        "poc_is_requester": "true",
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


def test_public_submission_requires_email(client, asset):
    resp = client.post("/public/work-orders", data=_base_form(asset, requester_email=""))
    assert resp.status_code == 400

    resp2 = client.post("/public/work-orders", data=_base_form(asset, requester_email="   "))
    assert resp2.status_code == 400


def test_public_submission_requires_phone(client, asset):
    resp = client.post("/public/work-orders", data=_base_form(asset, requester_phone=""))
    assert resp.status_code == 400

    resp2 = client.post("/public/work-orders", data=_base_form(asset, requester_phone="   "))
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


def test_public_lookup_returns_status_by_phone(client, asset):
    create = client.post("/public/work-orders", data=_base_form(asset, requester_phone="555-0100"))
    wo_number = create.json()["wo_number"]

    resp = client.get("/public/work-orders/lookup", params={"phone": "555-0100"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["wo_number"] == wo_number
    assert body[0]["status"] == "Requested"


def test_public_lookup_by_phone_ignores_formatting_differences(client, asset):
    client.post("/public/work-orders", data=_base_form(asset, requester_phone="(555) 010-0099"))

    resp = client.get("/public/work-orders/lookup", params={"phone": "555-010-0099"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_public_lookup_by_phone_returns_all_matching_work_orders(client, asset):
    client.post("/public/work-orders", data=_base_form(asset, requester_phone="555-0200"))
    client.post("/public/work-orders", data=_base_form(asset, requester_phone="555-0200", description="Second issue"))
    client.post("/public/work-orders", data=_base_form(asset, requester_phone="555-9999"))  # different phone

    resp = client.get("/public/work-orders/lookup", params={"phone": "555-0200"})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_public_lookup_by_phone_matches_poc_phone(client, asset):
    """Enhancement backlog Phase 1 (PRD §13#4): a delegated POC can look
    up a WO with their own phone number, not just the original requester's."""
    resp = client.post(
        "/public/work-orders",
        data=_base_form(
            asset,
            poc_is_requester="false",
            poc_name="Area Lead",
            poc_phone="555-0300",
        ),
    )
    assert resp.status_code == 201

    lookup = client.get("/public/work-orders/lookup", params={"phone": "555-0300"})
    assert lookup.status_code == 200
    assert len(lookup.json()) == 1


def test_public_lookup_404_on_unknown_phone(client, asset):
    resp = client.get("/public/work-orders/lookup", params={"phone": "555-0000"})
    assert resp.status_code == 404


def test_public_lookup_rejects_too_short_phone(client, asset):
    resp = client.get("/public/work-orders/lookup", params={"phone": "123"})
    assert resp.status_code == 400


def test_public_lookup_includes_note_to_requester(client, asset, db):
    create = client.post("/public/work-orders", data=_base_form(asset, requester_phone="555-0400"))
    wo_number = create.json()["wo_number"]
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == wo_number).first()
    wo.note_to_requester = "A tech is on the way."
    db.commit()

    resp = client.get("/public/work-orders/lookup", params={"phone": "555-0400"})
    assert resp.status_code == 200
    assert resp.json()[0]["note_to_requester"] == "A tech is on the way."


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


def test_poc_defaults_to_requester_when_omitted(client, asset, db):
    form = _base_form(asset)
    del form["poc_is_requester"]  # simulate an older/minimal client that never sends the flag
    resp = client.post("/public/work-orders", data=form)
    assert resp.status_code == 201
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == resp.json()["wo_number"]).first()
    assert wo.poc_is_requester is True
    assert wo.poc_name is None
    assert wo.poc_phone is None


def test_poc_not_requester_requires_name_and_phone(client, asset):
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset, poc_is_requester="false"),
    )
    assert resp.status_code == 400

    resp2 = client.post(
        "/public/work-orders",
        data=_base_form(asset, poc_is_requester="false", poc_name="Area Lead"),
    )
    assert resp2.status_code == 400  # phone still missing


def test_poc_not_requester_persists_name_and_phone(client, asset, db):
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset, poc_is_requester="false", poc_name="Area Lead", poc_phone="555-0199"),
    )
    assert resp.status_code == 201
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == resp.json()["wo_number"]).first()
    assert wo.poc_is_requester is False
    assert wo.poc_name == "Area Lead"
    assert wo.poc_phone == "555-0199"


def test_poc_fields_ignored_when_poc_is_requester_true(client, asset, db):
    """If poc_is_requester is true but a client sends stray poc_name/phone
    anyway, they should be discarded rather than stored, so the two never
    drift out of sync."""
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset, poc_is_requester="true", poc_name="Should Be Ignored", poc_phone="555-0000"),
    )
    assert resp.status_code == 201
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == resp.json()["wo_number"]).first()
    assert wo.poc_name is None
    assert wo.poc_phone is None
