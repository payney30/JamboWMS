"""
LOC triage inventory search (PRD §4.5e) — read-only lookup against the
warehouse SKU catalog, used by the "Suggested supplies" widget in the
triage drawer (static/index.html). The catalog itself is admin-managed;
see app/routers/admin.py's /admin/inventory/preview and /apply.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from .. import crud, schemas, models
from ..database import get_db
from ..auth import require_roles

router = APIRouter(prefix="/inventory", tags=["inventory"])
loc_or_admin = require_roles("loc", "admin")


@router.get("/search", response_model=list[schemas.InventoryItemOut])
def search_inventory(
    q: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    include_zero: bool = Query(default=False),
    limit: int = Query(default=25, le=100),
    db: Session = Depends(get_db),
    user: models.User = Depends(loc_or_admin),
):
    return crud.search_inventory(db, q=q, category=category, include_zero=include_zero, limit=limit)
