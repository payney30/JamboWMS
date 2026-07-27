from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from .. import crud, schemas, models
from ..database import get_db
from ..auth import require_roles, get_current_user

router = APIRouter(prefix="/work-orders", tags=["work-orders"])

loc_or_admin = require_roles("loc", "admin")
tech_or_loc_or_admin = require_roles("tech", "loc", "admin")

# Techs work a request-to-close queue, not the LOC's triage states — they
# can't move a WO back to "Requested" or hand-set "Assigned" (that's what
# the /assign endpoint is for). Enforced here rather than left to the
# frontend since this is a real permissions boundary, not just a UI nicety.
TECH_ALLOWED_STATUSES = {
    "Work In Progress", "On Hold", "Closed, Completed", "Closed, Incomplete",
}


def _get_wo_or_404(db: Session, wo_id: int) -> models.WorkOrder:
    wo = db.get(models.WorkOrder, wo_id)
    if not wo:
        raise HTTPException(404, "work order not found")
    return wo


def _enforce_team_scope(wo: models.WorkOrder, user: models.User):
    """A tech can only touch a WO currently assigned to their own team —
    this is the server-side backing for PRD 4.3's 'each team sees only
    their queue.' LOC/admin are unrestricted."""
    if user.role == "tech" and wo.assigned_team_id != user.team_id:
        raise HTTPException(403, "this work order is not assigned to your team")


@router.get("", response_model=list[schemas.WorkOrderListItem])
def list_work_orders(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    team_id: Optional[int] = None,
    work_type: Optional[str] = None,
    location_group: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    # Techs can't widen their view past their own team's queue by editing
    # the query string — the server, not the frontend, owns this boundary.
    if user.role == "tech":
        team_id = user.team_id
    return crud.list_work_orders(
        db, status=status, priority=priority, team_id=team_id,
        work_type=work_type, location_group=location_group, search=search,
        limit=limit, offset=offset,
    )


@router.post("", response_model=schemas.WorkOrderDetail, status_code=201)
def create_work_order(
    payload: schemas.WorkOrderCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(loc_or_admin),
):
    return crud.create_work_order(db, payload)


@router.get("/{wo_id}", response_model=schemas.WorkOrderDetail)
def get_work_order(wo_id: int, db: Session = Depends(get_db),
                    user: models.User = Depends(get_current_user)):
    wo = _get_wo_or_404(db, wo_id)
    _enforce_team_scope(wo, user)
    return wo


@router.patch("/{wo_id}", response_model=schemas.WorkOrderDetail)
def update_work_order(wo_id: int, payload: schemas.WorkOrderUpdate,
                       db: Session = Depends(get_db),
                       user: models.User = Depends(loc_or_admin)):
    wo = _get_wo_or_404(db, wo_id)
    return crud.update_work_order_fields(db, wo, payload, changed_by=user.id)


@router.post("/{wo_id}/assign", response_model=schemas.WorkOrderDetail)
def assign_work_order(wo_id: int, payload: schemas.AssignRequest,
                       db: Session = Depends(get_db),
                       user: models.User = Depends(tech_or_loc_or_admin)):
    wo = _get_wo_or_404(db, wo_id)
    # LOC/admin do the initial triage assignment (wo.assigned_team_id is
    # still null at that point). A tech only ever *reroutes* work their
    # own team already holds — the scope check below naturally excludes
    # unassigned WOs from a tech's reach, so this only opens up the
    # "mis-routed, hand it to the right team" case from PRD 4.3.
    _enforce_team_scope(wo, user)
    return crud.assign_work_order(db, wo, payload, changed_by=user.id)


@router.post("/{wo_id}/status", response_model=schemas.WorkOrderDetail)
def change_status(wo_id: int, payload: schemas.StatusChangeRequest,
                   db: Session = Depends(get_db),
                   user: models.User = Depends(get_current_user)):
    wo = _get_wo_or_404(db, wo_id)
    _enforce_team_scope(wo, user)
    if user.role == "tech" and payload.status not in TECH_ALLOWED_STATUSES:
        raise HTTPException(
            403,
            f"technicians can't set status to '{payload.status}' — "
            f"allowed: {', '.join(sorted(TECH_ALLOWED_STATUSES))}",
        )
    return crud.change_status(db, wo, payload, changed_by=user.id)


@router.post("/{wo_id}/notes", response_model=schemas.NoteOut, status_code=201)
def add_note(wo_id: int, payload: schemas.NoteCreate,
             db: Session = Depends(get_db),
             user: models.User = Depends(get_current_user)):
    wo = _get_wo_or_404(db, wo_id)
    _enforce_team_scope(wo, user)
    if user.role == "tech" and payload.note_type == "instruction":
        raise HTTPException(403, "instructions are LOC-authored; technicians can add work notes")
    return crud.add_note(db, wo, payload, author_id=user.id)
