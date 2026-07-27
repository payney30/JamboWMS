"""add poc fields to work_orders

Revision ID: 9e2c7a41d6b3
Revises: 5160d845f250
Create Date: 2026-07-27 20:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e2c7a41d6b3'
down_revision: Union[str, Sequence[str], None] = '5160d845f250'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default='true' so existing rows (all of which had a single
    # requester acting as their own POC) backfill correctly without a
    # separate data migration step.
    op.add_column(
        'work_orders',
        sa.Column('poc_is_requester', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column('work_orders', sa.Column('poc_name', sa.String(), nullable=True))
    op.add_column('work_orders', sa.Column('poc_phone', sa.String(), nullable=True))
    # Drop the server_default after backfill so future inserts rely on the
    # ORM-side default (models.WorkOrder.poc_is_requester) instead of the
    # DB default staying implicit forever — same convention as the other
    # boolean defaults in this schema (see Team.is_active / User.is_active
    # which don't carry server_defaults either). batch_alter_table keeps
    # this working on SQLite too (used by the test suite), which can't
    # ALTER COLUMN ... DROP DEFAULT directly.
    with op.batch_alter_table('work_orders') as batch_op:
        batch_op.alter_column('poc_is_requester', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('work_orders', 'poc_phone')
    op.drop_column('work_orders', 'poc_name')
    op.drop_column('work_orders', 'poc_is_requester')
