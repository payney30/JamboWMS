"""widen priority CHECK constraint for urgency relabel

Enhancement backlog Phase 14 (NJ2026_Work_Order_System_PRD.md §13#15):
relabels the 5 priority tiers (Highest/High/Medium/Low/Lowest ->
Immediate/Same Day/Next Day/2 Days/3 Days) for NEW work orders going
forward. Per an explicit decision, historic data is NOT rewritten — old
work orders keep their old priority strings forever. That means the DB
CHECK constraint has to accept the UNION of both old and new values
(10 total), not a swap of 5-for-5: a CHECK constraint applies uniformly
to every row, and narrowing it to only the new 5 would make this
migration itself fail immediately against every existing row that still
has an old value.

Revision ID: d3f8a2c1e5b7
Revises: c8e2f4a1b6d3
Create Date: 2026-07-30 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f8a2c1e5b7'
down_revision: Union[str, Sequence[str], None] = 'c8e2f4a1b6d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_VALUES = ("Highest", "High", "Medium", "Low", "Lowest")
NEW_VALUES = ("Immediate", "Same Day", "Next Day", "2 Days", "3 Days")
ALL_VALUES = OLD_VALUES + NEW_VALUES


def upgrade() -> None:
    """Upgrade schema."""
    values_sql = ",".join(f"'{v}'" for v in ALL_VALUES)
    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.drop_constraint("ck_wo_priority", type_="check")
        batch_op.create_check_constraint(
            "ck_wo_priority", f"priority IN ({values_sql})"
        )


def downgrade() -> None:
    """Downgrade schema."""
    values_sql = ",".join(f"'{v}'" for v in OLD_VALUES)
    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.drop_constraint("ck_wo_priority", type_="check")
        batch_op.create_check_constraint(
            "ck_wo_priority", f"priority IN ({values_sql})"
        )
