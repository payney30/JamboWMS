"""
Tests for the inventory / supply-lookup feature (PRD §4.5e):
  - crud.parse_inventory_csv / diff_inventory_import / apply_inventory_import
  - GET /inventory/search (LOC triage widget)
  - POST/DELETE /work-orders/{id}/suggested-supplies
  - admin CSV import endpoints (/admin/inventory/preview, /apply)
  - request_types.show_inventory_lookup admin toggle
"""
import io

import pytest

from app import crud, models, schemas


def _login(client, email, password="test-password"):
    resp = client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _csv_bytes(rows, header="ProductName,Description,ProductCustom3,ProductCustom4,Qty On Hand"):
    lines = [header] + rows
    # Prepend a UTF-8 BOM, matching the real source export.
    return ("\ufeff" + "\r\n".join(lines)).encode("utf-8")


BROOM_CSV = _csv_bytes([
    '"999000048","Broom Sweep Straw","Cleaning Items","Accessories","43"',
    '"999000055","Broom Whisk","Cleaning Items","Accessories","0"',
    '"1002041","Aspirin Ec Tab 325mg","","","0"',
])


# ---- crud.parse_inventory_csv ----

def test_parse_inventory_csv_basic_rows():
    rows = crud.parse_inventory_csv(BROOM_CSV)
    assert len(rows) == 3
    broom = next(r for r in rows if r["sku"] == "999000048")
    assert broom["description"] == "Broom Sweep Straw"
    assert broom["category"] == "Cleaning Items"
    assert broom["subcategory"] == "Accessories"
    assert broom["qty_on_hand"] == 43


def test_parse_inventory_csv_strips_thousands_commas():
    csv_bytes = _csv_bytes(['"999000001","Cot Participant","MMS","MMS","7,910"'])
    rows = crud.parse_inventory_csv(csv_bytes)
    assert rows[0]["qty_on_hand"] == 7910


def test_parse_inventory_csv_blank_qty_is_none_not_zero():
    csv_bytes = _csv_bytes(['"0001","Processing Fee","","",""'])
    rows = crud.parse_inventory_csv(csv_bytes)
    assert rows[0]["qty_on_hand"] is None


def test_parse_inventory_csv_skips_blank_sku_rows():
    csv_bytes = _csv_bytes([
        '"999000048","Broom Sweep Straw","","","5"',
        '"","No SKU here","","","1"',
    ])
    rows = crud.parse_inventory_csv(csv_bytes)
    assert len(rows) == 1


def test_parse_inventory_csv_rejects_unrecognized_format():
    with pytest.raises(Exception):
        crud.parse_inventory_csv(b"col_a,col_b\n1,2\n")


# ---- crud.diff_inventory_import / apply_inventory_import ----

def test_apply_inventory_import_inserts_new_items(db):
    rows = crud.parse_inventory_csv(BROOM_CSV)
    diff = crud.diff_inventory_import(db, rows)
    assert diff["added_count"] == 3
    assert diff["changed_count"] == 0
    assert diff["removed_count"] == 0

    result = crud.apply_inventory_import(db, rows)
    assert result["added_count"] == 3
    assert result["active_total"] == 3
    assert db.query(models.InventoryItem).filter(models.InventoryItem.sku == "999000048").one().qty_on_hand == 43


def test_apply_inventory_import_updates_changed_fields(db):
    crud.apply_inventory_import(db, crud.parse_inventory_csv(BROOM_CSV))

    updated_csv = _csv_bytes([
        '"999000048","Broom Sweep Straw","Cleaning Items","Accessories","10"',
        '"999000055","Broom Whisk","Cleaning Items","Accessories","0"',
        '"1002041","Aspirin Ec Tab 325mg","","","0"',
    ])
    rows = crud.parse_inventory_csv(updated_csv)
    diff = crud.diff_inventory_import(db, rows)
    assert diff["changed_count"] == 1
    assert diff["changed"][0]["sku"] == "999000048"
    assert diff["changed"][0]["diffs"]["qty_on_hand"] == {"old": 43, "new": 10}

    crud.apply_inventory_import(db, rows)
    assert db.query(models.InventoryItem).filter(models.InventoryItem.sku == "999000048").one().qty_on_hand == 10


def test_apply_inventory_import_soft_deletes_missing_skus(db):
    crud.apply_inventory_import(db, crud.parse_inventory_csv(BROOM_CSV))

    smaller_csv = _csv_bytes(['"999000048","Broom Sweep Straw","Cleaning Items","Accessories","43"'])
    rows = crud.parse_inventory_csv(smaller_csv)
    diff = crud.diff_inventory_import(db, rows)
    assert diff["removed_count"] == 2

    crud.apply_inventory_import(db, rows)
    whisk = db.query(models.InventoryItem).filter(models.InventoryItem.sku == "999000055").one()
    assert whisk.active is False
    # never hard-deleted
    assert db.query(models.InventoryItem).count() == 3


def test_apply_inventory_import_reactivates_returning_sku(db):
    crud.apply_inventory_import(db, crud.parse_inventory_csv(BROOM_CSV))
    smaller_csv = _csv_bytes(['"999000048","Broom Sweep Straw","Cleaning Items","Accessories","43"'])
    crud.apply_inventory_import(db, crud.parse_inventory_csv(smaller_csv))

    # Whisk is now soft-deleted; re-import the full file and it should
    # come back active, flagged as 'reactivated' in the diff.
    rows = crud.parse_inventory_csv(BROOM_CSV)
    diff = crud.diff_inventory_import(db, rows)
    whisk_diff = next(c for c in diff["changed"] if c["sku"] == "999000055")
    assert whisk_diff["reactivated"] is True

    crud.apply_inventory_import(db, rows)
    whisk = db.query(models.InventoryItem).filter(models.InventoryItem.sku == "999000055").one()
    assert whisk.active is True


# ---- Admin CSV import endpoints ----

def test_admin_inventory_preview_and_apply(client, auth_headers):
    files = {"file": ("inventory.csv", io.BytesIO(BROOM_CSV), "text/csv")}
    preview = client.post("/admin/inventory/preview", files=files, headers=auth_headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["added_count"] == 3

    files = {"file": ("inventory.csv", io.BytesIO(BROOM_CSV), "text/csv")}
    apply_resp = client.post("/admin/inventory/apply", files=files, headers=auth_headers)
    assert apply_resp.status_code == 200, apply_resp.text
    assert apply_resp.json()["active_total"] == 3

    # Preview doesn't apply on its own — confirm nothing landed until apply.
    search = client.get("/inventory/search?q=broom&include_zero=true", headers=auth_headers)
    assert len(search.json()) == 2


def test_admin_inventory_import_requires_admin(client, loc_user):
    headers = _login(client, loc_user.email)
    files = {"file": ("inventory.csv", io.BytesIO(BROOM_CSV), "text/csv")}
    resp = client.post("/admin/inventory/preview", files=files, headers=headers)
    assert resp.status_code == 403


# ---- GET /inventory/search ----

@pytest.fixture()
def loaded_inventory(db):
    crud.apply_inventory_import(db, crud.parse_inventory_csv(BROOM_CSV))


def test_search_inventory_matches_description(client, auth_headers, loaded_inventory):
    resp = client.get("/inventory/search?q=broom", headers=auth_headers)
    assert resp.status_code == 200
    names = {r["sku"] for r in resp.json()}
    # Whisk has 0 on hand and is excluded by default (include_zero=False)
    assert names == {"999000048"}


def test_search_inventory_include_zero_toggle(client, auth_headers, loaded_inventory):
    resp = client.get("/inventory/search?q=broom&include_zero=true", headers=auth_headers)
    names = {r["sku"] for r in resp.json()}
    assert names == {"999000048", "999000055"}


def test_search_inventory_matches_sku(client, auth_headers, loaded_inventory):
    resp = client.get("/inventory/search?q=1002041&include_zero=true", headers=auth_headers)
    assert [r["sku"] for r in resp.json()] == ["1002041"]


def test_search_inventory_requires_loc_or_admin(client, tech_user, loaded_inventory):
    headers = _login(client, tech_user.email)
    resp = client.get("/inventory/search?q=broom", headers=headers)
    assert resp.status_code == 403


def test_search_inventory_unauthenticated_rejected(client, loaded_inventory):
    resp = client.get("/inventory/search?q=broom")
    assert resp.status_code == 401


# ---- WO suggested-supplies ----

@pytest.fixture()
def wo(client, auth_headers, asset):
    resp = client.post(
        "/work-orders",
        json={
            "requester_name": "Scout Leader",
            "requester_email": "leader@example.com",
            "asset_id": asset.id,
            "work_type": "NJ Items/Parts",
            "description": "Need cleaning supplies",
            "priority": "Next Day",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_add_suggested_supplies_creates_rows_and_note(client, auth_headers, wo):
    resp = client.post(
        f"/work-orders/{wo['id']}/suggested-supplies",
        json={"items": [
            {"sku": "999000048", "description": "Broom Sweep Straw", "qty_requested": 2},
            {"sku": "999000055", "description": "Broom Whisk"},
        ]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body) == 2
    assert body[0]["qty_requested"] == 2
    assert body[1]["qty_requested"] == 1  # default

    detail = client.get(f"/work-orders/{wo['id']}", headers=auth_headers).json()
    assert len(detail["suggested_supplies"]) == 2
    supply_note = next(n for n in detail["notes"] if n["note_type"] == "supply_request")
    assert "999000048" in supply_note["note_text"]
    assert "Broom Sweep Straw x 2" in supply_note["note_text"]


def test_remove_suggested_supply(client, auth_headers, wo):
    add = client.post(
        f"/work-orders/{wo['id']}/suggested-supplies",
        json={"items": [{"sku": "999000048", "description": "Broom Sweep Straw"}]},
        headers=auth_headers,
    ).json()
    supply_id = add[0]["id"]

    resp = client.delete(f"/work-orders/{wo['id']}/suggested-supplies/{supply_id}", headers=auth_headers)
    assert resp.status_code == 204

    detail = client.get(f"/work-orders/{wo['id']}", headers=auth_headers).json()
    assert detail["suggested_supplies"] == []
    # the note stays — it's a point-in-time record, not live state
    assert any(n["note_type"] == "supply_request" for n in detail["notes"])


def test_add_suggested_supplies_requires_loc_or_admin(client, auth_headers, tech_user, team, wo):
    # Assign the WO to the tech's team so a 403 is provably about the
    # role restriction, not team scope.
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    tech_headers = _login(client, tech_user.email)
    resp = client.post(
        f"/work-orders/{wo['id']}/suggested-supplies",
        json={"items": [{"sku": "999000048", "description": "Broom Sweep Straw"}]},
        headers=tech_headers,
    )
    assert resp.status_code == 403


def test_add_suggested_supplies_empty_list_rejected(client, auth_headers, wo):
    resp = client.post(
        f"/work-orders/{wo['id']}/suggested-supplies",
        json={"items": []},
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ---- request_types.show_inventory_lookup ----

def test_create_request_type_with_inventory_lookup_flag(client, auth_headers):
    resp = client.post(
        "/admin/request-types",
        json={"name": "NJ Security", "show_inventory_lookup": True},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["show_inventory_lookup"] is True


def test_update_request_type_toggle_inventory_lookup(client, auth_headers):
    created = client.post("/admin/request-types", json={"name": "NJ Retail"}, headers=auth_headers).json()
    assert created["show_inventory_lookup"] is False

    updated = client.patch(
        f"/admin/request-types/{created['id']}",
        json={"show_inventory_lookup": True},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["show_inventory_lookup"] is True


def test_authenticated_request_types_endpoint_available_to_loc(client, loc_user):
    """Unlike /admin/request-types (admin-only), the plain /request-types
    endpoint (reference.py) is readable by any authenticated role — the
    triage drawer (loc) needs it to know which types show the inventory
    widget."""
    headers = _login(client, loc_user.email)
    resp = client.get("/request-types", headers=headers)
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert "NJ Items/Parts" in names


# ---- Full user journey (E2E-style): import → triage attach → tech view →
# task-worker view → remove. Each piece already has its own focused test
# above; this one exercises them together in the order a real triager,
# dispatcher, and field worker would actually hit them, against the same
# GET /work-orders/{id} response every one of those screens reads from —
# catching any gap where one persona's view stops reflecting reality
# (e.g. a future change that scopes suggested_supplies to a role and
# silently breaks technician.html/worker.html, which only surfaced as a
# real bug once during this feature's build).

def test_full_supply_lookup_journey_admin_to_field_worker(client, auth_headers, asset, team, db):
    # 1. Admin refreshes the warehouse catalog (as they would before the
    #    Jamboree, or any time the export changes).
    files = {"file": ("inventory.csv", io.BytesIO(BROOM_CSV), "text/csv")}
    apply_resp = client.post("/admin/inventory/apply", files=files, headers=auth_headers)
    assert apply_resp.status_code == 200, apply_resp.text

    # 2. Confirm the request type triagers rely on is flagged for the
    #    widget — this is what tells the triage drawer to show the
    #    inventory search box at all.
    types = {t["name"]: t for t in client.get("/request-types", headers=auth_headers).json()}
    assert types["NJ Items/Parts"]["show_inventory_lookup"] is True

    # 3. LOC creates and triages a supply-request WO.
    wo = client.post(
        "/work-orders",
        json={
            "requester_name": "Scout Leader", "requester_email": "leader@example.com",
            "asset_id": asset.id, "work_type": "NJ Items/Parts",
            "description": "Need cleaning supplies for the shower house", "priority": "Next Day",
        },
        headers=auth_headers,
    ).json()

    # 4. Triager searches the catalog the way the widget would (default:
    #    in-stock only), finds the broom, and attaches it plus a second
    #    item with a specific quantity.
    search = client.get("/inventory/search?q=broom", headers=auth_headers).json()
    assert [r["sku"] for r in search] == ["999000048"]  # Whisk excluded — 0 on hand

    attach = client.post(
        f"/work-orders/{wo['id']}/suggested-supplies",
        json={"items": [
            {"sku": "999000048", "description": "Broom Sweep Straw", "qty_requested": 2},
            {"sku": "1002041", "description": "Aspirin Ec Tab 325mg", "qty_requested": 1},
        ]},
        headers=auth_headers,
    )
    assert attach.status_code == 201, attach.text

    # 5. LOC assigns the WO to a team and tasks it to an individual field
    #    worker on that team, so both the Dispatcher and worker personas
    #    below have a legitimate reason to view it.
    client.post(f"/work-orders/{wo['id']}/assign", json={"team_id": team.id}, headers=auth_headers)

    tech = models.User(
        name="Dispatcher Dana", email="dana@test.local", role="tech", team_id=team.id,
        password_hash=crud.hash_password("test-password"),
    )
    db.add(tech)
    db.commit()
    db.refresh(tech)
    worker, pin = crud.create_task_worker(db, team.id, schemas.TaskWorkerCreate(name="Field Worker Wes"))

    tech_headers = _login(client, tech.email)
    worker_headers = client.post(
        "/public/worker-login", json={"worker_id": worker.id, "pin": pin}
    ).json()
    worker_auth = {"Authorization": f"Bearer {worker_headers['access_token']}"}

    client.post(
        f"/work-orders/{wo['id']}/assign",
        json={"team_id": team.id, "person_id": worker.id},
        headers=tech_headers,
    )

    # 6. Both the Dispatcher (technician.html) and the Field Worker
    #    (worker.html) read the exact same endpoint the LOC drawer does —
    #    confirm the supply list each of them would render is present and
    #    correct, not just for the LOC/admin caller who attached it.
    for caller_headers in (tech_headers, worker_auth):
        detail = client.get(f"/work-orders/{wo['id']}", headers=caller_headers).json()
        supplies = {s["sku"]: s["qty_requested"] for s in detail["suggested_supplies"]}
        assert supplies == {"999000048": 2, "1002041": 1}
        supply_note = next(n for n in detail["notes"] if n["note_type"] == "supply_request")
        assert "999000048 — Broom Sweep Straw x 2" in supply_note["note_text"]
        assert "1002041 — Aspirin Ec Tab 325mg x 1" in supply_note["note_text"]

    # 7. LOC corrects a mis-added item — confirm the removal is reflected
    #    back through to the field worker's view too, not just LOC's own.
    to_remove = next(
        s["id"] for s in
        client.get(f"/work-orders/{wo['id']}", headers=auth_headers).json()["suggested_supplies"]
        if s["sku"] == "1002041"
    )
    del_resp = client.delete(f"/work-orders/{wo['id']}/suggested-supplies/{to_remove}", headers=auth_headers)
    assert del_resp.status_code == 204

    worker_view = client.get(f"/work-orders/{wo['id']}", headers=worker_auth).json()
    assert [s["sku"] for s in worker_view["suggested_supplies"]] == ["999000048"]
    # The note is a point-in-time record of what was suggested, not live
    # state — it stays even after the item is removed from the structured
    # list (see crud.remove_suggested_supply's docstring).
    assert any(n["note_type"] == "supply_request" for n in worker_view["notes"])

