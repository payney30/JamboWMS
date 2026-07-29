"""
Tests for enhancement backlog Phase 12 (NJ2026_Work_Order_System_PRD.md
§16#5): exact-location (asset_id) filter on GET /work-orders, added to
back the technician queue's new LocationPicker filter.
"""
from app import models


def test_asset_id_filter_narrows_to_exact_location(client, auth_headers, wo_payload, db, asset):
    wo_at_asset = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()

    other_asset = models.Asset(name="Other Location", location_group="Program Areas", is_active=True)
    db.add(other_asset)
    db.commit()
    db.refresh(other_asset)
    other_payload = dict(wo_payload, asset_id=other_asset.id)
    wo_at_other = client.post("/work-orders", json=other_payload, headers=auth_headers).json()

    resp = client.get("/work-orders", params={"asset_id": asset.id}, headers=auth_headers)
    ids = [w["id"] for w in resp.json()]
    assert wo_at_asset["id"] in ids
    assert wo_at_other["id"] not in ids


def test_asset_id_filter_combines_with_team_scope_for_techs(
    client, tech_auth_headers, auth_headers, wo_payload, team, asset
):
    wo = client.post("/work-orders", json=wo_payload, headers=auth_headers).json()
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    resp = client.get("/work-orders", params={"asset_id": asset.id}, headers=tech_auth_headers)
    assert resp.status_code == 200
    assert any(w["id"] == wo["id"] for w in resp.json())
