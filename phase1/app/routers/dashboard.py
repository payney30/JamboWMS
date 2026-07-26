from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import crud, schemas, models
from ..database import get_db
from ..auth import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/kpis", response_model=schemas.KPIOut)
def kpis(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_kpis(db)


@router.get("/breakdowns", response_model=schemas.BreakdownOut)
def breakdowns(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_breakdowns(db)
