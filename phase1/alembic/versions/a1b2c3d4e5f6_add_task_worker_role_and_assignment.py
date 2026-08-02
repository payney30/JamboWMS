"""add task worker role, completion pin/photo stage

Enhancement backlog Phase 21 (NJ2026_Work_Order_System_PRD.md §17#10):
Task Team assignment. Adds:
  - users.pin_hash (nullable) + widens ck_user_role to include
    'task_worker' — a new lightweight role that logs in with a PIN
    instead of email/password (see app/auth.py for the hashing).
  - work_orders.completion_latitude/completion_longitude — the worker's
    own "here's where I actually dropped it" pin, deliberately separate
    from the requester's original submission pin (latitude/longitude,
    §13#14) and always optional.
  - wo_attachments.stage — distinguishes a worker's completion photo
    from the requester's original submission photo(s); defaults to
    'submission' for every existing row (today, every attachment in the
    system is a submission photo — there was no authenticated upload
    path before this).

Deliberately does NOT add a new "assigned worker" column — found during
implementation that WorkOrder.assigned_person_id (and the validation in
crud.assign_work_order that a person must belong to the team they're
assigned into) already exists and does exactly this job. It was simply
never wired to any frontend UI before now.

Revision ID: a1b2c3d4e5f6
Revises: f4a8d1c6e3b2
Create Date: 2026-08-01 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f4a8d1c6e3b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("pin_hash", sa.String(), nullable=True))
        batch_op.drop_constraint("ck_user_role", type_="check")
        batch_op.create_check_constraint(
            "ck_user_role", "role IN ('loc','tech','leadership','admin','task_worker')"
        )

    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.add_column(sa.Column("completion_latitude", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("completion_longitude", sa.Float(), nullable=True))

    with op.batch_alter_table("wo_attachments") as batch_op:
        batch_op.add_column(
            sa.Column("stage", sa.String(), nullable=False, server_default="submission")
        )
        batch_op.create_check_constraint(
            "ck_attachment_stage", "stage IN ('submission','completion')"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("wo_attachments") as batch_op:
        batch_op.drop_constraint("ck_attachment_stage", type_="check")
        batch_op.drop_column("stage")

    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.drop_column("completion_longitude")
        batch_op.drop_column("completion_latitude")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_user_role", type_="check")
        batch_op.create_check_constraint(
            "ck_user_role", "role IN ('loc','tech','leadership','admin')"
        )
        batch_op.drop_column("pin_hash")
