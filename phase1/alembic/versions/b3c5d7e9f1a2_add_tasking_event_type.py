"""widen history event_type to include 'tasking'

Enhancement backlog Phase 22 (NJ2026_Work_Order_System_PRD.md §17#10
follow-up): worker assignment ("tasking" — see the terminology
decision in this phase's PRD entry for why not "assignment") gets its
own distinct status-history event type, separate from team-level
'reassignment'. Previously, assigning a WO to a specific worker wrote a
'reassignment' row showing the team unchanged (from itself to itself),
which didn't actually record what happened.

Revision ID: b3c5d7e9f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-01 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c5d7e9f1a2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("wo_status_history") as batch_op:
        batch_op.drop_constraint("ck_history_event_type", type_="check")
        batch_op.create_check_constraint(
            "ck_history_event_type",
            "event_type IN ('status_change','reassignment','priority_change','tasking')",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("wo_status_history") as batch_op:
        batch_op.drop_constraint("ck_history_event_type", type_="check")
        batch_op.create_check_constraint(
            "ck_history_event_type",
            "event_type IN ('status_change','reassignment','priority_change')",
        )
