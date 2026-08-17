from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import schemas, models, rate_limit, crud
from ..database import get_db
from ..auth import get_current_user, verify_password, create_access_token
from fastapi import HTTPException

router = APIRouter(tags=["reference"])
auth_router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/teams", response_model=list[schemas.TeamOut])
def list_teams(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return db.query(models.Team).filter(models.Team.is_active == True).order_by(models.Team.name).all()  # noqa: E712


@router.get("/assets", response_model=list[schemas.AssetOut])
def list_assets(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return db.query(models.Asset).order_by(models.Asset.name).all()


@router.get("/locations/tree", response_model=list[schemas.LocationNode])
def get_location_tree(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Nested asset hierarchy for the LOC triage location picker (PRD
    4.2a). Active nodes only — same data an admin hierarchy editor (PRD
    4.5) would eventually manage, but this endpoint is what pickers read."""
    return crud.build_location_tree(db, include_inactive=False)


@router.get("/reporting-groups", response_model=list[schemas.ReportingGroupOut])
def list_reporting_groups(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Enhancement backlog Phase 13 (PRD §14#37): read-only reporting-
    group list for the LOC triage inbox filter — any authenticated role,
    unlike the full CRUD admin endpoint at /admin/reporting-groups
    (admin-only, since that one actually manages the catalog). Active
    groups only, same convention as /locations/tree above."""
    return crud.list_reporting_groups(db, include_inactive=False)


@router.get("/request-types", response_model=list[schemas.RequestTypeOut])
def list_request_types(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """PRD §4.5e: read-only request-type list (including
    show_inventory_lookup) for any authenticated role — the LOC triage
    drawer needs this to know which request types should show the
    inventory search widget, unlike the full CRUD admin endpoint at
    /admin/request-types (admin-only). Same pattern as
    list_reporting_groups above."""
    return crud.list_request_types(db, include_inactive=False)


@auth_router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    """Lets a page restore role/team from a stored token on reload without
    re-parsing the login response — used by the technician view, which
    needs to know the caller's team before it can request the right
    scoped queue."""
    return user


@auth_router.post("/login")
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"

    retry_after = rate_limit.check_locked(form.username, client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="too many failed login attempts; try again later",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    user = db.query(models.User).filter(models.User.email == form.username).first()
    if not user or not user.is_active or not verify_password(form.password, user.password_hash):
        rate_limit.record_failure(form.username, client_ip)
        raise HTTPException(401, "incorrect email or password")

    rate_limit.record_success(form.username)
    token = create_access_token(user.id)
    return {"access_token": token, "token_type": "bearer", "role": user.role, "name": user.name}
