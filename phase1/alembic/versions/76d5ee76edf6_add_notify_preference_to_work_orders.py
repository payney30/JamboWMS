"""add notify_preference to work_orders

Revision ID: 76d5ee76edf6
Revises: 2a48f356bf98
Create Date: 2026-07-25 14:52:01.516548

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '76d5ee76edf6'
down_revision: Union[str, Sequence[str], None] = '2a48f356bf98'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # batch_alter_table so this works on SQLite too — SQLite can't ALTER a
    # table to add a CHECK constraint directly; batch mode recreates the
    # table under the hood to do it. On Postgres this just runs normal
    # ALTER TABLE statements.
    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.add_column(sa.Column("notify_preference", sa.String(), nullable=True))
        batch_op.create_check_constraint(
            "ck_wo_notify_preference",
            "notify_preference IS NULL OR notify_preference IN ('email','text','both')",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.drop_constraint("ck_wo_notify_preference", type_="check")
        batch_op.drop_column("notify_preference")
