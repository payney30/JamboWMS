"""add program_viewer and basecamp_viewer roles

Enhancement backlog Phase 26 (NJ2026_Work_Order_System_PRD.md §17
follow-up, 8/2/26): audience-scoped, read-only dashboard roles.
Program HQ and Contingent Ops HQ are separate teams and need
separately-assignable access — a "program_viewer" can only ever log
into the Program HQ dashboard, a "basecamp_viewer" only the Base Camp
Ops one. Admin-managed (like loc/tech/leadership), not delegated like
task_worker was.

Revision ID: c4d6e8f0a2b4
Revises: b3c5d7e9f1a2
Create Date: 2026-08-02 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d6e8f0a2b4'
down_revision: Union[str, Sequence[str], None] = 'b3c5d7e9f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_user_role", type_="check")
        batch_op.create_check_constraint(
            "ck_user_role",
            "role IN ('loc','tech','leadership','admin','task_worker','program_viewer','basecamp_viewer')",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_user_role", type_="check")
        batch_op.create_check_constraint(
            "ck_user_role",
            "role IN ('loc','tech','leadership','admin','task_worker')",
        )
