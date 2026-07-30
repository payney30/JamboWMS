"""
Tests for enhancement backlog Phase 15 (NJ2026_Work_Order_System_PRD.md
§13#14 / §14#7): optional geo pin-drop on Submit WO, displayed on the
WO detail screen.
"""


def _base_form(asset, **overrides):
    form = {
        "requester_name": "Scout Leader",
        "requester_email": "leader@example.com",
        "requester_phone": "555-0100",
        "asset_id": str(asset.id),
        "work_type": "NJ Maintenance",
        "description": "Leaky faucet in the latrine block",
        "priority": "Next Day",
        "website": "",
        "poc_is_requester": "true",
    }
    form.update(overrides)
    return form


def test_submission_with_pin_stores_coordinates(client, asset, db):
    from app import models
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset, latitude="37.86012", longitude="-81.13245"),
    )
    assert resp.status_code == 201
    wo = db.query(models.WorkOrder).filter(
        models.WorkOrder.wo_number == resp.json()["wo_number"]
    ).first()
    assert wo.latitude == 37.86012
    assert wo.longitude == -81.13245


def test_submission_without_pin_leaves_coordinates_null(client, asset, db):
    from app import models
    resp = client.post("/public/work-orders", data=_base_form(asset))
    assert resp.status_code == 201
    wo = db.query(models.WorkOrder).filter(
        models.WorkOrder.wo_number == resp.json()["wo_number"]
    ).first()
    assert wo.latitude is None
    assert wo.longitude is None


def test_submission_with_only_one_coordinate_is_ignored(client, asset, db):
    """Both or neither — a stray single value (e.g. a client bug) isn't
    enough to place a pin."""
    from app import models
    resp = client.post("/public/work-orders", data=_base_form(asset, latitude="37.86"))
    assert resp.status_code == 201
    wo = db.query(models.WorkOrder).filter(
        models.WorkOrder.wo_number == resp.json()["wo_number"]
    ).first()
    assert wo.latitude is None
    assert wo.longitude is None


def test_submission_rejects_garbage_coordinates(client, asset):
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset, latitude="not-a-number", longitude="-81.1"),
    )
    assert resp.status_code == 400


def test_authenticated_detail_exposes_pin(client, auth_headers, wo_payload, db):
    from app import models
    wo_payload = dict(wo_payload)
    resp = client.post("/work-orders", json=wo_payload, headers=auth_headers)
    wo_id = resp.json()["id"]
    wo = db.query(models.WorkOrder).get(wo_id)
    wo.latitude = 37.86012
    wo.longitude = -81.13245
    db.commit()

    detail = client.get(f"/work-orders/{wo_id}", headers=auth_headers)
    assert detail.json()["latitude"] == 37.86012
    assert detail.json()["longitude"] == -81.13245


def test_authenticated_detail_pin_null_when_not_set(client, auth_headers, wo_payload):
    resp = client.post("/work-orders", json=wo_payload, headers=auth_headers)
    wo_id = resp.json()["id"]
    detail = client.get(f"/work-orders/{wo_id}", headers=auth_headers)
    assert detail.json()["latitude"] is None
    assert detail.json()["longitude"] is None


def test_out_of_range_latitude_rejected(client, asset):
    resp = client.post(
        "/public/work-orders",
        data=_base_form(asset, latitude="200", longitude="-81.1"),
    )
    assert resp.status_code == 400
