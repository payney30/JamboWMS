from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Literal, Optional

from .. import crud, schemas, models
from ..database import get_db
from ..auth import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

Scope = Literal["main", "program", "basecamp"]


def _filter_params(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    work_type: Optional[str] = None,
    team_id: Optional[int] = None,
    location_group: Optional[str] = None,
    search: Optional[str] = None,
) -> dict:
    return {
        "status": status, "priority": priority, "work_type": work_type,
        "team_id": team_id, "location_group": location_group, "search": search,
    }


@router.get("/kpis", response_model=schemas.KPIOut)
def kpis(scope: Scope = "main", filters: dict = Depends(_filter_params),
         db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_kpis(db, scope=scope, **filters)


@router.get("/breakdowns", response_model=schemas.BreakdownOut)
def breakdowns(scope: Scope = "main", filters: dict = Depends(_filter_params),
               db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_breakdowns(db, scope=scope, **filters)


@router.get("/trend")
def trend(scope: Scope = "main", days: int = Query(14, ge=1, le=90),
          filters: dict = Depends(_filter_params),
          db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_daily_trend(db, scope=scope, days=days, **filters)


@router.get("/attention", response_model=list[schemas.WorkOrderListItem])
def attention(scope: Scope = "main", limit: int = Query(15, ge=1, le=50),
              filters: dict = Depends(_filter_params),
              db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_needing_attention(db, scope=scope, limit=limit, **filters)
