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
from app import models, crud
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

# Matches the old hardcoded schemas.WORK_TYPES tuple — now seeded as
# admin-editable request_types rows instead (PRD 4.5c). Blank/'Other' is
# deliberately not a row here; it stays a valid sentinel outside this
# table, same as before.
STARTER_REQUEST_TYPES = [
    "NJ IT",
    "NJ Items/Parts",
    "NJ Maintenance",
    "NJ Transportation",
]


def seed_assets(db):
    with open(LOCATION_HIERARCHY_PATH) as f:
        tree = json.load(f)
    with open(NAME_TO_CAMP_LETTER_PATH) as f:
        name_to_camp_letter = json.load(f)

    # PRD 4.5b: reporting_groups becomes the admin-editable catalog that
    # assets.location_group used to be baked in from directly. Seed one
    # row per distinct branch_label found in the tree, in first-appearance
    # order, so a fresh install starts with exactly the groups the old
    # hardcoded pipeline had (Program Areas, Base Camp Ops, etc.) — same
    # idempotent upsert-by-name pattern as everything else in this file.
    existing_groups = {g.name: g for g in db.query(models.ReportingGroup).all()}
    group_added = 0
    sort_i = 0
    for node in _walk(tree):
        label = node["branch_label"]
        if label not in existing_groups:
            rg = models.ReportingGroup(name=label, sort_order=sort_i)
            db.add(rg)
            existing_groups[label] = rg
            group_added += 1
            sort_i += 1
    db.flush()
    print(f"reporting groups: added {group_added}")

    existing = {a.name: a for a in db.query(models.Asset).all()}
    added = 0
    updated = 0

    # Pass 1: create/update every node by name (no parent_id yet — a
    # child can be visited before its parent gets its id in some tree
    # shapes, so parent-linking is a separate pass below).
    def upsert(node, sort_order, parent_branch_label):
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
        # Explicit reporting_group_id override only at "boundary" nodes —
        # a root, or a node whose branch_label differs from its parent's
        # — so inheritance (crud.recompute_effective_groups) reproduces
        # every other node's value automatically instead of every single
        # row carrying its own redundant override. This is exactly the
        # backfill PRD 4.5b describes: e.g. Base Camp A/B get an explicit
        # override to "Program Areas" even though their parent branch is
        # "Base Camp Ops," and everything under A/B inherits it from there.
        is_boundary = parent_branch_label is None or node["branch_label"] != parent_branch_label
        if is_boundary:
            row.reporting_group_id = existing_groups[node["branch_label"]].id
        for i, child in enumerate(node.get("children", [])):
            upsert(child, i, node["branch_label"])

    for i, root in enumerate(tree):
        upsert(root, i, None)
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
    crud.recompute_effective_groups(db)
    total = sum(1 for _ in _walk(tree))
    print(f"assets: added {added}, updated {updated}, {total} total in source")


def seed_request_types(db):
    """PRD 4.5c — replaces the old hardcoded WORK_TYPES tuple; new/renamed
    types are managed via the admin screen from here on, this just gives
    a fresh install the same starting set the tuple used to provide."""
    existing = {t.name: t for t in db.query(models.RequestType).all()}
    added = 0
    for i, name in enumerate(STARTER_REQUEST_TYPES):
        if name in existing:
            continue
        db.add(models.RequestType(
            name=name, sort_order=i,
            # PRD §4.5e: the inventory search widget defaults on for
            # Items/Parts (the type it's actually useful for) so go-live
            # doesn't require a manual admin click first — every other
            # type stays off, admin-toggleable from here.
            show_inventory_lookup=(name == "NJ Items/Parts"),
        ))
        added += 1
    db.commit()
    print(f"request types: added {added}")


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
        seed_request_types(db)
        seed_admin(db)
    finally:
        db.close()
