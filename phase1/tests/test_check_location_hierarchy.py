"""
Tests for check_location_hierarchy.py -- the deploy-time guard against
the "migration added parent_id but nobody re-ran seed.py" flat-hierarchy
failure mode (see that module's docstring for the full story).
"""
import json

import check_location_hierarchy as check_mod
from app import models


def _write_hierarchy_file(tmp_path, roots):
    path = tmp_path / "location_hierarchy.json"
    path.write_text(json.dumps(roots))
    return str(path)


def test_empty_assets_table_is_ok(db, tmp_path):
    path = _write_hierarchy_file(tmp_path, [{"name": "NJ Base Camps Ops", "branch_label": "Base Camps"}])
    ok, message = check_mod.check(db, hierarchy_path=path)
    assert ok
    assert "not seeded yet" in message


def test_properly_nested_hierarchy_is_ok(db, tmp_path):
    branch = models.Asset(name="NJ Base Camps Ops", location_group="Base Camps")
    db.add(branch)
    db.flush()
    camp = models.Asset(name="NJ Base Camp A", location_group="Base Camps", parent_id=branch.id)
    other_branch = models.Asset(name="NJ Medical", location_group="Medical")
    db.add_all([camp, other_branch])
    db.commit()

    path = _write_hierarchy_file(
        tmp_path,
        [
            {"name": "NJ Base Camps Ops", "branch_label": "Base Camps"},
            {"name": "NJ Medical", "branch_label": "Medical"},
        ],
    )
    ok, message = check_mod.check(db, hierarchy_path=path)
    assert ok
    assert "OK" in message


def test_flat_hierarchy_after_unbackfilled_migration_fails(db, tmp_path):
    # Simulate the real bug: 30 asset rows, none with parent_id set,
    # even though the source tree only defines 2 top-level branches.
    for i in range(30):
        db.add(models.Asset(name=f"Asset {i}", location_group="Base Camps"))
    db.commit()

    path = _write_hierarchy_file(
        tmp_path,
        [
            {"name": "NJ Base Camps Ops", "branch_label": "Base Camps"},
            {"name": "NJ Medical", "branch_label": "Medical"},
        ],
    )
    ok, message = check_mod.check(db, hierarchy_path=path)
    assert not ok
    assert "flat" in message
    assert "seed.py" in message


def test_missing_hierarchy_file_does_not_false_positive(db, tmp_path):
    for i in range(30):
        db.add(models.Asset(name=f"Asset {i}", location_group="Base Camps"))
    db.commit()

    ok, message = check_mod.check(db, hierarchy_path=str(tmp_path / "does_not_exist.json"))
    assert ok
    assert "not found" in message


def test_main_exits_nonzero_on_flat_hierarchy(db, tmp_path, monkeypatch):
    for i in range(30):
        db.add(models.Asset(name=f"Asset {i}", location_group="Base Camps"))
    db.commit()

    path = _write_hierarchy_file(tmp_path, [{"name": "NJ Base Camps Ops", "branch_label": "Base Camps"}])
    monkeypatch.setattr(check_mod, "SessionLocal", lambda: db)
    monkeypatch.setattr(check_mod, "LOCATION_HIERARCHY_PATH", path)

    # main() closes the session it opens -- give it a no-op close so the
    # shared test `db` fixture session survives for pytest's own teardown.
    monkeypatch.setattr(db, "close", lambda: None)

    exit_code = check_mod.main()
    assert exit_code == 1
