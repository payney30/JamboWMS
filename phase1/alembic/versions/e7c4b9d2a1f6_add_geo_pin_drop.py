"""add latitude/longitude for geo pin-drop

Enhancement backlog Phase 15 (NJ2026_Work_Order_System_PRD.md §13#14):
optional geo pin-drop on Submit WO — supplements (doesn't replace) the
location-hierarchy picker. Nullable on purpose: a WO with no pin simply
shows no map, same as before this feature existed.

Revision ID: e7c4b9d2a1f6
Revises: d3f8a2c1e5b7
Create Date: 2026-07-30 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7c4b9d2a1f6'
down_revision: Union[str, Sequence[str], None] = 'd3f8a2c1e5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.add_column(sa.Column("latitude", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.drop_column("longitude")
        batch_op.drop_column("latitude")
