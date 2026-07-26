from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from .. import crud, schemas, models
from ..database import get_db
from ..auth import require_roles, get_current_user

router = APIRouter(prefix="/work-orders", tags=["work-orders"])

loc_or_admin = require_roles("loc", "admin")


def _get_wo_or_404(db: Session, wo_id: int) -> models.WorkOrder:
    wo = db.get(models.WorkOrder, wo_id)
    if not wo:
        raise HTTPException(404, "work order not found")
    return wo


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
    return _get_wo_or_404(db, wo_id)


@router.patch("/{wo_id}", response_model=schemas.WorkOrderDetail)
def update_work_order(wo_id: int, payload: schemas.WorkOrderUpdate,
                       db: Session = Depends(get_db),
                       user: models.User = Depends(loc_or_admin)):
    wo = _get_wo_or_404(db, wo_id)
    return crud.update_work_order_fields(db, wo, payload, changed_by=user.id)


@router.post("/{wo_id}/assign", response_model=schemas.WorkOrderDetail)
def assign_work_order(wo_id: int, payload: schemas.AssignRequest,
                       db: Session = Depends(get_db),
                       user: models.User = Depends(loc_or_admin)):
    wo = _get_wo_or_404(db, wo_id)
    return crud.assign_work_order(db, wo, payload, changed_by=user.id)


@router.post("/{wo_id}/status", response_model=schemas.WorkOrderDetail)
def change_status(wo_id: int, payload: schemas.StatusChangeRequest,
                   db: Session = Depends(get_db),
                   user: models.User = Depends(get_current_user)):
    wo = _get_wo_or_404(db, wo_id)
    return crud.change_status(db, wo, payload, changed_by=user.id)


@router.post("/{wo_id}/notes", response_model=schemas.NoteOut, status_code=201)
def add_note(wo_id: int, payload: schemas.NoteCreate,
             db: Session = Depends(get_db),
             user: models.User = Depends(get_current_user)):
    wo = _get_wo_or_404(db, wo_id)
    return crud.add_note(db, wo, payload, author_id=user.id)
