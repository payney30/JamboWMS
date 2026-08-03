import os
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session
from typing import Optional

from .. import crud, schemas, models
from ..database import get_db
from ..auth import require_roles, get_current_user
from .public import UPLOAD_DIR, ALLOWED_CONTENT_TYPES, MAX_FILE_BYTES

router = APIRouter(prefix="/work-orders", tags=["work-orders"])

loc_or_admin = require_roles("loc", "admin")
tech_or_loc_or_admin = require_roles("tech", "loc", "admin")

# Techs work a request-to-close queue, not the LOC's triage states — they
# can't move a WO back to "Requested" or hand-set "Assigned" (that's what
# the /assign endpoint is for). Enforced here rather than left to the
# frontend since this is a real permissions boundary, not just a UI nicety.
# Lives in schemas.py now (also used by the combined /save endpoint below)
# — re-exported here so nothing importing it from this module breaks.
TECH_ALLOWED_STATUSES = schemas.TECH_ALLOWED_STATUSES


def _get_wo_or_404(db: Session, wo_id: int) -> models.WorkOrder:
    wo = db.get(models.WorkOrder, wo_id)
    if not wo:
        raise HTTPException(404, "work order not found")
    return wo


# Enhancement backlog Phase 26 (§17 follow-up, 8/2/26): audience-scoped,
# read-only dashboard roles — each can only ever view WOs within their
# own reporting group, mirroring the exact same location_group values
# crud._apply_filters' scope="program"/"basecamp" branches already use.
VIEWER_ROLE_SCOPES = {"program_viewer": "Program Areas", "basecamp_viewer": "Base Camps"}


def _enforce_team_scope(wo: models.WorkOrder, user: models.User):
    """A tech can only touch a WO currently assigned to their own team —
    this is the server-side backing for PRD 4.3's 'each team sees only
    their queue.' LOC/admin are unrestricted.

    Enhancement backlog Phase 21 (PRD §17#10): a Task Worker is scoped
    one level narrower still — only a WO assigned to *them personally*,
    not just their team. Checked here (not a separate function) so
    every existing caller of this — get_work_order, status changes,
    etc. — picks up the restriction automatically.

    Enhancement backlog Phase 26 (§17 follow-up, 8/2/26): program_viewer/
    basecamp_viewer are scoped by *location*, not team or personal
    assignment — a WO outside their reporting group is invisible to
    them, full stop, even by direct WO-id lookup. This function only
    ever runs for callers who've already cleared the endpoint's own
    role check, so a viewer role reaching here for a *mutating*
    endpoint (status/notes) is already blocked by _reject_if_view_only
    below before this scope check would even matter — this function's
    job is narrowing what's visible, not deciding who may write."""
    if user.role == "tech" and wo.assigned_team_id != user.team_id:
        raise HTTPException(403, "this work order is not assigned to your team")
    if user.role == "task_worker" and wo.assigned_person_id != user.id:
        raise HTTPException(403, "this work order is not assigned to you")
    if user.role in VIEWER_ROLE_SCOPES:
        required_group = VIEWER_ROLE_SCOPES[user.role]
        if not wo.asset or wo.asset.location_group != required_group:
            raise HTTPException(403, "this work order is outside your assigned view")


def _reject_if_view_only(user: models.User):
    """Enhancement backlog Phase 26 (§17 follow-up, 8/2/26): these two
    roles are read-only, full stop — not "read-only except for a few
    narrow actions." Explicitly rejects at the top of the two mutating
    endpoints (change_status, add_note) that were otherwise only gated
    by get_current_user (any authenticated role) — every other
    mutating endpoint in this router already requires tech/loc/admin
    via require_roles, which naturally excludes these roles without
    needing this check, but these two didn't have that protection."""
    if user.role in VIEWER_ROLE_SCOPES:
        raise HTTPException(403, "this account has read-only access")


def _enforce_not_locked(wo: models.WorkOrder, user: models.User):
    """Enhancement backlog Phase 1 (PRD §14#1) — server-side backing for
    WO locking. A WO opened for edit by one LOC/admin user can't be
    mutated by another until they save/close it (releasing the lock) or
    it goes stale. Admins can bypass, same as they can force-unlock —
    useful for breaking a genuinely stuck lock without waiting out the
    timeout.

    Deliberately scoped to loc/admin callers only: locking was requested
    for the LOC triage screen (static/index.html), which is what opens
    locks in the first place. Technicians work their queue from a
    separate page (static/technician.html) that has no lock UI at all —
    enforcing this against them too would produce confusing, unexplained
    409s for a workflow that was never part of this ask. If technician.html
    ever needs the same protection, it should first gain its own lock
    acquire/release calls, same as the triage drawer has.
    """
    if user.role not in ("loc", "admin"):
        return
    holder = wo.locked_by
    if holder and holder.id != user.id and user.role != "admin":
        raise HTTPException(
            409,
            f"This work order is currently open for editing by {holder.name}. "
            "Try again once they save or close it.",
        )


@router.get("", response_model=list[schemas.WorkOrderListItem])
def list_work_orders(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    team_id: Optional[int] = None,
    work_type: Optional[str] = None,
    location_group: Optional[str] = None,
    asset_id: Optional[int] = None,  # PRD §16#5: technician queue location filter
    search: Optional[str] = None,
    exclude_closed: bool = False,
    closed_only: bool = False,
    priority_in: Optional[str] = None,  # comma-separated, e.g. "Immediate,Same Day"
    status_in: Optional[str] = None,  # comma-separated, e.g. "Assigned,On Hold,Work In Progress"
    unassigned_person: bool = False,  # PRD §17 Phase 24: "filter by assigned worker" -> Unassigned
    opened_today: bool = False,
    closed_today: bool = False,
    handled_by: Optional[int] = None,  # PRD §14#17: "work orders I've handled"
    approaching_deadline: bool = False,  # PRD §14#10
    past_deadline: bool = False,  # PRD §14#10
    # Bug fix (found in LOC triage testing, 8/1/26): LOC's whole job is
    # to see everything — a low default cap could silently hide newer/
    # lower-priority WOs once total volume grows past it. Raised well
    # past any single-event's realistic total.
    limit: int = Query(100, le=2000),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    # Techs can't widen their view past their own team's queue by editing
    # the query string — the server, not the frontend, owns this boundary.
    if user.role == "tech":
        team_id = user.team_id
    # Enhancement backlog Phase 21 (PRD §17#10): same principle, one
    # level narrower — a Task Worker only ever sees WOs assigned to them
    # personally, never their whole team's queue (that's the Dispatcher's
    # view). assigned_person_id is a query param a client *could* try to
    # pass, but it's always overridden to the caller's own id here.
    assigned_person_id = None
    if user.role == "task_worker":
        assigned_person_id = user.id
    # Enhancement backlog Phase 26 (§17 follow-up, 8/2/26): same
    # principle again — program_viewer/basecamp_viewer can't widen their
    # view past their assigned reporting group by passing a different
    # location_group (or none at all) in the query string.
    if user.role in VIEWER_ROLE_SCOPES:
        location_group = VIEWER_ROLE_SCOPES[user.role]
    priority_list = [p.strip() for p in priority_in.split(",")] if priority_in else None
    status_list = [s.strip() for s in status_in.split(",")] if status_in else None
    return crud.list_work_orders(
        db, status=status, priority=priority, team_id=team_id,
        work_type=work_type, location_group=location_group, asset_id=asset_id, search=search,
        exclude_closed=exclude_closed, closed_only=closed_only, priority_in=priority_list,
        status_in=status_list, unassigned_person=unassigned_person,
        opened_today=opened_today, closed_today=closed_today, handled_by=handled_by,
        approaching_deadline=approaching_deadline, past_deadline=past_deadline,
        assigned_person_id=assigned_person_id,
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
    _enforce_not_locked(wo, user)
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
    _enforce_not_locked(wo, user)
    return crud.assign_work_order(db, wo, payload, changed_by=user.id)


@router.post("/{wo_id}/status", response_model=schemas.WorkOrderDetail)
def change_status(wo_id: int, payload: schemas.StatusChangeRequest,
                   db: Session = Depends(get_db),
                   user: models.User = Depends(get_current_user)):
    _reject_if_view_only(user)
    wo = _get_wo_or_404(db, wo_id)
    _enforce_team_scope(wo, user)
    _enforce_not_locked(wo, user)
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
    _reject_if_view_only(user)
    wo = _get_wo_or_404(db, wo_id)
    _enforce_team_scope(wo, user)
    _enforce_not_locked(wo, user)
    if user.role == "tech" and payload.note_type == "instruction":
        raise HTTPException(403, "instructions are LOC-authored; technicians can add work notes")
    return crud.add_note(db, wo, payload, author_id=user.id)


@router.post("/{wo_id}/lock", response_model=schemas.LockOut)
def lock_work_order(wo_id: int, db: Session = Depends(get_db),
                     user: models.User = Depends(tech_or_loc_or_admin)):
    """Enhancement backlog Phase 1 (PRD §14#1). Called when the triage
    drawer opens a WO for edit. A no-op success if the caller already
    holds the lock; a 409 (with who + since when) if someone else does —
    the frontend uses that to fall back to a read-only view."""
    wo = _get_wo_or_404(db, wo_id)
    _enforce_team_scope(wo, user)
    wo = crud.acquire_lock(db, wo, user)
    return schemas.LockOut(locked=True, locked_by=wo.locked_by, locked_at=wo.locked_at)


@router.post("/{wo_id}/unlock", response_model=schemas.LockOut)
def unlock_work_order(wo_id: int, db: Session = Depends(get_db),
                       user: models.User = Depends(tech_or_loc_or_admin)):
    """Called when the drawer closes without saving (or after a save —
    though save_work_order below already releases the lock itself).
    Admins can force-clear someone else's lock; anyone else releasing a
    lock they don't hold gets a 403."""
    wo = _get_wo_or_404(db, wo_id)
    wo = crud.release_lock(db, wo, user, force=(user.role == "admin"))
    return schemas.LockOut(locked=False, locked_by=None, locked_at=None)


@router.post("/{wo_id}/save", response_model=schemas.WorkOrderDetail)
def save_work_order(wo_id: int, payload: schemas.WorkOrderSaveRequest,
                     db: Session = Depends(get_db),
                     user: models.User = Depends(tech_or_loc_or_admin)):
    """Enhancement backlog Phase 1 (PRD §14#2) — the single Save action
    for the WO detail drawer. Applies whichever sections of the payload
    are present (details/status/assignment/note) in one transaction and
    releases the WO's edit lock. Per-field permissions mirror the
    granular endpoints above exactly, just checked together here instead
    of via separate `Depends(loc_or_admin)` dependencies."""
    wo = _get_wo_or_404(db, wo_id)
    _enforce_team_scope(wo, user)
    _enforce_not_locked(wo, user)

    details_touched = any(v is not None for v in (
        payload.description, payload.work_type, payload.priority,
        payload.asset_id, payload.note_to_requester,
    ))
    if details_touched and user.role not in ("loc", "admin"):
        raise HTTPException(403, "only LOC/admin can edit work order details")

    if payload.status is not None and user.role == "tech" \
            and payload.status not in TECH_ALLOWED_STATUSES:
        raise HTTPException(
            403,
            f"technicians can't set status to '{payload.status}' — "
            f"allowed: {', '.join(sorted(TECH_ALLOWED_STATUSES))}",
        )

    if payload.new_note_text and payload.new_note_type == "instruction" and user.role == "tech":
        raise HTTPException(403, "instructions are LOC-authored; technicians can add work notes")

    return crud.save_work_order(db, wo, payload, changed_by=user.id)


# ---- Enhancement backlog Phase 21 (PRD §17#10): Task Team assignment ----

task_worker_only = require_roles("task_worker")


@router.post("/{wo_id}/complete", response_model=schemas.WorkOrderDetail)
def complete_work_order(wo_id: int, payload: schemas.CompleteWorkOrderRequest,
                         db: Session = Depends(get_db),
                         user: models.User = Depends(task_worker_only)):
    """The 'simple Completed button' — a Task Worker's single-action way
    to close out their own assigned WO. Locking (§14#1) doesn't apply
    here — that's a LOC/tech edit-drawer concept for the fuller editing
    surface, not this narrower action."""
    wo = _get_wo_or_404(db, wo_id)
    _enforce_team_scope(wo, user)  # covers the task_worker "must be assigned to me" check
    return crud.complete_work_order(db, wo, payload, changed_by=user.id)


@router.post("/{wo_id}/attachments", response_model=schemas.AttachmentOut, status_code=201)
async def upload_completion_photo(wo_id: int, file: UploadFile = File(...),
                                   db: Session = Depends(get_db),
                                   user: models.User = Depends(task_worker_only)):
    """A Task Worker's completion photo — deliberately its own endpoint
    (not reusing the public submission upload path) since it's
    authenticated, scoped to the worker's own assigned WO, and always
    tagged stage='completion'. Same validation (allowed types, size
    limit) as the public upload."""
    wo = _get_wo_or_404(db, wo_id)
    _enforce_team_scope(wo, user)
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, "unsupported file type")
    contents = await file.read()
    if len(contents) > MAX_FILE_BYTES:
        raise HTTPException(400, "file too large (10 MB max)")
    ext = os.path.splitext(file.filename or "")[1][:10]
    safe_name = f"{wo.wo_number}-completion-{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, safe_name), "wb") as out:
        out.write(contents)
    return crud.add_attachment(db, wo, f"/uploads/{safe_name}", uploaded_by=user.id, stage="completion")
