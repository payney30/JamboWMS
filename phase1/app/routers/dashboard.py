from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Literal, Optional

from .. import crud, schemas, models
from ..database import get_db
from ..auth import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

Scope = Literal["main", "program", "basecamp"]

# Enhancement backlog Phase 26 (§17 follow-up, 8/2/26): audience-scoped,
# read-only dashboard roles — same VIEWER_ROLE_SCOPES concept as
# app/routers/work_orders.py, expressed in this router's own "scope"
# vocabulary (program/basecamp) rather than a location_group string.
VIEWER_ROLE_FORCED_SCOPE = {"program_viewer": "program", "basecamp_viewer": "basecamp"}


def _effective_scope(user: models.User, requested_scope: str) -> str:
    """A program_viewer/basecamp_viewer can't widen their view by
    passing a different `scope` query param — the server, not the
    frontend, owns this boundary, same principle as team_id/
    location_group being forced elsewhere in this app for tech/
    task_worker/these same two roles."""
    return VIEWER_ROLE_FORCED_SCOPE.get(user.role, requested_scope)


def _filter_params(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    work_type: Optional[str] = None,
    team_id: Optional[int] = None,
    location_group: Optional[str] = None,
    asset_id: Optional[int] = None,  # PRD §17#14: LocationPicker upgrade
    search: Optional[str] = None,
    exclude_closed: bool = False,  # PRD §17#14: clickable KPI tiles
    closed_only: bool = False,
    # Bug fix (end-to-end testing 8/10/26): _apply_filters (see crud.py)
    # already supports status_in specifically so "Active" can mean
    # Assigned/On Hold/Work In Progress without also matching Requested
    # (which has its own separate tile) — same convention LOC triage's
    # Open/Active tile already uses (index.html, comma-separated). This
    # router just never exposed it as a query param, so these dashboards
    # had no way to ask for it and fell back to exclude_closed (plain
    # "not closed," which incorrectly included Requested).
    status_in: Optional[str] = None,  # comma-separated, e.g. "Assigned,On Hold,Work In Progress"
) -> dict:
    return {
        "status": status, "priority": priority, "work_type": work_type,
        "team_id": team_id, "location_group": location_group, "asset_id": asset_id,
        "search": search, "exclude_closed": exclude_closed, "closed_only": closed_only,
        "status_in": [s.strip() for s in status_in.split(",")] if status_in else None,
    }


@router.get("/kpis", response_model=schemas.KPIOut)
def kpis(scope: Scope = "main", filters: dict = Depends(_filter_params),
         db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_kpis(db, scope=_effective_scope(user, scope), **filters)


@router.get("/breakdowns", response_model=schemas.BreakdownOut)
def breakdowns(scope: Scope = "main", filters: dict = Depends(_filter_params),
               db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_breakdowns(db, scope=_effective_scope(user, scope), **filters)


@router.get("/trend")
def trend(scope: Scope = "main", days: int = Query(14, ge=1, le=90),
          filters: dict = Depends(_filter_params),
          db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_daily_trend(db, scope=_effective_scope(user, scope), days=days, **filters)


@router.get("/attention", response_model=list[schemas.WorkOrderListItem])
def attention(scope: Scope = "main", limit: int = Query(15, ge=1, le=50),
              filters: dict = Depends(_filter_params),
              db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    return crud.get_needing_attention(db, scope=_effective_scope(user, scope), limit=limit, **filters)
