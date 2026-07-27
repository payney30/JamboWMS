"""
Deploy-time safety check: verify the location hierarchy is actually
nested, not flat.

Why this exists: migration 5160d845f250 (add_location_hierarchy_to_assets)
added assets.parent_id as a new nullable column. A migration only adds
columns -- it never populates them. The only thing that ever sets
parent_id on existing rows is seed.py's second pass (link_parents). If
`alembic upgrade head` ever runs against a database that already has
asset rows, without `python seed.py` also being re-run afterward, every
row's parent_id stays NULL and crud.build_location_tree() -- which treats
parent_id IS NULL as "this is a root node" -- returns every asset as a
top-level root. Both the public requester form and the LOC triage
location pickers then render a flat list instead of a tree. This
happened once in production; this script exists so it fails the deploy
instead of silently shipping broken pickers.

This is now wired into the deploy start command (see Procfile /
render.yaml) to run after `python seed.py` and before the app starts
serving traffic:

    alembic upgrade head && python seed.py && python check_location_hierarchy.py && uvicorn ...

Can also be run by hand at any time:

    python check_location_hierarchy.py

Exits 0 if the hierarchy looks healthy, 1 (with a clear explanation) if
it looks flat.
"""
import json
import os
import sys

from app import models
from app.database import SessionLocal

LOCATION_HIERARCHY_PATH = os.environ.get(
    "LOCATION_HIERARCHY_PATH", "data/location_hierarchy.json"
)

# An admin could legitimately add a handful of new top-level locations
# over time, so some slack above the source tree's own root count is
# expected. But if the DB has dramatically more roots than the source
# tree defines, that's not organic growth -- it means parent_id was
# never backfilled and (almost) every asset is sitting at root.
SLACK_MULTIPLIER = 3
MIN_SUSPICIOUS_ROOTS = 15


def check(db, hierarchy_path: str = LOCATION_HIERARCHY_PATH) -> tuple[bool, str]:
    """Returns (ok, message)."""
    total = db.query(models.Asset).count()
    if total == 0:
        return True, "assets table is empty (not seeded yet) -- nothing to check"

    roots = db.query(models.Asset).filter(models.Asset.parent_id.is_(None)).count()

    if not os.path.exists(hierarchy_path):
        return True, (
            f"source hierarchy file not found at {hierarchy_path!r} -- "
            "skipping comparison rather than risking a false positive"
        )

    with open(hierarchy_path) as f:
        tree = json.load(f)
    expected_roots = len(tree)

    threshold = max(expected_roots * SLACK_MULTIPLIER, MIN_SUSPICIOUS_ROOTS)
    if roots > threshold:
        return False, (
            f"location hierarchy looks flat: {roots} of {total} assets have "
            f"no parent_id set, but {hierarchy_path} only defines "
            f"{expected_roots} top-level branches. This is the known failure "
            f"mode when a migration adds a backfill-requiring column (e.g. "
            f"parent_id) but `python seed.py` isn't re-run afterward. "
            f"Fix: run `python seed.py` against this database -- it's "
            f"idempotent, safe to re-run -- then retry."
        )

    return True, f"OK: {roots} root(s) out of {total} assets (source defines {expected_roots} top-level branches)"


def main() -> int:
    db = SessionLocal()
    try:
        # Pass the module global explicitly (rather than relying on check()'s
        # default parameter, which is bound once at import time) so tests
        # can monkeypatch LOCATION_HIERARCHY_PATH and have it actually apply.
        ok, message = check(db, LOCATION_HIERARCHY_PATH)
    finally:
        db.close()

    print(("OK: " if ok else "FAIL: ") + message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
