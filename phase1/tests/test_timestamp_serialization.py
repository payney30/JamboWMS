"""
Regression test for a real bug: naive UTC datetimes serialize without a
timezone marker (e.g. "2026-07-27T18:23:22"), and per the JS spec,
`new Date(...)` on a date-time string with no timezone parses it as LOCAL
time, not UTC. That silently shifted every date comparison and displayed
time in the frontend by the browser's UTC offset — e.g. the LOC triage
"Opened Today" KPI card (computed server-side in UTC) disagreeing with the
same filter applied client-side against locally-misparsed timestamps.
"""
from app import crud, schemas


def test_work_order_created_at_serializes_with_utc_marker(client, auth_headers, asset):
    _make_wo_via_api = client.post(
        "/work-orders",
        json={"requester_name": "Scout", "asset_id": asset.id, "description": "x", "priority": "Medium"},
        headers=auth_headers,
    )
    assert _make_wo_via_api.status_code == 201, _make_wo_via_api.text
    created_at = _make_wo_via_api.json()["created_at"]
    assert created_at.endswith("+00:00") or created_at.endswith("Z"), (
        f"created_at={created_at!r} has no UTC marker — browsers will misparse "
        f"it as local time"
    )


def test_status_history_changed_at_has_utc_marker(client, auth_headers, asset):
    wo = client.post(
        "/work-orders",
        json={"requester_name": "Scout", "asset_id": asset.id, "description": "x", "priority": "Medium"},
        headers=auth_headers,
    ).json()
    detail = client.get(f"/work-orders/{wo['id']}", headers=auth_headers).json()
    assert detail["history"], "expected at least one history row from creation"
    changed_at = detail["history"][0]["changed_at"]
    assert changed_at.endswith("+00:00") or changed_at.endswith("Z")


def test_dashboard_kpis_opened_today_matches_a_client_side_utc_filter(client, auth_headers, asset):
    """The bug report in one sentence: KPI card said 1, clicking it (client
    filters by created_at) showed 0. This asserts the two numbers agree
    when the client does the comparison correctly (UTC-to-UTC), which is
    only possible if created_at carries a UTC marker."""
    import datetime as dt

    resp = client.post(
        "/work-orders",
        json={"requester_name": "Scout", "asset_id": asset.id, "description": "x", "priority": "Medium"},
        headers=auth_headers,
    )
    wo = resp.json()

    kpis = client.get("/dashboard/kpis", headers=auth_headers).json()
    assert kpis["opened_today"] == 1

    # Simulate the frontend's isToday() check, but done correctly against
    # a UTC-aware timestamp (what UTCDateTime now guarantees).
    created = dt.datetime.fromisoformat(wo["created_at"])
    assert created.tzinfo is not None
    today_utc = dt.datetime.now(dt.timezone.utc).date()
    assert created.astimezone(dt.timezone.utc).date() == today_utc
