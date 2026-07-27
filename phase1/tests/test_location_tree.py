"""
Tests for the hierarchical location picker's backing endpoints
(PRD 4.2a): GET /locations/tree (authenticated) and
GET /public/locations/tree (public), plus crud.build_location_tree
directly.
"""
from app import models


def _make_hierarchy(db):
    """branch -> camp -> shower house, plus a second independent branch,
    matching the real shape (multiple levels, multiple roots)."""
    branch = models.Asset(name="NJ Base Camps Ops", location_group="Base Camps", sort_order=0)
    db.add(branch)
    db.flush()
    camp = models.Asset(name="NJ Base Camp A", location_group="Base Camps",
                         parent_id=branch.id, sort_order=0, code="BC-A")
    db.add(camp)
    db.flush()
    shower = models.Asset(name="Shower House NJ A1-1 E", location_group="Base Camps",
                           parent_id=camp.id, sort_order=0, code="E")
    inactive_shower = models.Asset(name="Shower House NJ A1-1 J", location_group="Base Camps",
                                    parent_id=camp.id, sort_order=1, code="J", is_active=False)
    other_branch = models.Asset(name="NJ Medical", location_group="Medical", sort_order=1)
    db.add_all([shower, inactive_shower, other_branch])
    db.commit()
    return branch, camp, shower, inactive_shower, other_branch


def test_build_location_tree_nests_correctly(db):
    branch, camp, shower, inactive_shower, other_branch = _make_hierarchy(db)
    from app import crud
    tree = crud.build_location_tree(db)

    assert {n["name"] for n in tree} == {"NJ Base Camps Ops", "NJ Medical"}
    bc = next(n for n in tree if n["name"] == "NJ Base Camps Ops")
    assert len(bc["children"]) == 1
    camp_node = bc["children"][0]
    assert camp_node["name"] == "NJ Base Camp A"
    assert camp_node["code"] == "BC-A"
    # inactive sibling pruned, active one kept
    assert [c["name"] for c in camp_node["children"]] == ["Shower House NJ A1-1 E"]


def test_build_location_tree_include_inactive(db):
    _make_hierarchy(db)
    from app import crud
    tree = crud.build_location_tree(db, include_inactive=True)
    bc = next(n for n in tree if n["name"] == "NJ Base Camps Ops")
    camp_node = bc["children"][0]
    names = {c["name"] for c in camp_node["children"]}
    assert names == {"Shower House NJ A1-1 E", "Shower House NJ A1-1 J"}


def test_authenticated_locations_tree_endpoint(client, db, auth_headers):
    _make_hierarchy(db)
    resp = client.get("/locations/tree", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    names = {n["name"] for n in data}
    assert names == {"NJ Base Camps Ops", "NJ Medical"}


def test_authenticated_locations_tree_requires_auth(client, db):
    _make_hierarchy(db)
    resp = client.get("/locations/tree")
    assert resp.status_code == 401


def test_public_locations_tree_endpoint_no_auth_required(client, db):
    _make_hierarchy(db)
    resp = client.get("/public/locations/tree")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    names = {n["name"] for n in data}
    assert names == {"NJ Base Camps Ops", "NJ Medical"}


def test_public_locations_tree_excludes_inactive(client, db):
    _make_hierarchy(db)
    resp = client.get("/public/locations/tree")
    data = resp.json()
    bc = next(n for n in data if n["name"] == "NJ Base Camps Ops")
    camp_node = bc["children"][0]
    child_names = [c["name"] for c in camp_node["children"]]
    assert "Shower House NJ A1-1 J" not in child_names
