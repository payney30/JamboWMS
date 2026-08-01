"""convert all priority data to new urgency names, narrow constraint back

Enhancement backlog Phase 18 (NJ2026_Work_Order_System_PRD.md §13#15
follow-up): the original urgency-tier rename (migration d3f8a2c1e5b7)
deliberately did NOT rewrite historic data — old-named WOs were assumed
to be real operational history worth preserving as-is. That assumption
turned out to be wrong: all 2026 data in this system (including the
Fiix backfill import) is test data used to validate the system, not
real history. Per explicit direction (7/30/26), this migration:

1. Converts every existing `work_orders.priority` value from old to new
   (1:1 mapping, same as the original rename).
2. Converts every `wo_status_history` row's `from_value`/`to_value` for
   `priority_change` events the same way, so the audit trail doesn't
   show a jarring mix of old/new names for the same underlying data.
3. Narrows `ck_wo_priority` back down to the 5 new names only — safe
   now, since no row will have an old value left by the time this
   constraint change runs (each step here happens in order, and the
   constraint step is last).

The `backfill_fiix_history.py` import is confirmed done for good (won't
run again) — see the code cleanup accompanying this migration, which
removes `schemas.LEGACY_PRIORITIES` and the backward-compatibility code
this migration makes unnecessary elsewhere (SLA_HOURS, priority sort
rank, the urgent-open KPI, and several frontend files).

Revision ID: f4a8d1c6e3b2
Revises: e7c4b9d2a1f6
Create Date: 2026-07-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a8d1c6e3b2'
down_revision: Union[str, Sequence[str], None] = 'e7c4b9d2a1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Same 1:1 mapping used for the original rename (d3f8a2c1e5b7) — SLA
# hour targets are identical either side, this is purely a relabel.
OLD_TO_NEW = {
    "Highest": "Immediate",
    "High": "Same Day",
    "Medium": "Next Day",
    "Low": "2 Days",
    "Lowest": "3 Days",
}

NEW_VALUES = ("Immediate", "Same Day", "Next Day", "2 Days", "3 Days")


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()

    for old, new in OLD_TO_NEW.items():
        conn.execute(
            sa.text("UPDATE work_orders SET priority = :new WHERE priority = :old"),
            {"new": new, "old": old},
        )
        conn.execute(
            sa.text(
                "UPDATE wo_status_history SET from_value = :new "
                "WHERE event_type = 'priority_change' AND from_value = :old"
            ),
            {"new": new, "old": old},
        )
        conn.execute(
            sa.text(
                "UPDATE wo_status_history SET to_value = :new "
                "WHERE event_type = 'priority_change' AND to_value = :old"
            ),
            {"new": new, "old": old},
        )

    values_sql = ",".join(f"'{v}'" for v in NEW_VALUES)
    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.drop_constraint("ck_wo_priority", type_="check")
        batch_op.create_check_constraint(
            "ck_wo_priority", f"priority IN ({values_sql})"
        )


def downgrade() -> None:
    """Downgrade schema.

    Data-only step (the priority relabel) is not reversed — same
    reasoning as why the original rename migration's downgrade didn't
    attempt to reverse data either; this just restores the wider
    (old+new) constraint so a downgrade doesn't immediately break on
    whatever's in the table at downgrade time.
    """
    old_values = ("Highest", "High", "Medium", "Low", "Lowest")
    all_values = old_values + NEW_VALUES
    values_sql = ",".join(f"'{v}'" for v in all_values)
    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.drop_constraint("ck_wo_priority", type_="check")
        batch_op.create_check_constraint(
            "ck_wo_priority", f"priority IN ({values_sql})"
        )
