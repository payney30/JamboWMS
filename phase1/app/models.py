"""
SQLAlchemy models — mirrors PHASE1_TECH_SPEC.md Section 1 exactly.
wo_attachments / response_templates are included as stubs for schema
forward-compatibility with Phase 2/3 but have no endpoints yet.
"""
import datetime as dt
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, ForeignKey, TIMESTAMP, CheckConstraint
)
from sqlalchemy.orm import relationship, backref
from .database import Base


def now():
    return dt.datetime.utcnow()


class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, default=True)

    users = relationship("User", back_populates="team")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)  # loc | tech | leadership | admin
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP, default=now)

    team = relationship("Team", back_populates="users")

    __table_args__ = (
        CheckConstraint("role IN ('loc','tech','leadership','admin')", name="ck_user_role"),
    )


class Asset(Base):
    """
    A single node in the location hierarchy (PRD 4.2a / 4.5). Every node —
    branch, camp, subcamp, shower house, or a directly-loggable leaf — is a
    row here, same as before. What's new is that rows can now nest via
    parent_id, so the full tree (not just each leaf's top-level branch) can
    be reconstructed for the hierarchical location picker.

    Soft-delete only: is_active=False removes a location from the picker
    for NEW selections, but the row itself is never deleted, so existing
    work_orders.asset_id references (and historical reporting/dashboards)
    keep working exactly as before. See crud.build_location_tree.
    """
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    location_group = Column(String, nullable=False)
    camp_letter = Column(String, nullable=True)
    parent_id = Column(Integer, ForeignKey("assets.id"), nullable=True)
    code = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)

    children = relationship(
        "Asset", backref=backref("parent", remote_side=[id]),
        order_by="Asset.sort_order",
    )


class WorkOrder(Base):
    __tablename__ = "work_orders"
    id = Column(Integer, primary_key=True)
    wo_number = Column(String, nullable=False, unique=True)
    # Original Fiix ticket number, only set on rows created by
    # backfill_fiix_history.py. Nullable — normal WOs (public form, LOC
    # manual entry) never set this. Kept because the historical
    # descriptions themselves are full of cross-references like "follow up
    # on ticket 9051," so being able to search for the old number matters.
    external_ref = Column(String, nullable=True, index=True)
    requester_name = Column(String, nullable=False)
    requester_email = Column(String, nullable=True)
    requester_phone = Column(String, nullable=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    work_type = Column(String, nullable=False, default="")
    description = Column(Text, nullable=False)
    priority = Column(String, nullable=False)
    status = Column(String, nullable=False, default="Requested")
    assigned_team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    assigned_person_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(TIMESTAMP, default=now)
    updated_at = Column(TIMESTAMP, default=now, onupdate=now)
    closed_at = Column(TIMESTAMP, nullable=True)
    # Captured at submission (public form or LOC manual entry). Nullable —
    # older rows and internal-entry WOs may not have one. Not wired to an
    # actual email/SMS provider yet; see app/routers/public.py and the
    # README's "Known gaps" list.
    notify_preference = Column(String, nullable=True)

    asset = relationship("Asset")
    assigned_team = relationship("Team")
    assigned_person = relationship("User")
    notes = relationship("WONote", back_populates="work_order", cascade="all, delete-orphan")
    history = relationship("WOStatusHistory", back_populates="work_order", cascade="all, delete-orphan")
    attachments = relationship("WOAttachment", cascade="all, delete-orphan", order_by="WOAttachment.uploaded_at")

    __table_args__ = (
        CheckConstraint(
            "work_type IN ('NJ IT','NJ Items/Parts','NJ Maintenance','NJ Transportation','')",
            name="ck_wo_work_type",
        ),
        CheckConstraint(
            "priority IN ('Highest','High','Medium','Low','Lowest')", name="ck_wo_priority"
        ),
        CheckConstraint(
            "status IN ('Requested','Assigned','Work In Progress','On Hold',"
            "'Closed, Completed','Closed, Incomplete')",
            name="ck_wo_status",
        ),
        CheckConstraint(
            "notify_preference IS NULL OR notify_preference IN ('email','text','both')",
            name="ck_wo_notify_preference",
        ),
    )


class WONote(Base):
    __tablename__ = "wo_notes"
    id = Column(Integer, primary_key=True)
    work_order_id = Column(Integer, ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    note_text = Column(Text, nullable=False)
    note_type = Column(String, nullable=False)  # internal | instruction | work_note
    created_at = Column(TIMESTAMP, default=now)

    work_order = relationship("WorkOrder", back_populates="notes")
    author = relationship("User")

    __table_args__ = (
        CheckConstraint(
            "note_type IN ('internal','instruction','work_note')", name="ck_note_type"
        ),
    )


class WOStatusHistory(Base):
    """
    The status-history engine. Every status/team/priority mutation on a
    WorkOrder must produce exactly one row here, in the same transaction
    as the mutation. See crud.py for where this is enforced.
    """
    __tablename__ = "wo_status_history"
    id = Column(Integer, primary_key=True)
    work_order_id = Column(Integer, ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String, nullable=False)  # status_change | reassignment | priority_change
    from_value = Column(String, nullable=True)
    to_value = Column(String, nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    changed_at = Column(TIMESTAMP, default=now)

    work_order = relationship("WorkOrder", back_populates="history")

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('status_change','reassignment','priority_change')",
            name="ck_history_event_type",
        ),
    )


class WOAttachment(Base):
    """Photos attached at submission via the public requester form
    (app/routers/public.py). uploaded_by is null for public submissions
    since there's no authenticated user in that flow."""
    __tablename__ = "wo_attachments"
    id = Column(Integer, primary_key=True)
    work_order_id = Column(Integer, ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    file_url = Column(String, nullable=False)
    uploaded_at = Column(TIMESTAMP, default=now)


class ResponseTemplate(Base):
    """Stub for the suggested/curated response library — no endpoints yet."""
    __tablename__ = "response_templates"
    id = Column(Integer, primary_key=True)
    text = Column(Text, nullable=False)
    category = Column(String, nullable=False)  # triage_note | work_note | closing_resolution
    work_type = Column(String, nullable=True)
    use_count = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="active")  # active | retired
    created_from_wo_id = Column(Integer, ForeignKey("work_orders.id"), nullable=True)
    source = Column(String, nullable=False)  # seeded | usage_promoted | admin_authored
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(TIMESTAMP, default=now)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(TIMESTAMP, default=now, onupdate=now)
