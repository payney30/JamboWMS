"""
Admin configuration endpoints (PRD 4.5): location hierarchy CRUD/
reparenting (4.5a), reporting groups (4.5b), request types (4.5c), and
team CRUD (4.5d). All admin-only — this is structural configuration, not
day-to-day triage, matching the System Admin role's scope in PRD §3
("manage teams, users, pick lists, asset hierarchy, exports").

Common pattern across all four: soft-delete only (is_active flip, never
a hard DB delete — see each model's docstring in models.py), and
deactivating something with live dependents (a location with active
children, a team with open WOs/active users) returns 409 with what's
still attached rather than silently blocking or cascading, unless the
caller explicitly opts in (cascade_deactivate / confirm_deactivate).
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from zoneinfo import available_timezones

from .. import crud, schemas, models
from ..database import get_db
from ..auth import require_roles

router = APIRouter(prefix="/admin", tags=["admin"])
admin_only = require_roles("admin")


# ---- 4.5a: Location hierarchy ----

@router.get("/assets", response_model=list[schemas.AssetAdminOut])
def list_assets(include_inactive: bool = True, db: Session = Depends(get_db),
                 user: models.User = Depends(admin_only)):
    return crud.list_assets_admin(db, include_inactive=include_inactive)


@router.post("/assets", response_model=schemas.AssetAdminOut, status_code=201)
def create_asset(payload: schemas.AssetCreate, db: Session = Depends(get_db),
                  user: models.User = Depends(admin_only)):
    asset = crud.create_asset(db, payload, changed_by=user.id)
    return _find_admin_row(db, asset.id)


@router.patch("/assets/{asset_id}", response_model=schemas.AssetAdminOut)
def update_asset(asset_id: int, payload: schemas.AssetUpdate, db: Session = Depends(get_db),
                  user: models.User = Depends(admin_only)):
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise HTTPException(404, "location not found")
    crud.update_asset(db, asset, payload, changed_by=user.id)
    return _find_admin_row(db, asset_id)


@router.get("/assets/{asset_id}/history", response_model=list[schemas.AssetChangeLogOut])
def asset_history(asset_id: int, db: Session = Depends(get_db),
                   user: models.User = Depends(admin_only)):
    if not db.get(models.Asset, asset_id):
        raise HTTPException(404, "location not found")
    return crud.get_asset_change_log(db, asset_id)


def _find_admin_row(db: Session, asset_id: int) -> dict:
    """AssetAdminOut needs depth/parent_name, which crud.list_assets_admin
    computes for the whole tree — cheap at this scale, and avoids a
    second code path that could drift from the list view's numbers."""
    rows = crud.list_assets_admin(db, include_inactive=True)
    for row in rows:
        if row["id"] == asset_id:
            return row
    raise HTTPException(404, "location not found")


# ---- 4.5b: Reporting groups ----

@router.get("/reporting-groups", response_model=list[schemas.ReportingGroupOut])
def list_reporting_groups(include_inactive: bool = True, db: Session = Depends(get_db),
                           user: models.User = Depends(admin_only)):
    return crud.list_reporting_groups(db, include_inactive=include_inactive)


@router.post("/reporting-groups", response_model=schemas.ReportingGroupOut, status_code=201)
def create_reporting_group(payload: schemas.ReportingGroupCreate, db: Session = Depends(get_db),
                            user: models.User = Depends(admin_only)):
    return crud.create_reporting_group(db, payload)


@router.patch("/reporting-groups/{group_id}", response_model=schemas.ReportingGroupOut)
def update_reporting_group(group_id: int, payload: schemas.ReportingGroupUpdate,
                            db: Session = Depends(get_db), user: models.User = Depends(admin_only)):
    rg = db.get(models.ReportingGroup, group_id)
    if not rg:
        raise HTTPException(404, "reporting group not found")
    return crud.update_reporting_group(db, rg, payload)


# ---- 4.5c: Request types ----

@router.get("/request-types", response_model=list[schemas.RequestTypeOut])
def list_request_types(include_inactive: bool = True, db: Session = Depends(get_db),
                        user: models.User = Depends(admin_only)):
    return crud.list_request_types(db, include_inactive=include_inactive)


@router.post("/request-types", response_model=schemas.RequestTypeOut, status_code=201)
def create_request_type(payload: schemas.RequestTypeCreate, db: Session = Depends(get_db),
                         user: models.User = Depends(admin_only)):
    return crud.create_request_type(db, payload)


@router.patch("/request-types/{type_id}", response_model=schemas.RequestTypeOut)
def update_request_type(type_id: int, payload: schemas.RequestTypeUpdate,
                         db: Session = Depends(get_db), user: models.User = Depends(admin_only)):
    rt = db.get(models.RequestType, type_id)
    if not rt:
        raise HTTPException(404, "request type not found")
    return crud.update_request_type(db, rt, payload)


# ---- 4.5d: Teams ----

@router.get("/teams", response_model=list[schemas.TeamAdminOut])
def list_teams(include_inactive: bool = True, db: Session = Depends(get_db),
               user: models.User = Depends(admin_only)):
    return crud.list_teams_admin(db, include_inactive=include_inactive)


@router.post("/teams", response_model=schemas.TeamAdminOut, status_code=201)
def create_team(payload: schemas.TeamCreate, db: Session = Depends(get_db),
                 user: models.User = Depends(admin_only)):
    return crud.create_team(db, payload)


@router.patch("/teams/{team_id}", response_model=schemas.TeamAdminOut)
def update_team(team_id: int, payload: schemas.TeamUpdate, db: Session = Depends(get_db),
                 user: models.User = Depends(admin_only)):
    team = db.get(models.Team, team_id)
    if not team:
        raise HTTPException(404, "team not found")
    return crud.update_team(db, team, payload)


# ---- 4.5e: Inventory / supply lookup ----

@router.post("/inventory/preview", response_model=schemas.InventoryImportPreview)
async def preview_inventory_import(file: UploadFile = File(...), db: Session = Depends(get_db),
                                     user: models.User = Depends(admin_only)):
    """Parses the uploaded warehouse CSV and returns a diff against the
    current catalog (added/changed/removed) WITHOUT applying anything —
    the admin reviews this before calling /inventory/apply with the same
    file. Re-parses/re-diffs on each call rather than caching server-side
    state, so there's no session/token to manage between the two steps."""
    rows = crud.parse_inventory_csv(await file.read())
    if not rows:
        raise HTTPException(400, "no valid rows found in the uploaded file")
    return crud.diff_inventory_import(db, rows)


@router.post("/inventory/apply", response_model=schemas.InventoryImportResult)
async def apply_inventory_import(file: UploadFile = File(...), db: Session = Depends(get_db),
                                   user: models.User = Depends(admin_only)):
    """Applies the same file previously reviewed via /inventory/preview.
    Inserts new SKUs, updates changed fields on existing ones, and soft-
    deletes any active SKU no longer present in the file — never a hard
    delete, since a work order may already reference it."""
    rows = crud.parse_inventory_csv(await file.read())
    if not rows:
        raise HTTPException(400, "no valid rows found in the uploaded file")
    return crud.apply_inventory_import(db, rows)


# ---- Global settings (PRD §15#1) ----

@router.get("/settings", response_model=schemas.SettingsOut)
def get_settings(db: Session = Depends(get_db), user: models.User = Depends(admin_only)):
    return crud.get_all_settings(db)


@router.put("/settings", response_model=schemas.SettingsOut)
def update_settings(payload: schemas.SettingsUpdate, db: Session = Depends(get_db),
                     user: models.User = Depends(admin_only)):
    if payload.timezone not in available_timezones():
        raise HTTPException(400, f"'{payload.timezone}' is not a recognized IANA time zone")
    crud.set_setting(db, "timezone", payload.timezone, updated_by=user.id)
    return crud.get_all_settings(db)
