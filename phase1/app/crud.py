"""
CRUD + the status-history engine.

The one rule that matters in this whole file: any change to
WorkOrder.status / .assigned_team_id / .priority happens in the SAME
db.commit() as the matching WOStatusHistory row. If you add a new field
mutation later, ask "does this need a history row?" before wiring it up.
"""
import datetime as dt
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from fastapi import HTTPException
from . import models, schemas

# Lower rank = higher priority = sorts first. Ties (an unrecognized value,
# which the CHECK constraint shouldn't allow, but belt-and-suspenders)
# fall to the end rather than erroring.
_PRIORITY_RANK = case(
    (models.WorkOrder.priority == "Highest", 0),
    (models.WorkOrder.priority == "High", 1),
    (models.WorkOrder.priority == "Medium", 2),
    (models.WorkOrder.priority == "Low", 3),
    (models.WorkOrder.priority == "Lowest", 4),
    else_=5,
)


def _next_wo_number(db: Session) -> str:
    last = db.query(models.WorkOrder).order_by(models.WorkOrder.id.desc()).first()
    next_id = (last.id + 1) if last else 10001
    return f"WO-{next_id}"


def create_work_order(db: Session, payload: schemas.WorkOrderCreate) -> models.WorkOrder:
    asset = db.get(models.Asset, payload.asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")

    wo = models.WorkOrder(
        wo_number=_next_wo_number(db),
        requester_name=payload.requester_name,
        requester_email=payload.requester_email,
        requester_phone=payload.requester_phone,
        asset_id=payload.asset_id,
        work_type=payload.work_type,
        description=payload.description,
        priority=payload.priority,
        notify_preference=payload.notify_preference,
        external_ref=payload.external_ref,
        status="Requested",
    )
    db.add(wo)
    db.flush()  # get wo.id before writing history

    db.add(models.WOStatusHistory(
        work_order_id=wo.id,
        event_type="status_change",
        from_value=None,
        to_value="Requested",
        changed_by=None,
    ))
    db.commit()
    db.refresh(wo)
    return wo


def add_attachment(db: Session, wo: models.WorkOrder, file_url: str,
                    uploaded_by: int | None = None) -> models.WOAttachment:
    """No history row here on purpose — attachments aren't one of the
    tracked mutation fields (status/team/priority) the status-history
    engine covers; they're just files hung off the WO."""
    att = models.WOAttachment(work_order_id=wo.id, uploaded_by=uploaded_by, file_url=file_url)
    db.add(att)
    db.commit()
    db.refresh(att)
    return att


def update_work_order_fields(db: Session, wo: models.WorkOrder, payload: schemas.WorkOrderUpdate,
                              changed_by: int | None) -> models.WorkOrder:
    """Non-status/team fields (description, work_type, location). Priority
    changes DO get a history row since Section 6 of the PRD calls priority
    changes out as reportable."""
    if payload.priority is not None and payload.priority != wo.priority:
        db.add(models.WOStatusHistory(
            work_order_id=wo.id,
            event_type="priority_change",
            from_value=wo.priority,
            to_value=payload.priority,
            changed_by=changed_by,
        ))
        wo.priority = payload.priority

    if payload.description is not None:
        wo.description = payload.description
    if payload.work_type is not None:
        wo.work_type = payload.work_type
    if payload.asset_id is not None:
        asset = db.get(models.Asset, payload.asset_id)
        if not asset:
            raise HTTPException(404, "asset not found")
        wo.asset_id = payload.asset_id

    db.commit()
    db.refresh(wo)
    return wo


def assign_work_order(db: Session, wo: models.WorkOrder, payload: schemas.AssignRequest,
                       changed_by: int | None) -> models.WorkOrder:
    team = db.get(models.Team, payload.team_id)
    if not team:
        raise HTTPException(404, "team not found")

    is_reroute = wo.assigned_team_id is not None and wo.assigned_team_id != payload.team_id
    if is_reroute and not payload.note:
        raise HTTPException(400, "a reason note is required when reassigning to a different team")

    from_team_name = wo.assigned_team.name if wo.assigned_team else None
    wo.assigned_team_id = payload.team_id
    wo.assigned_person_id = payload.person_id
    if wo.status == "Requested":
        wo.status = "Assigned"
        db.add(models.WOStatusHistory(
            work_order_id=wo.id, event_type="status_change",
            from_value="Requested", to_value="Assigned", changed_by=changed_by,
        ))

    db.add(models.WOStatusHistory(
        work_order_id=wo.id,
        event_type="reassignment",
        from_value=from_team_name,
        to_value=team.name,
        changed_by=changed_by,
    ))

    if payload.note:
        db.add(models.WONote(
            work_order_id=wo.id, author_id=changed_by,
            note_text=payload.note, note_type="internal",
        ))

    db.commit()
    db.refresh(wo)
    return wo


def change_status(db: Session, wo: models.WorkOrder, payload: schemas.StatusChangeRequest,
                   changed_by: int | None) -> models.WorkOrder:
    if payload.status not in schemas.STATUSES:
        raise HTTPException(400, f"invalid status: {payload.status}")

    from_status = wo.status
    wo.status = payload.status
    if payload.status.startswith("Closed"):
        wo.closed_at = dt.datetime.utcnow()
    else:
        wo.closed_at = None

    db.add(models.WOStatusHistory(
        work_order_id=wo.id, event_type="status_change",
        from_value=from_status, to_value=payload.status, changed_by=changed_by,
    ))

    if payload.note:
        note_type = "work_note" if payload.status.startswith("Closed") else "internal"
        db.add(models.WONote(
            work_order_id=wo.id, author_id=changed_by,
            note_text=payload.note, note_type=note_type,
        ))

    db.commit()
    db.refresh(wo)
    return wo


def add_note(db: Session, wo: models.WorkOrder, payload: schemas.NoteCreate,
              author_id: int | None) -> models.WONote:
    note = models.WONote(
        work_order_id=wo.id, author_id=author_id,
        note_text=payload.note_text, note_type=payload.note_type,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def list_work_orders(db: Session, status=None, priority=None, team_id=None,
                      work_type=None, location_group=None, search=None,
                      limit=100, offset=0):
    q = db.query(models.WorkOrder)
    if status:
        q = q.filter(models.WorkOrder.status == status)
    if priority:
        q = q.filter(models.WorkOrder.priority == priority)
    if team_id:
        q = q.filter(models.WorkOrder.assigned_team_id == team_id)
    if work_type:
        q = q.filter(models.WorkOrder.work_type == work_type)
    if location_group:
        q = q.join(models.Asset).filter(models.Asset.location_group == location_group)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (models.WorkOrder.description.ilike(like)) |
            (models.WorkOrder.wo_number.ilike(like)) |
            (models.WorkOrder.external_ref.ilike(like))
        )
    # PRD 4.2: inbox sorts highest-priority first, oldest first within a
    # priority tier — matches the old dashboard's "needing attention" table.
    return (
        q.order_by(_PRIORITY_RANK.asc(), models.WorkOrder.created_at.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_kpis(db: Session) -> dict:
    total = db.query(func.count(models.WorkOrder.id)).scalar()
    closed = db.query(func.count(models.WorkOrder.id)).filter(
        models.WorkOrder.status.like("Closed%")
    ).scalar()
    open_ = total - closed
    rate = round((closed / total) * 100, 1) if total else 0.0

    today = dt.date.today()
    opened_today = db.query(func.count(models.WorkOrder.id)).filter(
        func.date(models.WorkOrder.created_at) == today
    ).scalar()
    closed_today = db.query(func.count(models.WOStatusHistory.id)).filter(
        models.WOStatusHistory.event_type == "status_change",
        models.WOStatusHistory.to_value.like("Closed%"),
        func.date(models.WOStatusHistory.changed_at) == today,
    ).scalar()

    return {
        "total": total, "open": open_, "closed": closed,
        "completion_rate": rate, "opened_today": opened_today,
        "closed_today": closed_today,
    }


def get_breakdowns(db: Session) -> dict:
    def counts(col):
        rows = db.query(col, func.count(models.WorkOrder.id)).group_by(col).all()
        return {k or "Unset": v for k, v in rows}

    by_location = dict(
        db.query(models.Asset.location_group, func.count(models.WorkOrder.id))
        .join(models.WorkOrder, models.WorkOrder.asset_id == models.Asset.id)
        .group_by(models.Asset.location_group).all()
    )
    by_team = dict(
        db.query(models.Team.name, func.count(models.WorkOrder.id))
        .join(models.WorkOrder, models.WorkOrder.assigned_team_id == models.Team.id)
        .group_by(models.Team.name).all()
    )
    return {
        "by_status": counts(models.WorkOrder.status),
        "by_priority": counts(models.WorkOrder.priority),
        "by_work_type": counts(models.WorkOrder.work_type),
        "by_location": by_location,
        "by_team": by_team,
    }
