import datetime as dt
from typing import Optional, List, Annotated
from pydantic import BaseModel, ConfigDict, PlainSerializer

# Bug fix (PRD §14#36): every datetime stored in this app is naive UTC
# (see models.now(), which returns dt.datetime.utcnow() with no tzinfo
# attached). Pydantic's default serialization of a naive datetime just
# calls .isoformat() on it, which produces a string with NO timezone
# marker at all — e.g. "2026-07-29T20:48:00.123456", not
# "...+00:00" or "...Z". Per the JS spec, `new Date(...)` on a
# date-time string with no timezone marker parses it as the BROWSER'S
# OWN LOCAL time, not UTC. That silently shifted every date comparison
# and every displayed timestamp in the frontend by the browser's UTC
# offset — e.g. a WO created at 20:48 UTC (16:48 Eastern) displayed as
# "20:48" even after the admin-configurable-timezone feature (§15#1)
# was built, because that feature reformats whatever `new Date(iso)`
# already (mis)parsed, and a naive string never gave it a real UTC
# instant to start from in the first place.
#
# This was actually caught early — see tests/test_timestamp_serialization.py,
# which was written specifically to pin this bug and already assumed a
# `UTCDateTime` type would exist — but the actual schema fix was never
# applied; the tests were left failing rather than acted on. Confirmed
# in production (7/29/26): displayed timestamps were exactly the local
# UTC offset ahead of actual local time, matching this exact failure
# mode.
#
# Fixed here, once, as a reusable annotated type — every API-facing
# schema field below uses UTCDateTime instead of a bare dt.datetime, so
# the JSON that goes out always carries an explicit UTC marker
# (isoformat() on a tz-aware datetime appends "+00:00" automatically).
# Internal representation is untouched (still naive dt.datetime.utcnow()
# everywhere in crud.py/models.py) — this only affects what's
# serialized on the way out, so none of the many naive-datetime
# comparisons elsewhere in the codebase needed to change.
def _serialize_utc(v: dt.datetime) -> str:
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.timezone.utc)
    return v.isoformat()


UTCDateTime = Annotated[dt.datetime, PlainSerializer(_serialize_utc, return_type=str)]

# Enhancement backlog Phase 14 (PRD §13#15): urgency-tier rename. This
# tuple governs what NEW work orders (any creation/priority-edit path)
# are allowed to be assigned — old names ("Highest"/"High"/"Medium"/
# "Low"/"Lowest") are deliberately excluded here going forward. Historic
# data was NOT rewritten (explicit decision, 7/29/26), so old values
# still exist and remain readable/valid in the database — the DB CHECK
# constraint (see models.py) accepts the union of both old and new for
# exactly that reason, and models.SLA_HOURS/crud._PRIORITY_RANK/
# crud.URGENT_PRIORITIES all still recognize old values too, so already-
# existing WOs keep working correctly (SLA math, sorting, the urgent-
# open KPI) for the rest of their lifecycle. This tuple is the one place
# that's intentionally narrower than the DB: it's what stops someone
# from newly assigning an old-style value going forward.
PRIORITIES = ("Immediate", "Same Day", "Next Day", "2 Days", "3 Days")

# The old names, kept around explicitly for contexts that deal with
# historic/legacy data on purpose — right now just
# backfill_fiix_history.py, which imports real historical Fiix records
# that were always written with these names and always will be (it's a
# one-time import of a fixed, already-recorded dataset, not an ongoing
# input). Deliberately a separate tuple from PRIORITIES, not a superset
# of it, so it stays obvious at each call site which meaning ("what a
# NEW work order may be assigned" vs. "what historical data legitimately
# contains") is intended — mixing them into one tuple would make it easy
# to accidentally let old names back in somewhere that should only ever
# see new ones going forward.
LEGACY_PRIORITIES = ("Highest", "High", "Medium", "Low", "Lowest")
STATUSES = (
    "Requested", "Assigned", "Work In Progress", "On Hold",
    "Closed, Completed", "Closed, Incomplete",
)
WORK_TYPES = ("NJ IT", "NJ Items/Parts", "NJ Maintenance", "NJ Transportation", "")
NOTIFY_PREFERENCES = ("email", "text", "both")
ROLES = ("loc", "tech", "leadership", "admin")
# Techs work a request-to-close queue, not the LOC's triage states — they
# can't move a WO back to "Requested" or hand-set "Assigned". Lives here
# (not just app/routers/work_orders.py) so both the granular /status
# endpoint and the combined /save endpoint (enhancement backlog Phase 1,
# PRD §14#2) enforce the exact same rule from one place.
TECH_ALLOWED_STATUSES = {
    "Work In Progress", "On Hold", "Closed, Completed", "Closed, Incomplete",
}


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    location_group: str
    camp_letter: Optional[str] = None


class LocationNode(BaseModel):
    """A node in the nested location hierarchy (PRD 4.2a). Built by
    crud.build_location_tree, not read directly off the ORM model, so this
    doesn't use from_attributes — it's constructed from plain dicts."""
    id: int
    name: str
    code: Optional[str] = None
    branch_label: str
    is_active: bool = True
    children: List["LocationNode"] = []


LocationNode.model_rebuild()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    role: str
    team: Optional[TeamOut] = None


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    note_text: str
    note_type: str
    author: Optional[UserOut] = None
    created_at: UTCDateTime


class NoteCreate(BaseModel):
    note_text: str
    note_type: str  # internal | instruction | work_note


class HistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: str
    from_value: Optional[str]
    to_value: str
    changed_by: Optional[int]
    # Enhancement backlog Phase 1 (PRD §14#4) — resolved from
    # WOStatusHistory.changed_by_name; None for system-generated rows
    # (e.g. the initial "Requested" row, which has no changed_by).
    changed_by_name: Optional[str] = None
    changed_at: UTCDateTime


class WorkOrderCreate(BaseModel):
    requester_name: str
    requester_email: Optional[str] = None
    requester_phone: Optional[str] = None
    poc_is_requester: bool = True
    poc_name: Optional[str] = None  # required by the router when poc_is_requester is False
    poc_phone: Optional[str] = None  # required by the router when poc_is_requester is False
    asset_id: int
    work_type: str = ""
    description: str
    priority: str
    notify_preference: Optional[str] = None  # 'email' | 'text' | 'both' — see NOTIFY_PREFERENCES
    external_ref: Optional[str] = None  # original Fiix ticket number — backfill only
    # Enhancement backlog Phase 15 (PRD §13#14): optional geo pin-drop,
    # set from the Submit WO screen only — no editing surface elsewhere
    # in this pass (LOC triage displays it, doesn't adjust it).
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class WorkOrderUpdate(BaseModel):
    description: Optional[str] = None
    work_type: Optional[str] = None
    priority: Optional[str] = None
    asset_id: Optional[int] = None
    # Enhancement backlog Phase 1 (PRD §14#5).
    note_to_requester: Optional[str] = None


class AssignRequest(BaseModel):
    team_id: int
    person_id: Optional[int] = None
    note: Optional[str] = None  # required if this is a reassignment reroute; see router


class StatusChangeRequest(BaseModel):
    status: str
    note: Optional[str] = None


class WorkOrderSaveRequest(BaseModel):
    """Enhancement backlog Phase 1 (PRD §14#2): one combined payload
    covering every kind of edit the WO detail drawer supports, so the
    frontend has exactly one Save action instead of four. Every field is
    optional — only the sections the user actually touched are included
    and applied; see app/routers/work_orders.py:save_work_order for the
    per-field permission checks and crud.save_work_order for the single
    transaction that applies them.
    """
    # Details — loc/admin only
    description: Optional[str] = None
    work_type: Optional[str] = None
    priority: Optional[str] = None
    asset_id: Optional[int] = None
    note_to_requester: Optional[str] = None
    # Status change
    status: Optional[str] = None
    status_note: Optional[str] = None
    # Assignment
    team_id: Optional[int] = None
    person_id: Optional[int] = None
    assign_note: Optional[str] = None
    # A new note to add, if any
    new_note_text: Optional[str] = None
    new_note_type: str = "internal"  # internal | instruction | work_note


class LockOut(BaseModel):
    """Response for POST /work-orders/{id}/lock and /unlock, and embedded
    implicitly via WorkOrderListItem/WorkOrderDetail's locked_by/locked_at
    fields (enhancement backlog Phase 1, PRD §14#1)."""
    locked: bool
    locked_by: Optional[UserOut] = None
    locked_at: Optional[UTCDateTime] = None


class WorkOrderListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    wo_number: str
    external_ref: Optional[str] = None
    priority: str
    status: str
    description: str
    asset: AssetOut
    assigned_team: Optional[TeamOut] = None
    created_at: UTCDateTime
    # Enhancement backlog Phase 1 (PRD §14#1) — read from
    # WorkOrder.locked_by, already None if the lock has gone stale.
    # Populates the inbox lock icon + hover tooltip.
    locked_by: Optional[UserOut] = None
    locked_at: Optional[UTCDateTime] = None
    # Enhancement backlog Phase 2 (PRD §14#9) — populates the inbox notes
    # icon + hover preview when a "Note to Requestor" has been set.
    note_to_requester: Optional[str] = None
    # Enhancement backlog Phase 5 (PRD §14#10) — read from
    # WorkOrder.sla_warn_at / sla_deadline. Frontend compares against
    # "now" to decide the yellow/red deadline flag; skipped entirely for
    # closed WOs client-side.
    sla_warn_at: Optional[UTCDateTime] = None
    sla_deadline: Optional[UTCDateTime] = None


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file_url: str
    uploaded_at: UTCDateTime


class WorkOrderDetail(WorkOrderListItem):
    requester_name: str
    requester_email: Optional[str]
    requester_phone: Optional[str]
    poc_is_requester: bool = True
    poc_name: Optional[str] = None
    poc_phone: Optional[str] = None
    notify_preference: Optional[str] = None
    work_type: str
    assigned_person: Optional[UserOut] = None
    updated_at: UTCDateTime
    closed_at: Optional[UTCDateTime]
    # Enhancement backlog Phase 1 (PRD §14#5).
    note_to_requester: Optional[str] = None
    # Enhancement backlog Phase 5 (PRD §14#10).
    sla_warn_at: Optional[UTCDateTime] = None
    sla_deadline: Optional[UTCDateTime] = None
    # Enhancement backlog Phase 15 (PRD §13#14): geo pin-drop, if the
    # requester set one at submission. Both null (the common case for
    # WOs submitted before this feature, or where the requester skipped
    # it) means "no pin" — the WO detail screen shows no map at all.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    notes: List[NoteOut] = []
    history: List[HistoryOut] = []
    attachments: List[AttachmentOut] = []


class PublicAssetOut(BaseModel):
    """Deliberately thinner than AssetOut — no camp_letter, no internal
    fields — since this goes to the unauthenticated public form."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    location_group: str


class PublicWorkOrderConfirmation(BaseModel):
    wo_number: str


class PublicWorkOrderStatus(BaseModel):
    """What a requester is allowed to see when looking up their own WO(s)
    — no internal notes, no requester PII beyond what they already
    provided to look it up. Enhancement backlog Phase 1 (PRD §13#4):
    lookup is now anchored on phone number and can return more than one
    WO, so this also carries a short description/location snippet so
    multiple results are distinguishable — and note_to_requester (PRD
    §14#5), the one requester-facing field LOC can set.

    Enhancement backlog Phase 11 (PRD §13#7): assigned_team is now
    included too — internal triage notes are deliberately still NOT
    exposed here (those are LOC's working notes, not written with a
    requester audience in mind); note_to_requester above already covers
    the "give the requester a note" half of that ask.
    """
    model_config = ConfigDict(from_attributes=True)
    wo_number: str
    status: str
    priority: str
    work_type: str
    description: str
    created_at: UTCDateTime
    closed_at: Optional[UTCDateTime]
    note_to_requester: Optional[str] = None
    assigned_team: Optional[TeamOut] = None


class KPIOut(BaseModel):
    total: int
    open: int
    closed: int
    highest_high_open: int
    # Enhancement backlog Phase 11 (PRD §14#25).
    requested: int
    completion_rate: float
    opened_today: int
    closed_today: int
    # Enhancement backlog Phase 5 (PRD §14#10).
    approaching_deadline: int
    past_deadline: int


class BreakdownOut(BaseModel):
    by_status: dict
    by_priority: dict
    by_work_type: dict
    by_location: dict
    by_team: dict


class UserAdminOut(BaseModel):
    """User row as shown on the admin user-management screen — includes
    is_active/created_at, unlike the lean UserOut used for note authorship
    etc. Never includes password_hash."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    role: str
    team: Optional[TeamOut] = None
    is_active: bool
    created_at: UTCDateTime


class UserCreate(BaseModel):
    name: str
    email: str
    role: str  # loc | tech | leadership | admin — see schemas.ROLES
    team_id: Optional[int] = None  # required if role == 'tech'; see crud.create_user
    password: Optional[str] = None  # omit to auto-generate a temporary password


class UserCreateResponse(BaseModel):
    """temporary_password is only ever returned here and from the
    reset-password endpoint — it's never stored in plaintext and this is
    the caller's one chance to see it, same convention as seed.py's
    printed admin password."""
    user: UserAdminOut
    temporary_password: str


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    team_id: Optional[int] = None
    is_active: Optional[bool] = None


class PasswordResetResponse(BaseModel):
    temporary_password: str


# ---- Admin configuration (PRD 4.5) ----

class ReportingGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    sort_order: int
    is_active: bool


class ReportingGroupCreate(BaseModel):
    name: str
    sort_order: int = 0


class ReportingGroupUpdate(BaseModel):
    name: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class RequestTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    sort_order: int
    is_active: bool


class RequestTypeCreate(BaseModel):
    name: str
    sort_order: int = 0


class RequestTypeUpdate(BaseModel):
    name: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class TeamAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    is_active: bool


class TeamCreate(BaseModel):
    name: str


class TeamUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    # If deactivating a team that still has open WOs or active users
    # assigned, the request is rejected with a 409 describing what's
    # still attached unless this is set — same "warn, don't silently
    # block or cascade" rule as AssetUpdate.cascade_deactivate.
    confirm_deactivate: bool = False


class AssetAdminOut(BaseModel):
    """Flat row for the admin location-hierarchy screen — includes
    inactive nodes and the reporting-group inheritance state (own
    explicit override vs. inherited), unlike the lean AssetOut/
    PublicAssetOut used by pickers. Built by crud.list_assets_admin, not
    read directly off the ORM model (needs parent_name/depth for table
    rendering), so this doesn't use from_attributes."""
    id: int
    name: str
    parent_id: Optional[int] = None
    parent_name: Optional[str] = None
    depth: int = 0
    code: Optional[str] = None
    sort_order: int
    is_active: bool
    camp_letter: Optional[str] = None
    reporting_group_id: Optional[int] = None  # this node's own override, if any
    reporting_group_name: Optional[str] = None  # this node's own override's name, if any
    effective_reporting_group: str  # resolved display value (own override, or inherited) — same as location_group


class AssetCreate(BaseModel):
    name: str
    parent_id: Optional[int] = None
    code: Optional[str] = None
    sort_order: int = 0
    reporting_group_id: Optional[int] = None


class AssetUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    code: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    reporting_group_id: Optional[int] = None
    # If deactivating a node that has active children, the request is
    # rejected with a 409 listing them unless this is set — mirrors the
    # "warn, don't silently block or cascade" rule from PRD 4.5.
    cascade_deactivate: bool = False


class AssetChangeLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    field_changed: str
    from_value: Optional[str]
    to_value: Optional[str]
    changed_by: Optional[int]
    changed_at: UTCDateTime


# ---- Enhancement backlog Phase 4: admin-configurable settings (PRD §15#1) ----

class SettingsOut(BaseModel):
    """What every screen — including the unauthenticated Submit WO form
    — reads at load time to know which time zone to display dates in.
    Kept minimal/public-safe on purpose; nothing sensitive belongs here."""
    timezone: str


class SettingsUpdate(BaseModel):
    timezone: str
