import datetime as dt
from typing import Optional, List
from pydantic import BaseModel, ConfigDict

PRIORITIES = ("Highest", "High", "Medium", "Low", "Lowest")
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
    created_at: dt.datetime


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
    changed_at: dt.datetime


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
    locked_at: Optional[dt.datetime] = None


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
    created_at: dt.datetime
    # Enhancement backlog Phase 1 (PRD §14#1) — read from
    # WorkOrder.locked_by, already None if the lock has gone stale.
    # Populates the inbox lock icon + hover tooltip.
    locked_by: Optional[UserOut] = None
    locked_at: Optional[dt.datetime] = None
    # Enhancement backlog Phase 2 (PRD §14#9) — populates the inbox notes
    # icon + hover preview when a "Note to Requestor" has been set.
    note_to_requester: Optional[str] = None


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file_url: str
    uploaded_at: dt.datetime


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
    updated_at: dt.datetime
    closed_at: Optional[dt.datetime]
    # Enhancement backlog Phase 1 (PRD §14#5).
    note_to_requester: Optional[str] = None
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
    — no internal notes, no assignment details, no requester PII beyond
    what they already provided to look it up. Enhancement backlog Phase 1
    (PRD §13#4): lookup is now anchored on phone number and can return
    more than one WO, so this also carries a short description/location
    snippet so multiple results are distinguishable — and
    note_to_requester (PRD §14#5), the one requester-facing field LOC can
    set."""
    model_config = ConfigDict(from_attributes=True)
    wo_number: str
    status: str
    priority: str
    work_type: str
    description: str
    created_at: dt.datetime
    closed_at: Optional[dt.datetime]
    note_to_requester: Optional[str] = None


class KPIOut(BaseModel):
    total: int
    open: int
    closed: int
    highest_high_open: int
    completion_rate: float
    opened_today: int
    closed_today: int


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
    created_at: dt.datetime


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
    changed_at: dt.datetime
