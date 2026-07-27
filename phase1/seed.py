"""
Seed script for Phase 1.

Run once against a fresh database:
    python seed.py

Loads:
  - assets: from location_hierarchy.json (the full nested tree, generated
            by parse_asset_hierarchy.py from Asset_Hierarchy_Analysis.md —
            point the path below at your copy). Every node in the tree
            (branch, camp, subcamp, shower house, leaf) becomes one Asset
            row, linked via parent_id, so the hierarchical location picker
            (PRD 4.2a) has a real tree to render instead of a flat list.
  - teams:  a starter list matching the team names already seen in the
            current Fiix export data; edit/extend as needed
  - one initial admin user (email/password printed at the end — change
    the password immediately after first login)

NOTE: this script assumes the schema already exists. Run migrations first:
    alembic upgrade head
    python seed.py
"""
import json
import os
import secrets
import sys

from sqlalchemy import inspect

from app.database import SessionLocal, engine
from app import models
from app.auth import hash_password

LOCATION_HIERARCHY_PATH = os.environ.get("LOCATION_HIERARCHY_PATH", "data/location_hierarchy.json")
NAME_TO_CAMP_LETTER_PATH = os.environ.get("NAME_TO_CAMP_LETTER_PATH", "data/name_to_camp_letter.json")

STARTER_TEAMS = [
    "2026 Jamboree LOC (Work Order Management)",
    "2026 Jamboree Maintenance (Repairs and General Needs)",
    "2026 Jamboree IT",
    "2026 Jamboree ALC (Warehouse for Items)",
    "2026 Jamboree Allied (Contractor)",
    "2026 Jamboree Freeman (Contractor)",
]


def seed_assets(db):
    with open(LOCATION_HIERARCHY_PATH) as f:
        tree = json.load(f)
    with open(NAME_TO_CAMP_LETTER_PATH) as f:
        name_to_camp_letter = json.load(f)

    existing = {a.name: a for a in db.query(models.Asset).all()}
    added = 0
    updated = 0

    # Pass 1: create/update every node by name (no parent_id yet — a
    # child can be visited before its parent gets its id in some tree
    # shapes, so parent-linking is a separate pass below).
    def upsert(node, sort_order):
        nonlocal added, updated
        name = node["name"]
        row = existing.get(name)
        if row is None:
            row = models.Asset(name=name, location_group=node["branch_label"])
            db.add(row)
            existing[name] = row
            added += 1
        else:
            updated += 1
        row.location_group = node["branch_label"]
        row.code = node.get("code") or None
        row.camp_letter = name_to_camp_letter.get(name)
        row.sort_order = sort_order
        row.is_active = True
        for i, child in enumerate(node.get("children", [])):
            upsert(child, i)

    for i, root in enumerate(tree):
        upsert(root, i)
    db.flush()  # every row now has an id, needed for pass 2

    # Pass 2: wire up parent_id now that every node has an id.
    def link_parents(node, parent_row):
        row = existing[node["name"]]
        row.parent_id = parent_row.id if parent_row else None
        for child in node.get("children", []):
            link_parents(child, row)

    for root in tree:
        link_parents(root, None)

    db.commit()
    total = sum(1 for _ in _walk(tree))
    print(f"assets: added {added}, updated {updated}, {total} total in source")


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.get("children", []))


def seed_teams(db):
    existing = {t.name for t in db.query(models.Team.name).all()}
    added = 0
    for name in STARTER_TEAMS:
        if name in existing:
            continue
        db.add(models.Team(name=name))
        added += 1
    db.commit()
    print(f"teams: added {added}")


def seed_admin(db):
    if db.query(models.User).filter(models.User.role == "admin").first():
        print("admin user already exists, skipping")
        return
    password = secrets.token_urlsafe(12)
    admin = models.User(
        name="LOC Admin",
        email="admin@njloc.local",
        password_hash=hash_password(password),
        role="admin",
    )
    db.add(admin)
    db.commit()
    print(f"admin user created: admin@njloc.local / {password}  (change this immediately)")


if __name__ == "__main__":
    if not inspect(engine).has_table("work_orders"):
        sys.exit(
            "Schema not found. Run `alembic upgrade head` before seeding "
            "(see README)."
        )
    db = SessionLocal()
    try:
        seed_assets(db)
        seed_teams(db)
        seed_admin(db)
    finally:
        db.close()
