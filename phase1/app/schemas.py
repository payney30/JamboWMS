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


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    role: str


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
    changed_at: dt.datetime


class WorkOrderCreate(BaseModel):
    requester_name: str
    requester_email: Optional[str] = None
    requester_phone: Optional[str] = None
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


class AssignRequest(BaseModel):
    team_id: int
    person_id: Optional[int] = None
    note: Optional[str] = None  # required if this is a reassignment reroute; see router


class StatusChangeRequest(BaseModel):
    status: str
    note: Optional[str] = None


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


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file_url: str
    uploaded_at: dt.datetime


class WorkOrderDetail(WorkOrderListItem):
    requester_name: str
    requester_email: Optional[str]
    requester_phone: Optional[str]
    notify_preference: Optional[str] = None
    work_type: str
    assigned_person: Optional[UserOut] = None
    updated_at: dt.datetime
    closed_at: Optional[dt.datetime]
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
    """What a requester is allowed to see when looking up their own WO —
    no internal notes, no assignment details, no requester PII beyond
    what they already provided to look it up."""
    model_config = ConfigDict(from_attributes=True)
    wo_number: str
    status: str
    priority: str
    work_type: str
    created_at: dt.datetime
    closed_at: Optional[dt.datetime]


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
