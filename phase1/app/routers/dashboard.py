from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Literal

from .. import crud, schemas, models
from ..database import get_db
from ..auth import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

Scope = Literal["main", "program", "basecamp"]


@router.get("/kpis", response_model=schemas.KPIOut)
def kpis(scope: Scope = "main", db: Session = Depends(get_db),
         user: models.User = Depends(get_current_user)):
    return crud.get_kpis(db, scope=scope)


@router.get("/breakdowns", response_model=schemas.BreakdownOut)
def breakdowns(scope: Scope = "main", db: Session = Depends(get_db),
               user: models.User = Depends(get_current_user)):
    return crud.get_breakdowns(db, scope=scope)


@router.get("/trend")
def trend(scope: Scope = "main", days: int = Query(14, ge=1, le=90),
          db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_daily_trend(db, scope=scope, days=days)


@router.get("/attention", response_model=list[schemas.WorkOrderListItem])
def attention(scope: Scope = "main", limit: int = Query(15, ge=1, le=50),
              db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_needing_attention(db, scope=scope, limit=limit)
