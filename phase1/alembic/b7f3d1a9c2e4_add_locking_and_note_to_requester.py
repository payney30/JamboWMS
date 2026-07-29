"""add WO locking fields and note_to_requester

Enhancement backlog Phase 1 (NJ2026_Work_Order_System_PRD.md §14#1, #2, #5):
- locked_by_id / locked_at back the "opening a WO locks it for editing"
  feature — see models.WorkOrder.locked_by / crud.acquire_lock /
  crud.release_lock.
- note_to_requester backs the new requester-facing note field, editable
  from the WO detail screen and surfaced on the public status-lookup
  response (app/routers/public.py).

Revision ID: b7f3d1a9c2e4
Revises: c4f1a9e2b7d5
Create Date: 2026-07-28 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f3d1a9c2e4'
down_revision: Union[str, Sequence[str], None] = 'c4f1a9e2b7d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.add_column(sa.Column("locked_by_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("locked_at", sa.TIMESTAMP(), nullable=True))
        batch_op.add_column(sa.Column("note_to_requester", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_wo_locked_by_id_users", "users", ["locked_by_id"], ["id"]
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.drop_constraint("fk_wo_locked_by_id_users", type_="foreignkey")
        batch_op.drop_column("note_to_requester")
        batch_op.drop_column("locked_at")
        batch_op.drop_column("locked_by_id")
