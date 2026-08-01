"""
Tests for enhancement backlog Phase 20 (NJ2026_Work_Order_System_PRD.md
§17#15): the public, no-sign-in-required "management" dashboard —
aggregate-only endpoints under /public/dashboard/*.
"""
from app import crud, schemas, rate_limit


def _make_wo(db, asset, priority="Next Day"):
    return crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority=priority,
        ),
    )


def test_public_kpis_no_auth_required(client, asset, db):
    _make_wo(db, asset)
    resp = client.get("/public/dashboard/kpis")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_public_breakdowns_no_auth_required(client, asset, db):
    _make_wo(db, asset, priority="Immediate")
    resp = client.get("/public/dashboard/breakdowns")
    assert resp.status_code == 200
    assert resp.json()["by_priority"]["Immediate"] == 1


def test_public_trend_no_auth_required(client, asset, db):
    _make_wo(db, asset)
    resp = client.get("/public/dashboard/trend?days=7")
    assert resp.status_code == 200
    assert len(resp.json()) == 7


def test_public_trend_days_bounded(client):
    resp = client.get("/public/dashboard/trend?days=9999")
    assert resp.status_code == 200
    assert len(resp.json()) == 90  # clamped to the max, not the raw input

    resp2 = client.get("/public/dashboard/trend?days=0")
    assert resp2.status_code == 200
    assert len(resp2.json()) == 1  # clamped to the min


def test_public_kpis_exposes_no_wo_level_detail(client, asset, db):
    """The whole point of this endpoint: aggregate counts only, never
    anything identifying a specific work order or requester."""
    _make_wo(db, asset)
    resp = client.get("/public/dashboard/kpis")
    body = resp.json()
    # KPIOut's actual fields are all counts/rates — this just double
    # -checks no WO-shaped keys (description, requester_name, wo_number,
    # etc.) ever sneak in via a future schema change.
    forbidden_keys = {"description", "requester_name", "requester_email",
                       "requester_phone", "wo_number", "notes", "history"}
    assert not (forbidden_keys & set(body.keys()))


def test_public_dashboard_rate_limited(client, asset, db):
    """Same per-IP rate limiter as the phone-based status lookup —
    confirms it's actually wired up, not just imported."""
    rate_limit.reset_all()
    # Exhaust the limit, then confirm the next call is rejected.
    tripped = False
    for _ in range(200):
        resp = client.get("/public/dashboard/kpis")
        if resp.status_code == 429:
            tripped = True
            assert "Retry-After" in resp.headers
            break
    assert tripped, "expected the rate limiter to eventually return 429"
