"""
CRUD + the status-history engine.

The one rule that matters in this whole file: any change to
WorkOrder.status / .assigned_team_id / .priority happens in the SAME
db.commit() as the matching WOStatusHistory row. If you add a new field
mutation later, ask "does this need a history row?" before wiring it up.
"""
import datetime as dt
import secrets
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from fastapi import HTTPException
from . import models, schemas
from .auth import hash_password

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


def build_location_tree(db: Session, include_inactive: bool = False) -> list[dict]:
    """Reconstruct the nested location hierarchy (PRD 4.2a) from the flat
    assets table. Every Asset row (branch, camp, subcamp, shower house,
    leaf) becomes one tree node; parent_id links them.

    include_inactive=False (the default, used by both the requester form
    and LOC triage pickers) prunes soft-deleted nodes AND everything under
    them, so a deleted branch can't resurface via an active child — new
    selections never see it. include_inactive=True is for the future admin
    hierarchy view (PRD 4.5), which needs to show inactive nodes to allow
    restoring them.
    """
    q = db.query(models.Asset)
    if not include_inactive:
        q = q.filter(models.Asset.is_active == True)  # noqa: E712
    rows = q.order_by(models.Asset.sort_order, models.Asset.name).all()

    by_id = {a.id: a for a in rows}
    children_by_parent: dict[int | None, list] = {}
    for a in rows:
        # An active node whose parent was pruned (parent inactive, or
        # missing from this filtered set) has no reachable parent in this
        # view — treat it as a root rather than dropping it silently.
        parent_key = a.parent_id if (a.parent_id in by_id or a.parent_id is None) else None
        children_by_parent.setdefault(parent_key, []).append(a)

    def node(a) -> dict:
        return {
            "id": a.id,
            "name": a.name,
            "code": a.code,
            "branch_label": a.location_group,
            "is_active": a.is_active,
            "children": [node(c) for c in children_by_parent.get(a.id, [])],
        }

    return [node(a) for a in children_by_parent.get(None, [])]


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
        poc_is_requester=payload.poc_is_requester,
        poc_name=None if payload.poc_is_requester else payload.poc_name,
        poc_phone=None if payload.poc_is_requester else payload.poc_phone,
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

    if payload.person_id is not None:
        person = db.get(models.User, payload.person_id)
        if not person or person.team_id != payload.team_id:
            raise HTTPException(400, "person must belong to the team they're being assigned into")

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
                      exclude_closed=False, closed_only=False, priority_in=None,
                      opened_today=False, closed_today=False,
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
    # These back the LOC triage KPI-card quick views (Open/Active,
    # Highest+High Open, Closed, Opened Today, Closed Today). They used to
    # be applied client-side against whatever page the default limit
    # happened to return — with priority-then-oldest-first sort and a
    # limit far below the total row count, a newly-created low-priority
    # WO (exactly the "Opened Today" case) would sort near the *end* and
    # never make it into the fetched page at all, so the client-side
    # filter had nothing to find even though the WO existed. Doing this
    # filtering in SQL means pagination is applied *after* filtering, so
    # nothing gets silently excluded before the client ever sees it.
    if exclude_closed:
        q = q.filter(~models.WorkOrder.status.like("Closed%"))
    if closed_only:
        q = q.filter(models.WorkOrder.status.like("Closed%"))
    if priority_in:
        q = q.filter(models.WorkOrder.priority.in_(priority_in))
    if opened_today:
        q = q.filter(func.date(models.WorkOrder.created_at) == dt.date.today())
    if closed_today:
        q = q.filter(
            models.WorkOrder.status.like("Closed%"),
            func.date(models.WorkOrder.closed_at) == dt.date.today(),
        )
    # PRD 4.2: inbox sorts highest-priority first, oldest first within a
    # priority tier — matches the old dashboard's "needing attention" table.
    return (
        q.order_by(_PRIORITY_RANK.asc(), models.WorkOrder.created_at.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def _apply_filters(db: Session, query, scope: str, status: str | None = None,
                    priority: str | None = None, work_type: str | None = None,
                    team_id: int | None = None, location_group: str | None = None,
                    search: str | None = None):
    """Scope (main/program/basecamp) plus the same fine-grained filters the
    original static dashboards had (status/priority/work type/team/location/
    search) — narrows further *within* whichever scope tab/page you're on.
    Filters via subqueries on asset_id rather than explicit joins, so this
    stays safe to combine with queries that already join Asset or Team
    themselves (e.g. the by_location/by_team breakdowns) — an explicit join
    here would double-join and blow up with an ambiguous-column error."""
    if scope == "program":
        ids = db.query(models.Asset.id).filter(models.Asset.location_group == "Program Areas")
        query = query.filter(models.WorkOrder.asset_id.in_(ids))
    elif scope == "basecamp":
        # PRD: Base Camp Ops dashboard scope stays Charlie/Delta/Echo only.
        ids = db.query(models.Asset.id).filter(models.Asset.camp_letter.in_(["C", "D", "E"]))
        query = query.filter(models.WorkOrder.asset_id.in_(ids))

    if status:
        query = query.filter(models.WorkOrder.status == status)
    if priority:
        query = query.filter(models.WorkOrder.priority == priority)
    if work_type:
        query = query.filter(models.WorkOrder.work_type == work_type)
    if team_id:
        query = query.filter(models.WorkOrder.assigned_team_id == team_id)
    if location_group:
        ids = db.query(models.Asset.id).filter(models.Asset.location_group == location_group)
        query = query.filter(models.WorkOrder.asset_id.in_(ids))
    if search:
        like = f"%{search}%"
        query = query.filter(
            (models.WorkOrder.description.ilike(like)) | (models.WorkOrder.wo_number.ilike(like))
        )
    return query


def get_kpis(db: Session, scope: str = "main", **filters) -> dict:
    base = _apply_filters(db, db.query(models.WorkOrder.id), scope, **filters)
    total = base.count()
    closed = _apply_filters(db, db.query(models.WorkOrder.id), scope, **filters).filter(
        models.WorkOrder.status.like("Closed%")
    ).count()
    open_ = total - closed
    rate = round((closed / total) * 100, 1) if total else 0.0

    highest_high_open = _apply_filters(db, db.query(models.WorkOrder.id), scope, **filters).filter(
        ~models.WorkOrder.status.like("Closed%"),
        models.WorkOrder.priority.in_(["Highest", "High"]),
    ).count()

    today = dt.date.today()
    opened_today = _apply_filters(db, db.query(models.WorkOrder.id), scope, **filters).filter(
        func.date(models.WorkOrder.created_at) == today
    ).count()

    closed_today_q = db.query(models.WOStatusHistory.id).join(
        models.WorkOrder, models.WOStatusHistory.work_order_id == models.WorkOrder.id
    ).filter(
        models.WOStatusHistory.event_type == "status_change",
        models.WOStatusHistory.to_value.like("Closed%"),
        func.date(models.WOStatusHistory.changed_at) == today,
    )
    closed_today = _apply_filters(db, closed_today_q, scope, **filters).count()

    return {
        "total": total, "open": open_, "closed": closed,
        "highest_high_open": highest_high_open,
        "completion_rate": rate, "opened_today": opened_today,
        "closed_today": closed_today,
    }


def get_breakdowns(db: Session, scope: str = "main", **filters) -> dict:
    def counts(col):
        q = _apply_filters(db, db.query(col, func.count(models.WorkOrder.id)), scope, **filters)
        rows = q.group_by(col).all()
        return {k or "Unset": v for k, v in rows}

    by_location_q = _apply_filters(
        db,
        db.query(models.Asset.location_group, func.count(models.WorkOrder.id))
        .join(models.WorkOrder, models.WorkOrder.asset_id == models.Asset.id),
        scope, **filters,
    )
    by_location = dict(by_location_q.group_by(models.Asset.location_group).all())

    by_team_q = _apply_filters(
        db,
        db.query(models.Team.name, func.count(models.WorkOrder.id))
        .join(models.WorkOrder, models.WorkOrder.assigned_team_id == models.Team.id),
        scope, **filters,
    )
    by_team = dict(by_team_q.group_by(models.Team.name).all())

    return {
        "by_status": counts(models.WorkOrder.status),
        "by_priority": counts(models.WorkOrder.priority),
        "by_work_type": counts(models.WorkOrder.work_type),
        "by_location": by_location,
        "by_team": by_team,
    }


def get_daily_trend(db: Session, scope: str = "main", days: int = 14, **filters) -> list[dict]:
    """New WOs opened per day and WOs closed per day, for the trend line
    on PRD 4.4's dashboards — this is the thing that made 'closed today'
    trivial with a real DB instead of the old snapshot-diff file."""
    start = dt.date.today() - dt.timedelta(days=days - 1)

    opened_q = _apply_filters(
        db,
        db.query(func.date(models.WorkOrder.created_at).label("d"), func.count(models.WorkOrder.id)),
        scope, **filters,
    ).filter(func.date(models.WorkOrder.created_at) >= start).group_by("d")
    opened_by_day = dict(opened_q.all())

    closed_q = _apply_filters(
        db,
        db.query(func.date(models.WOStatusHistory.changed_at).label("d"), func.count(models.WOStatusHistory.id))
        .join(models.WorkOrder, models.WOStatusHistory.work_order_id == models.WorkOrder.id)
        .filter(
            models.WOStatusHistory.event_type == "status_change",
            models.WOStatusHistory.to_value.like("Closed%"),
            func.date(models.WOStatusHistory.changed_at) >= start,
        ),
        scope, **filters,
    ).group_by("d")
    closed_by_day = dict(closed_q.all())

    out = []
    for i in range(days):
        d = start + dt.timedelta(days=i)
        key = d.isoformat()
        # some drivers return date objects, some return strings — normalize
        opened = opened_by_day.get(d) or opened_by_day.get(key) or 0
        closed = closed_by_day.get(d) or closed_by_day.get(key) or 0
        out.append({"date": key, "opened": opened, "closed": closed})
    return out


def get_needing_attention(db: Session, scope: str = "main", limit: int = 15, **filters) -> list[models.WorkOrder]:
    """Highest/High priority, still open, oldest first — matches the
    'needing attention' table from the original dashboards and the LOC
    triage inbox's default sort."""
    q = _apply_filters(
        db,
        db.query(models.WorkOrder).filter(
            ~models.WorkOrder.status.like("Closed%"),
            models.WorkOrder.priority.in_(["Highest", "High"]),
        ),
        scope, **filters,
    )
    return q.order_by(models.WorkOrder.created_at.asc()).limit(limit).all()


def _generate_temp_password() -> str:
    return secrets.token_urlsafe(9)  # ~12 chars, URL-safe — easy to read aloud/text


def list_users(db: Session, role: str | None = None, team_id: int | None = None,
                include_inactive: bool = False) -> list[models.User]:
    q = db.query(models.User)
    if not include_inactive:
        q = q.filter(models.User.is_active == True)  # noqa: E712
    if role:
        q = q.filter(models.User.role == role)
    if team_id:
        q = q.filter(models.User.team_id == team_id)
    return q.order_by(models.User.name).all()


def create_user(db: Session, payload: schemas.UserCreate) -> tuple[models.User, str]:
    if payload.role not in schemas.ROLES:
        raise HTTPException(400, f"invalid role: {payload.role}")
    if payload.role == "tech" and not payload.team_id:
        raise HTTPException(400, "a team is required for tech-role accounts")
    if payload.team_id is not None and not db.get(models.Team, payload.team_id):
        raise HTTPException(404, "team not found")
    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(400, "a user with this email already exists")

    password = payload.password or _generate_temp_password()
    user = models.User(
        name=payload.name, email=payload.email, role=payload.role,
        team_id=payload.team_id, password_hash=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, password


def update_user(db: Session, user: models.User, payload: schemas.UserUpdate) -> models.User:
    data = payload.model_dump(exclude_unset=True)

    if "role" in data and data["role"] not in schemas.ROLES:
        raise HTTPException(400, f"invalid role: {data['role']}")
    if "team_id" in data and data["team_id"] is not None and not db.get(models.Team, data["team_id"]):
        raise HTTPException(404, "team not found")

    effective_role = data.get("role", user.role)
    effective_team_id = data.get("team_id", user.team_id)
    if effective_role == "tech" and not effective_team_id:
        raise HTTPException(400, "a team is required for tech-role accounts")

    for field, value in data.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


def reset_password(db: Session, user: models.User) -> str:
    password = _generate_temp_password()
    user.password_hash = hash_password(password)
    db.commit()
    return password
