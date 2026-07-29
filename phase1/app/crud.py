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
from sqlalchemy import func, case, cast, Integer
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
    """Enhancement backlog Phase 4 (PRD §14#13): bare, unpadded WO
    numbers — no "WO-" prefix. Each number is already unique on its own;
    the prefix added nothing but made search/sort harder (see §14#16,
    the numeric-sort fix that depends on this). Since the system isn't
    live yet, there's no in-flight "WO-xxxxx" numbers to migrate.

    Bug fix (PRD §14#19), found while making the above change: this used
    to derive the next number from the most-recent row's raw database
    `id` (`last.id + 1`), which is a different auto-increment sequence
    than the intended "starts at 10001" WO numbering — it only happens to
    equal 10001 for the very first WO (the `else 10001` branch), then
    permanently diverges from the second WO onward, since `id` starts
    counting from 1 like any other table's primary key. The result: the
    second work order ever created would get wo_number "2", not "10002".
    Fixed by deriving the next number from the highest wo_number actually
    issued so far (cast to int), not from the row id.
    """
    highest = db.query(func.max(cast(models.WorkOrder.wo_number, Integer))).scalar()
    next_id = (highest + 1) if highest else 10001
    return str(max(next_id, 10001))


def _validate_work_type(db: Session, work_type: str):
    """Request types are admin-editable (PRD 4.5c) — this is now the only
    validation for work_type on any creation/update path, LOC-manual-entry
    included, since the old DB CheckConstraint was dropped in favor of
    this table. Blank ('Other/not sure') stays valid without a
    request_types row, matching existing behavior."""
    if work_type == "":
        return
    exists = db.query(models.RequestType.id).filter(
        models.RequestType.name == work_type, models.RequestType.is_active == True  # noqa: E712
    ).first()
    if not exists:
        raise HTTPException(400, f"invalid or inactive request type: {work_type}")


def create_work_order(db: Session, payload: schemas.WorkOrderCreate) -> models.WorkOrder:
    asset = db.get(models.Asset, payload.asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    _validate_work_type(db, payload.work_type)

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
                              changed_by: int | None, commit: bool = True) -> models.WorkOrder:
    """Non-status/team fields (description, work_type, location, the
    requester-facing note). Priority changes DO get a history row since
    Section 6 of the PRD calls priority changes out as reportable.

    commit=False lets save_work_order (enhancement backlog Phase 1, PRD
    §14#2) apply this alongside a status/assign/note change in one
    transaction instead of its own — everything else keeps calling this
    with the default commit=True and is unaffected.
    """
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
        _validate_work_type(db, payload.work_type)
        wo.work_type = payload.work_type
    if payload.asset_id is not None:
        asset = db.get(models.Asset, payload.asset_id)
        if not asset:
            raise HTTPException(404, "asset not found")
        wo.asset_id = payload.asset_id
    if payload.note_to_requester is not None:
        wo.note_to_requester = payload.note_to_requester or None

    if commit:
        db.commit()
        db.refresh(wo)
    return wo


def assign_work_order(db: Session, wo: models.WorkOrder, payload: schemas.AssignRequest,
                       changed_by: int | None, commit: bool = True) -> models.WorkOrder:
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

    if commit:
        db.commit()
        db.refresh(wo)
    return wo


def change_status(db: Session, wo: models.WorkOrder, payload: schemas.StatusChangeRequest,
                   changed_by: int | None, commit: bool = True) -> models.WorkOrder:
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

    if commit:
        db.commit()
        db.refresh(wo)
    return wo


def add_note(db: Session, wo: models.WorkOrder, payload: schemas.NoteCreate,
              author_id: int | None, commit: bool = True) -> models.WONote:
    note = models.WONote(
        work_order_id=wo.id, author_id=author_id,
        note_text=payload.note_text, note_type=payload.note_type,
    )
    db.add(note)
    if commit:
        db.commit()
        db.refresh(note)
    return note


# ---- Enhancement backlog Phase 1: WO locking (PRD §14#1) ----

def acquire_lock(db: Session, wo: models.WorkOrder, user: models.User) -> models.WorkOrder:
    """Called when a user opens a WO in the triage drawer. Succeeds
    no-op if they already hold the lock (re-opening the same WO, or a
    heartbeat/refresh). Raises 409 if someone else actively holds it —
    the router surfaces who and since when so the frontend can fall back
    to a read-only view instead of erroring out."""
    holder = wo.locked_by
    if holder and holder.id != user.id:
        raise HTTPException(
            409,
            f"This work order is currently being edited by {holder.name}.",
        )
    wo.locked_by_id = user.id
    wo.locked_at = models.now()
    db.commit()
    db.refresh(wo)
    return wo


def release_lock(db: Session, wo: models.WorkOrder, user: models.User,
                  force: bool = False) -> models.WorkOrder:
    """Called on save, on explicit close/cancel of the drawer, or by an
    admin force-clearing a stuck lock (force=True). Releasing a lock you
    don't hold (and aren't forcing) is a 403, not a silent no-op — that
    would let anyone boot anyone else out of an edit."""
    if wo.locked_by_id and wo.locked_by_id != user.id and not force:
        raise HTTPException(403, "you don't hold the lock on this work order")
    wo.locked_by_id = None
    wo.locked_at = None
    db.commit()
    db.refresh(wo)
    return wo


# ---- Enhancement backlog Phase 1: combined save (PRD §14#2) ----

def save_work_order(db: Session, wo: models.WorkOrder, payload: schemas.WorkOrderSaveRequest,
                     changed_by: int | None) -> models.WorkOrder:
    """One transaction covering every section of the WO detail drawer —
    details, status, assignment, and a new note — instead of the four
    separate save points the granular endpoints below still expose
    (kept for API back-compat / tests, but the frontend now only calls
    this). Per-field role permission checks happen in the router before
    this is called; this function assumes the caller is authorized for
    everything present in the payload.

    Releases the lock at the end, on the assumption that a save means
    the user is done editing — see app/routers/work_orders.py.
    """
    if any(v is not None for v in (
        payload.description, payload.work_type, payload.priority,
        payload.asset_id, payload.note_to_requester,
    )):
        update_work_order_fields(
            db, wo,
            schemas.WorkOrderUpdate(
                description=payload.description,
                work_type=payload.work_type,
                priority=payload.priority,
                asset_id=payload.asset_id,
                note_to_requester=payload.note_to_requester,
            ),
            changed_by=changed_by, commit=False,
        )

    if payload.team_id is not None:
        assign_work_order(
            db, wo,
            schemas.AssignRequest(
                team_id=payload.team_id, person_id=payload.person_id, note=payload.assign_note,
            ),
            changed_by=changed_by, commit=False,
        )

    if payload.status is not None:
        change_status(
            db, wo,
            schemas.StatusChangeRequest(status=payload.status, note=payload.status_note),
            changed_by=changed_by, commit=False,
        )

    if payload.new_note_text:
        add_note(
            db, wo,
            schemas.NoteCreate(note_text=payload.new_note_text, note_type=payload.new_note_type),
            author_id=changed_by, commit=False,
        )

    # Save = done editing: release the lock in the same transaction as
    # the changes it protected, so a rollback (e.g. the atomicity rule at
    # the top of this file) can't leave the lock cleared but the edit lost.
    wo.locked_by_id = None
    wo.locked_at = None

    db.commit()
    db.refresh(wo)
    return wo


# ---- Enhancement backlog Phase 1: phone-anchored public lookup (PRD §13#4) ----

def _digits_only(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def lookup_work_orders_by_phone(db: Session, phone_digits: str, search: str | None = None) -> list[models.WorkOrder]:
    """Matches on requester_phone OR poc_phone, comparing digits-only so
    formatting differences (dashes/parens/spaces/leading '1') at
    submission vs. lookup don't cause false negatives. Returns newest
    first — most people checking status care about their most recent
    submission.

    Filters in Python rather than SQL (can't normalize digits-only in a
    portable way across SQLite/Postgres without a dialect-specific
    REGEXP_REPLACE). Fine at event scale (low thousands of WOs); if this
    ever needs to scale further, store a precomputed digits-only phone
    column and index it instead.

    Enhancement backlog Phase 2 (PRD §13#5): `search`, if given, further
    narrows to WOs whose description OR location (asset name) contains
    the text (case-insensitive substring) — lets a requester with several
    open WOs on the same phone number find "the shower house one" without
    knowing a WO number. Applied client-side on the already phone-matched
    set (small, per-person list), not as a separate SQL query.
    """
    candidates = (
        db.query(models.WorkOrder)
        .filter(
            (models.WorkOrder.requester_phone.isnot(None)) |
            (models.WorkOrder.poc_phone.isnot(None))
        )
        .order_by(models.WorkOrder.created_at.desc())
        .all()
    )
    matches = [
        wo for wo in candidates
        if _digits_only(wo.requester_phone) == phone_digits
        or _digits_only(wo.poc_phone) == phone_digits
    ]
    if search:
        needle = search.strip().lower()
        matches = [
            wo for wo in matches
            if needle in (wo.description or "").lower()
            or (wo.asset and needle in wo.asset.name.lower())
        ]
    return matches


def list_work_orders(db: Session, status=None, priority=None, team_id=None,
                      work_type=None, location_group=None, search=None,
                      exclude_closed=False, closed_only=False, priority_in=None,
                      opened_today=False, closed_today=False, handled_by=None,
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
    if handled_by:
        # Enhancement backlog Phase 4 (PRD §14#17): "work orders I've
        # handled" — any WO where this user shows up as the actor on a
        # status-history row OR as a note author, i.e. they touched it at
        # some point. Deliberately NOT the same as "currently assigned to
        # me" (assigned_team/assigned_person) — someone can have worked a
        # WO earlier and it's since moved teams, and this should still
        # find it.
        history_ids = db.query(models.WOStatusHistory.work_order_id).filter(
            models.WOStatusHistory.changed_by == handled_by
        )
        note_ids = db.query(models.WONote.work_order_id).filter(
            models.WONote.author_id == handled_by
        )
        q = q.filter(models.WorkOrder.id.in_(history_ids.union(note_ids)))
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


# ============================================================
# Admin configuration (PRD 4.5)
# ============================================================

def _log_asset_change(db: Session, asset_id: int, field: str, from_value, to_value,
                       changed_by: int | None):
    db.add(models.AssetChangeLog(
        asset_id=asset_id, field_changed=field,
        from_value=None if from_value is None else str(from_value),
        to_value=None if to_value is None else str(to_value),
        changed_by=changed_by,
    ))


def recompute_effective_groups(db: Session):
    """Recompute assets.location_group (the cached, live-read display
    value every existing query already uses) from the reporting_group_id
    inheritance chain: a node's effective group is its own explicit
    override if set, otherwise its parent's effective group.

    Full-table pass rather than a targeted subtree cascade — the asset
    count at this scale (low hundreds) makes a full recompute cheap, and
    it's much harder to get wrong than partial-cascade logic tracking
    "which descendants don't have their own override." Called after any
    admin write that could change the result: reparenting, creating a
    node, or changing a reporting_group_id assignment.
    """
    rows = db.query(models.Asset).order_by(models.Asset.id).all()
    by_id = {a.id: a for a in rows}
    group_names = {
        g.id: g.name for g in db.query(models.ReportingGroup).all()
    }
    resolved: dict[int, str] = {}

    def resolve(asset_id: int, _seen: set | None = None) -> str:
        if asset_id in resolved:
            return resolved[asset_id]
        _seen = _seen or set()
        if asset_id in _seen:
            # A cycle slipped through (shouldn't happen — reparenting is
            # cycle-checked — but fail safe rather than infinite-loop).
            return "Unset"
        _seen.add(asset_id)
        a = by_id[asset_id]
        if a.reporting_group_id and a.reporting_group_id in group_names:
            value = group_names[a.reporting_group_id]
        elif a.parent_id and a.parent_id in by_id:
            value = resolve(a.parent_id, _seen)
        else:
            value = a.location_group or "Unset"  # top-level node with no override: keep current value
        resolved[asset_id] = value
        return value

    for a in rows:
        new_value = resolve(a.id)
        if a.location_group != new_value:
            a.location_group = new_value
    db.commit()


def _asset_descendant_ids(db: Session, asset_id: int) -> set[int]:
    """All descendant ids of asset_id (not including itself), used for
    both the reparent cycle-guard and cascade-deactivate."""
    children_by_parent: dict[int, list[int]] = {}
    for aid, pid in db.query(models.Asset.id, models.Asset.parent_id).all():
        if pid is not None:
            children_by_parent.setdefault(pid, []).append(aid)
    out: set[int] = set()
    stack = list(children_by_parent.get(asset_id, []))
    while stack:
        nid = stack.pop()
        if nid in out:
            continue
        out.add(nid)
        stack.extend(children_by_parent.get(nid, []))
    return out


def list_assets_admin(db: Session, include_inactive: bool = True) -> list[dict]:
    """Flat, depth-annotated listing for the admin hierarchy screen (PRD
    4.5a) — includes inactive nodes by default (admin needs to see/
    restore them, unlike every picker-facing use of build_location_tree).
    """
    q = db.query(models.Asset)
    if not include_inactive:
        q = q.filter(models.Asset.is_active == True)  # noqa: E712
    rows = q.order_by(models.Asset.sort_order, models.Asset.name).all()
    by_id = {a.id: a for a in rows}
    children_by_parent: dict[int | None, list] = {}
    for a in rows:
        parent_key = a.parent_id if (a.parent_id in by_id or a.parent_id is None) else None
        children_by_parent.setdefault(parent_key, []).append(a)

    out = []

    def walk(a, depth):
        out.append({
            "id": a.id,
            "name": a.name,
            "parent_id": a.parent_id,
            "parent_name": by_id[a.parent_id].name if a.parent_id in by_id else None,
            "depth": depth,
            "code": a.code,
            "sort_order": a.sort_order,
            "is_active": a.is_active,
            "camp_letter": a.camp_letter,
            "reporting_group_id": a.reporting_group_id,
            "reporting_group_name": a.reporting_group.name if a.reporting_group else None,
            "effective_reporting_group": a.location_group,
        })
        for c in children_by_parent.get(a.id, []):
            walk(c, depth + 1)

    for root in children_by_parent.get(None, []):
        walk(root, 0)
    return out


def create_asset(db: Session, payload: schemas.AssetCreate, changed_by: int | None) -> models.Asset:
    if payload.parent_id is not None and not db.get(models.Asset, payload.parent_id):
        raise HTTPException(404, "parent location not found")
    if payload.reporting_group_id is not None and not db.get(models.ReportingGroup, payload.reporting_group_id):
        raise HTTPException(404, "reporting group not found")
    if db.query(models.Asset).filter(models.Asset.name == payload.name).first():
        raise HTTPException(400, "a location with this name already exists")

    asset = models.Asset(
        name=payload.name,
        parent_id=payload.parent_id,
        code=payload.code,
        sort_order=payload.sort_order,
        reporting_group_id=payload.reporting_group_id,
        location_group="Unset",  # placeholder — recompute_effective_groups fills this in below
        is_active=True,
    )
    db.add(asset)
    db.flush()
    _log_asset_change(db, asset.id, "created", None, payload.name, changed_by)
    db.commit()
    recompute_effective_groups(db)
    db.refresh(asset)
    return asset


def update_asset(db: Session, asset: models.Asset, payload: schemas.AssetUpdate,
                  changed_by: int | None) -> models.Asset:
    data = payload.model_dump(exclude_unset=True, exclude={"cascade_deactivate"})

    if "name" in data and data["name"] != asset.name:
        dupe = db.query(models.Asset).filter(
            models.Asset.name == data["name"], models.Asset.id != asset.id
        ).first()
        if dupe:
            raise HTTPException(400, "a location with this name already exists")

    if "parent_id" in data and data["parent_id"] != asset.parent_id:
        new_parent_id = data["parent_id"]
        if new_parent_id is not None:
            if new_parent_id == asset.id:
                raise HTTPException(400, "a location can't be its own parent")
            if not db.get(models.Asset, new_parent_id):
                raise HTTPException(404, "parent location not found")
            if new_parent_id in _asset_descendant_ids(db, asset.id):
                raise HTTPException(400, "can't move a location under its own descendant")

    if "reporting_group_id" in data and data["reporting_group_id"] is not None:
        if not db.get(models.ReportingGroup, data["reporting_group_id"]):
            raise HTTPException(404, "reporting group not found")

    # Deactivation with active children: warn (409) rather than silently
    # cascading or blocking outright, per PRD 4.5's common design rules —
    # unless the caller explicitly opted into cascading.
    if data.get("is_active") is False and asset.is_active:
        active_children = [
            c for c in db.query(models.Asset).filter(models.Asset.parent_id == asset.id) if c.is_active
        ]
        if active_children and not payload.cascade_deactivate:
            raise HTTPException(
                409,
                "this location has active child locations: "
                + ", ".join(c.name for c in active_children)
                + ". Pass cascade_deactivate=true to deactivate them too, or reassign them first.",
            )

    for field in ("name", "parent_id", "code", "sort_order", "is_active", "reporting_group_id"):
        if field in data and data[field] != getattr(asset, field):
            _log_asset_change(db, asset.id, field, getattr(asset, field), data[field], changed_by)
            setattr(asset, field, data[field])

    if data.get("is_active") is False and payload.cascade_deactivate:
        for descendant_id in _asset_descendant_ids(db, asset.id):
            child = by_id_or_get(db, descendant_id)
            if child.is_active:
                _log_asset_change(db, child.id, "is_active", True, False, changed_by)
                child.is_active = False

    db.commit()
    recompute_effective_groups(db)
    db.refresh(asset)
    return asset


def by_id_or_get(db: Session, asset_id: int) -> models.Asset:
    return db.get(models.Asset, asset_id)


def get_asset_change_log(db: Session, asset_id: int) -> list[models.AssetChangeLog]:
    return (
        db.query(models.AssetChangeLog)
        .filter(models.AssetChangeLog.asset_id == asset_id)
        .order_by(models.AssetChangeLog.changed_at.desc())
        .all()
    )


def list_reporting_groups(db: Session, include_inactive: bool = True) -> list[models.ReportingGroup]:
    q = db.query(models.ReportingGroup)
    if not include_inactive:
        q = q.filter(models.ReportingGroup.is_active == True)  # noqa: E712
    return q.order_by(models.ReportingGroup.sort_order, models.ReportingGroup.name).all()


def create_reporting_group(db: Session, payload: schemas.ReportingGroupCreate) -> models.ReportingGroup:
    if db.query(models.ReportingGroup).filter(models.ReportingGroup.name == payload.name).first():
        raise HTTPException(400, "a reporting group with this name already exists")
    rg = models.ReportingGroup(name=payload.name, sort_order=payload.sort_order)
    db.add(rg)
    db.commit()
    db.refresh(rg)
    return rg


def update_reporting_group(db: Session, rg: models.ReportingGroup,
                            payload: schemas.ReportingGroupUpdate) -> models.ReportingGroup:
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != rg.name:
        if db.query(models.ReportingGroup).filter(
            models.ReportingGroup.name == data["name"], models.ReportingGroup.id != rg.id
        ).first():
            raise HTTPException(400, "a reporting group with this name already exists")
    for field, value in data.items():
        setattr(rg, field, value)
    db.commit()
    # A rename changes the display string every assigned/inheriting asset
    # shows — recompute so location_group reflects the new name immediately
    # rather than waiting for an unrelated hierarchy edit to trigger it.
    recompute_effective_groups(db)
    db.refresh(rg)
    return rg


def list_request_types(db: Session, include_inactive: bool = True) -> list[models.RequestType]:
    q = db.query(models.RequestType)
    if not include_inactive:
        q = q.filter(models.RequestType.is_active == True)  # noqa: E712
    return q.order_by(models.RequestType.sort_order, models.RequestType.name).all()


def create_request_type(db: Session, payload: schemas.RequestTypeCreate) -> models.RequestType:
    if db.query(models.RequestType).filter(models.RequestType.name == payload.name).first():
        raise HTTPException(400, "a request type with this name already exists")
    rt = models.RequestType(name=payload.name, sort_order=payload.sort_order)
    db.add(rt)
    db.commit()
    db.refresh(rt)
    return rt


def update_request_type(db: Session, rt: models.RequestType,
                         payload: schemas.RequestTypeUpdate) -> models.RequestType:
    """Renaming in place is allowed (matches PRD 4.5c's guidance for
    simple typo fixes) — existing work_orders.work_type strings are NOT
    retroactively updated, since that column isn't a FK; a rename here
    only changes what the picker offers going forward. A real
    reclassification should be a deactivate + add-new instead, left to
    the admin's judgment rather than enforced here."""
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != rt.name:
        if db.query(models.RequestType).filter(
            models.RequestType.name == data["name"], models.RequestType.id != rt.id
        ).first():
            raise HTTPException(400, "a request type with this name already exists")
    for field, value in data.items():
        setattr(rt, field, value)
    db.commit()
    db.refresh(rt)
    return rt


def list_teams_admin(db: Session, include_inactive: bool = True) -> list[models.Team]:
    q = db.query(models.Team)
    if not include_inactive:
        q = q.filter(models.Team.is_active == True)  # noqa: E712
    return q.order_by(models.Team.name).all()


def create_team(db: Session, payload: schemas.TeamCreate) -> models.Team:
    if db.query(models.Team).filter(models.Team.name == payload.name).first():
        raise HTTPException(400, "a team with this name already exists")
    team = models.Team(name=payload.name)
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def update_team(db: Session, team: models.Team, payload: schemas.TeamUpdate) -> models.Team:
    data = payload.model_dump(exclude_unset=True, exclude={"confirm_deactivate"})
    if "name" in data and data["name"] != team.name:
        if db.query(models.Team).filter(
            models.Team.name == data["name"], models.Team.id != team.id
        ).first():
            raise HTTPException(400, "a team with this name already exists")

    if data.get("is_active") is False and team.is_active:
        open_wo_count = db.query(models.WorkOrder.id).filter(
            models.WorkOrder.assigned_team_id == team.id,
            ~models.WorkOrder.status.like("Closed%"),
        ).count()
        active_user_count = db.query(models.User.id).filter(
            models.User.team_id == team.id, models.User.is_active == True  # noqa: E712
        ).count()
        if (open_wo_count or active_user_count) and not payload.confirm_deactivate:
            raise HTTPException(
                409,
                f"this team still has {open_wo_count} open work order(s) and "
                f"{active_user_count} active user(s) assigned. Resend with "
                "confirm_deactivate=true to deactivate anyway — they'll stay pointed "
                "at this team until manually reassigned.",
            )

    for field, value in data.items():
        setattr(team, field, value)
    db.commit()
    db.refresh(team)
    return team


# ---- Enhancement backlog Phase 4: admin-configurable settings (PRD §15#1) ----

# The event's physical location (Summit Bechtel Reserve, WV) is Eastern
# time — a sensible operational default until an admin sets it
# explicitly, rather than defaulting to UTC and being wrong for everyone
# out of the gate.
DEFAULT_TIMEZONE = "America/New_York"


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(models.AppSetting, key)
    return row.value if row else default


def get_all_settings(db: Session) -> dict:
    """Public-readable snapshot of settings — used by every screen
    (including the unauthenticated Submit WO form) to know which time
    zone to format dates in. Deliberately just the display-affecting
    settings, nothing sensitive."""
    return {
        "timezone": get_setting(db, "timezone", DEFAULT_TIMEZONE),
    }


def set_setting(db: Session, key: str, value: str, updated_by: int | None) -> models.AppSetting:
    row = db.get(models.AppSetting, key)
    if row:
        row.value = value
        row.updated_by = updated_by
    else:
        row = models.AppSetting(key=key, value=value, updated_by=updated_by)
        db.add(row)
    db.commit()
    db.refresh(row)
    return row
