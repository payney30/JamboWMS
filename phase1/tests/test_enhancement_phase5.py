"""
Tests for enhancement backlog Phase 5 (NJ2026_Work_Order_System_PRD.md
§14#10): SLA deadline flagging — sla_warn_at/sla_deadline fields, and
the approaching_deadline / past_deadline inbox filters + KPI counts.
"""
import datetime as dt

from app import crud, schemas


def _make_wo(db, asset, priority, created_at, status=None):
    wo = crud.create_work_order(
        db,
        schemas.WorkOrderCreate(
            requester_name="Scout", asset_id=asset.id, description="x", priority=priority
        ),
    )
    wo.created_at = created_at
    if status:
        wo.status = status
    db.commit()
    return wo


def test_sla_deadline_and_warn_at_computed_from_priority(db, asset):
    now = dt.datetime.utcnow()
    wo = _make_wo(db, asset, "Same Day", now)  # 6-hour SLA window
    assert wo.sla_deadline == now + dt.timedelta(hours=6)
    assert wo.sla_warn_at == now + dt.timedelta(hours=4.5)  # 75% of 6 hours


# Enhancement backlog Phase 18 (PRD §13#15 follow-up, 7/30/26): removed
# test_sla_math_still_works_for_old_style_priority_names — it existed
# specifically to confirm SLA math still worked for already-existing
# old-named WOs, a backward-compatibility guarantee that no longer
# applies now that migration f4a8d1c6e3b2 converted every row in the
# system to the new names and the DB CHECK constraint no longer accepts
# old values at all (confirmed: constructing a WorkOrder with an
# old-style priority now raises an IntegrityError, which is correct).
# Keeping a test for a capability that was deliberately removed would
# just be testing that removal didn't happen, which isn't useful.


def test_sla_fields_exposed_on_list_endpoint(client, auth_headers, db, asset):
    now = dt.datetime.utcnow()
    wo = _make_wo(db, asset, "Immediate", now)  # 2-hour SLA window
    resp = client.get("/work-orders", headers=auth_headers)
    row = next(w for w in resp.json() if w["id"] == wo.id)
    assert row["sla_deadline"] is not None
    assert row["sla_warn_at"] is not None


def test_approaching_deadline_filter_finds_wo_past_75_percent(client, auth_headers, db, asset):
    now = dt.datetime.utcnow()
    # High priority = 6h window; 5h elapsed = past the 4.5h (75%) mark,
    # not yet past the full 6h deadline.
    approaching = _make_wo(db, asset, "Same Day", now - dt.timedelta(hours=5))
    not_yet = _make_wo(db, asset, "Same Day", now - dt.timedelta(hours=1))

    resp = client.get("/work-orders", params={"approaching_deadline": "true"}, headers=auth_headers)
    ids = [w["id"] for w in resp.json()]
    assert approaching.id in ids
    assert not_yet.id not in ids


def test_past_deadline_filter_finds_wo_past_full_window(client, auth_headers, db, asset):
    now = dt.datetime.utcnow()
    overdue = _make_wo(db, asset, "Immediate", now - dt.timedelta(hours=3))  # 2h window, 3h elapsed
    still_ok = _make_wo(db, asset, "Immediate", now - dt.timedelta(minutes=30))

    resp = client.get("/work-orders", params={"past_deadline": "true"}, headers=auth_headers)
    ids = [w["id"] for w in resp.json()]
    assert overdue.id in ids
    assert still_ok.id not in ids


def test_deadline_filters_exclude_closed_wos(client, auth_headers, db, asset):
    now = dt.datetime.utcnow()
    overdue_but_closed = _make_wo(
        db, asset, "Immediate", now - dt.timedelta(hours=10), status="Closed, Completed"
    )
    resp = client.get("/work-orders", params={"past_deadline": "true"}, headers=auth_headers)
    ids = [w["id"] for w in resp.json()]
    assert overdue_but_closed.id not in ids


def test_deadline_filters_are_mutually_exclusive_buckets(client, auth_headers, db, asset):
    """A WO can't be both "approaching" and "past" at the same time."""
    now = dt.datetime.utcnow()
    overdue = _make_wo(db, asset, "Next Day", now - dt.timedelta(hours=30))  # 24h window
    approaching_resp = client.get(
        "/work-orders", params={"approaching_deadline": "true"}, headers=auth_headers
    )
    past_resp = client.get("/work-orders", params={"past_deadline": "true"}, headers=auth_headers)
    assert overdue.id not in [w["id"] for w in approaching_resp.json()]
    assert overdue.id in [w["id"] for w in past_resp.json()]


def test_kpis_include_deadline_counts(client, auth_headers, db, asset):
    now = dt.datetime.utcnow()
    _make_wo(db, asset, "Immediate", now - dt.timedelta(hours=3))  # past deadline
    _make_wo(db, asset, "Same Day", now - dt.timedelta(hours=5))  # approaching

    resp = client.get("/dashboard/kpis", headers=auth_headers)
    body = resp.json()
    assert body["past_deadline"] >= 1
    assert body["approaching_deadline"] >= 1
