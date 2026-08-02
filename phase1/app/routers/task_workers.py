"""
Enhancement backlog Phase 21 (NJ2026_Work_Order_System_PRD.md §17#10):
Task Team assignment — the delegated worker-management half.

Deliberately separate from app/routers/users.py (the Admin/LOC-scoped
user-management endpoints): per an explicit decision (7/31/26), Task
Worker setup is delegated to Dispatchers (the existing tech/team-lead
role) managing their own team's workers directly, not something routed
through Admin or LOC. Every endpoint here is scoped to "my own team"
automatically from the caller's own team_id — there's no way for a
Dispatcher to create or see another team's workers, by construction,
not just convention.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud, schemas, models
from ..database import get_db
from ..auth import require_roles

router = APIRouter(prefix="/my-team/workers", tags=["task-workers"])

tech_only = require_roles("tech")


def _get_worker_or_404(db: Session, worker_id: int, team_id: int) -> models.User:
    """Scoped lookup, not a generic get-by-id-then-check — a Dispatcher
    asking for a worker_id that exists but belongs to a different team
    gets the same 404 as a worker_id that doesn't exist at all, rather
    than a 403 that would confirm the id is real."""
    worker = db.query(models.User).filter(
        models.User.id == worker_id, models.User.role == "task_worker",
        models.User.team_id == team_id,
    ).first()
    if not worker:
        raise HTTPException(404, "worker not found")
    return worker


@router.get("", response_model=list[schemas.TaskWorkerOut])
def list_my_workers(db: Session = Depends(get_db), user: models.User = Depends(tech_only)):
    return crud.list_task_workers(db, user.team_id)


@router.post("", response_model=schemas.TaskWorkerCreated, status_code=201)
def create_my_worker(payload: schemas.TaskWorkerCreate, db: Session = Depends(get_db),
                      user: models.User = Depends(tech_only)):
    """Returns the plaintext PIN once — the Dispatcher is responsible
    for sharing it with the worker (verbally, over radio, written down).
    It can't be retrieved again after this response; a Dispatcher who
    loses it has to regenerate one (no reset endpoint yet — small enough
    gap to leave for a follow-up rather than block this build on it)."""
    worker, pin = crud.create_task_worker(db, user.team_id, payload)
    return schemas.TaskWorkerCreated(worker=worker, pin=pin)


@router.delete("/{worker_id}", response_model=schemas.TaskWorkerOut)
def deactivate_my_worker(worker_id: int, db: Session = Depends(get_db),
                          user: models.User = Depends(tech_only)):
    worker = _get_worker_or_404(db, worker_id, user.team_id)
    return crud.deactivate_task_worker(db, worker)
