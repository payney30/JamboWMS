"""
Admin user-management endpoints (PRD §10: "someone needs to manage which
teams exist, who's on them" — this is the "who's on them" half; team CRUD
itself is still config-file/seed-script, see seed.py STARTER_TEAMS).

Both 'loc' and 'admin' can reach these endpoints — day-to-day fulfillment
account creation is an LOC operational task at a live event, not something
that should require pulling in the one admin. But privilege escalation is
gated: only an admin can create, promote, or modify an 'admin' or 'loc'
account. A non-admin LOC user can freely manage 'tech' and 'leadership'
accounts.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from .. import crud, schemas, models
from ..database import get_db
from ..auth import require_roles, get_current_user

router = APIRouter(prefix="/users", tags=["users"])

admin_or_loc = require_roles("admin", "loc")

# Roles a non-admin ('loc') caller is allowed to create/see/modify without
# an admin's involvement.
_LOC_MANAGEABLE_ROLES = {"tech", "leadership"}


def _check_can_manage_role(actor: models.User, role: str):
    if actor.role != "admin" and role not in _LOC_MANAGEABLE_ROLES:
        raise HTTPException(403, "only an admin can create or modify admin/LOC accounts")


@router.get("/me", response_model=schemas.UserOut)
def get_me(user: models.User = Depends(get_current_user)):
    """Any authenticated user (loc/tech/leadership/admin) can read their
    own identity — used by the frontend (enhancement backlog Phase 1, PRD
    §14#1) to tell whether *it* holds a WO's edit lock vs. someone else
    does. Declared before the /{user_id} routes below so it isn't
    swallowed by that path pattern."""
    return user


@router.get("", response_model=list[schemas.UserAdminOut])
def list_users(
    role: Optional[str] = None,
    team_id: Optional[int] = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    user: models.User = Depends(admin_or_loc),
):
    return crud.list_users(db, role=role, team_id=team_id, include_inactive=include_inactive)


@router.post("", response_model=schemas.UserCreateResponse, status_code=201)
def create_user(
    payload: schemas.UserCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(admin_or_loc),
):
    _check_can_manage_role(user, payload.role)
    created, password = crud.create_user(db, payload)
    return {"user": created, "temporary_password": password}


@router.patch("/{user_id}", response_model=schemas.UserAdminOut)
def update_user(
    user_id: int,
    payload: schemas.UserUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(admin_or_loc),
):
    target = db.get(models.User, user_id)
    if not target:
        raise HTTPException(404, "user not found")

    # Gate on both the account's CURRENT role and any role it's being
    # moved TO — a non-admin can't touch an existing admin account, and
    # can't promote a tech into one either.
    _check_can_manage_role(user, target.role)
    if payload.role is not None:
        _check_can_manage_role(user, payload.role)

    if target.id == user.id and payload.is_active is False:
        raise HTTPException(400, "you can't deactivate your own account")

    return crud.update_user(db, target, payload)


@router.post("/{user_id}/reset-password", response_model=schemas.PasswordResetResponse)
def reset_password(
    user_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(admin_or_loc),
):
    target = db.get(models.User, user_id)
    if not target:
        raise HTTPException(404, "user not found")
    _check_can_manage_role(user, target.role)
    password = crud.reset_password(db, target)
    return {"temporary_password": password}
