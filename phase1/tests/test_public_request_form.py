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
        "priority": "Next Day",
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
    assert wo_number.isdigit()  # no more "WO-" prefix, see PRD §14#13

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
    assert resp.json()["wo_number"] == "00000"
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


def test_public_submission_with_pdf_attachment(client, asset, db):
    """Enhancement backlog Phase 6 (PRD §13#9): PDFs are now an allowed
    attachment type alongside photos (e.g. a packing list or formal
    transportation request)."""
    fake_pdf = io.BytesIO(b"%PDF-1.4 not really a pdf")
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset),
        files={"files": ("doc.pdf", fake_pdf, "application/pdf")},
    )
    assert resp.status_code == 201
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == resp.json()["wo_number"]).first()
    assert len(wo.attachments) == 1
    assert wo.attachments[0].file_url.endswith(".pdf")


def test_public_submission_skips_disallowed_file_types_silently(client, asset, db):
    fake_doc = io.BytesIO(b"not an allowed type")
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset),
        files={"files": ("doc.docx", fake_doc, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert resp.status_code == 201  # submission still succeeds
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == resp.json()["wo_number"]).first()
    assert len(wo.attachments) == 0  # but the disallowed file wasn't kept


def test_public_submission_rejects_more_than_five_files(client, asset, db):
    """End-to-end testing 8/10/26: request.html now caps attachments at 5
    client-side, but the server-side cap (public.py MAX_FILES_PER_SUBMISSION)
    is the real guarantee — a client that bypasses/ignores the UI limit
    must still be rejected, and cleanly, with no partial work order left
    behind."""
    before = db.query(models.WorkOrder).count()
    files = [
        ("files", (f"photo{i}.jpg", io.BytesIO(b"\xff\xd8\xff\xe0fake jpeg"), "image/jpeg"))
        for i in range(6)
    ]
    resp = client.post("/public/work-orders", data=_base_form(asset), files=files)
    assert resp.status_code == 400
    assert "5" in resp.json()["detail"]
    after = db.query(models.WorkOrder).count()
    assert after == before  # rejected before any WO or attachment was created


def test_public_submission_accepts_exactly_five_files(client, asset, db):
    """The cap is <= 5, not < 5 — five files should still succeed."""
    files = [
        ("files", (f"photo{i}.jpg", io.BytesIO(b"\xff\xd8\xff\xe0fake jpeg"), "image/jpeg"))
        for i in range(5)
    ]
    resp = client.post("/public/work-orders", data=_base_form(asset), files=files)
    assert resp.status_code == 201
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == resp.json()["wo_number"]).first()
    assert len(wo.attachments) == 5


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


def test_public_lookup_matches_dashed_stored_with_plain_digit_search(client, asset):
    """PRD §13#10: a requester should be able to search with either
    xxx-xxx-xxxx or a bare 10-digit string, regardless of how the
    number was originally stored — both directions."""
    client.post("/public/work-orders", data=_base_form(asset, requester_phone="555-010-0100"))
    plain = client.get("/public/work-orders/lookup", params={"phone": "5550100100"})
    assert plain.status_code == 200
    assert len(plain.json()) == 1


def test_public_lookup_matches_plain_stored_with_dashed_search(client, asset):
    client.post("/public/work-orders", data=_base_form(asset, requester_phone="5550200200"))
    dashed = client.get("/public/work-orders/lookup", params={"phone": "555-020-0200"})
    assert dashed.status_code == 200
    assert len(dashed.json()) == 1


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


def test_public_lookup_includes_assigned_team(client, asset, db, team):
    """Enhancement backlog Phase 11 (PRD §13#7)."""
    create = client.post("/public/work-orders", data=_base_form(asset, requester_phone="555-0450"))
    wo_number = create.json()["wo_number"]
    wo = db.query(models.WorkOrder).filter(models.WorkOrder.wo_number == wo_number).first()
    wo.assigned_team_id = team.id
    db.commit()

    resp = client.get("/public/work-orders/lookup", params={"phone": "555-0450"})
    assert resp.status_code == 200
    assert resp.json()[0]["assigned_team"]["name"] == team.name


def test_public_lookup_assigned_team_null_when_unassigned(client, asset):
    client.post("/public/work-orders", data=_base_form(asset, requester_phone="555-0460"))
    resp = client.get("/public/work-orders/lookup", params={"phone": "555-0460"})
    assert resp.status_code == 200
    assert resp.json()[0]["assigned_team"] is None


# ---- Enhancement backlog Phase 2 (PRD §13#5): free-text search scoped to phone ----

def test_lookup_search_matches_description(client, asset):
    client.post("/public/work-orders", data=_base_form(
        asset, requester_phone="555-0500", description="Leaky faucet in the shower house"
    ))
    client.post("/public/work-orders", data=_base_form(
        asset, requester_phone="555-0500", description="Wifi is down in the office"
    ))

    resp = client.get("/public/work-orders/lookup", params={"phone": "555-0500", "search": "faucet"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert "faucet" in body[0]["description"].lower()


def test_lookup_search_matches_location(client, asset):
    client.post("/public/work-orders", data=_base_form(asset, requester_phone="555-0600"))

    resp = client.get("/public/work-orders/lookup", params={"phone": "555-0600", "search": asset.name[:4]})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_lookup_search_is_case_insensitive(client, asset):
    client.post("/public/work-orders", data=_base_form(
        asset, requester_phone="555-0700", description="LEAKY Faucet"
    ))
    resp = client.get("/public/work-orders/lookup", params={"phone": "555-0700", "search": "leaky"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_lookup_search_scoped_within_phone_matches_only(client, asset):
    """A search term that matches a WO on a DIFFERENT phone number must
    not leak into these results — search narrows within the phone match,
    it never widens beyond it."""
    client.post("/public/work-orders", data=_base_form(
        asset, requester_phone="555-0800", description="Broken generator"
    ))
    client.post("/public/work-orders", data=_base_form(
        asset, requester_phone="555-0801", description="Broken generator"
    ))

    resp = client.get("/public/work-orders/lookup", params={"phone": "555-0800", "search": "generator"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_lookup_search_404_when_no_match_within_phone(client, asset):
    client.post("/public/work-orders", data=_base_form(asset, requester_phone="555-0900"))
    resp = client.get("/public/work-orders/lookup", params={"phone": "555-0900", "search": "nonexistent-term-xyz"})
    assert resp.status_code == 404


# ---- Bug fix (PRD §14#20): public request-types endpoint ----
# The Submit WO form's "kind of issue" dropdown used to hardcode a fixed
# set of work-type strings. Request types are admin-editable
# (PRD 4.5c) — if an admin's active list ever diverges from that
# hardcoded guess, every submission using one of the stale values fails
# with a 400. This endpoint is the fix: both the dropdown and the
# validator now read from the same source of truth.

def test_public_request_types_returns_active_names(client, db):
    from app import models
    db.add(models.RequestType(name="Custom Type A", sort_order=10, is_active=True))
    db.add(models.RequestType(name="Custom Type B", sort_order=11, is_active=True))
    db.add(models.RequestType(name="Retired Type", sort_order=12, is_active=False))
    db.commit()

    resp = client.get("/public/request-types")
    assert resp.status_code == 200
    names = resp.json()
    assert "Custom Type A" in names
    assert "Custom Type B" in names
    assert "Retired Type" not in names  # inactive types are excluded
    # the four standard seeded types (see conftest._seed_request_types)
    # are also present, since this endpoint returns everything active
    assert "NJ Maintenance" in names


def test_submission_fails_if_admin_deactivates_a_hardcoded_type(client, asset, db):
    """Reproduces the actual bug: every test gets the four standard
    request types seeded automatically (see conftest._seed_request_types)
    — which is exactly why this drift risk went unnoticed. This test
    explicitly simulates an admin deactivating one of them (a real,
    existing admin feature, PRD 4.5c) to prove the failure mode a
    hardcoded frontend dropdown is exposed to: a submission using that
    now-inactive name gets rejected. Fetching /public/request-types
    dynamically (the fix) avoids this because it only ever offers
    currently-active names in the first place."""
    from app import models
    rt = db.query(models.RequestType).filter_by(name="NJ Maintenance").first()
    rt.is_active = False
    db.commit()

    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset, work_type="NJ Maintenance"),
    )
    assert resp.status_code == 400
    assert "invalid or inactive request type" in resp.json()["detail"]

    # And confirm the fix's other half: the public endpoint no longer
    # offers that name, so a form built from it wouldn't present this
    # choice to begin with.
    types_resp = client.get("/public/request-types")
    assert "NJ Maintenance" not in types_resp.json()


def test_submission_succeeds_with_currently_active_type(client, asset, db):
    from app import models
    db.add(models.RequestType(name="Facilities", sort_order=0, is_active=True))
    db.commit()

    resp = client.post("/public/work-orders", data=_base_form(asset, work_type="Facilities"))
    assert resp.status_code == 201


def test_submission_with_blank_work_type_always_succeeds(client, asset):
    """The 'Other / not sure' sentinel (blank string) is valid
    regardless of the request_types table's state — the fail-open path
    if the dropdown's fetch fails entirely."""
    resp = client.post("/public/work-orders", data=_base_form(asset, work_type=""))
    assert resp.status_code == 201


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
