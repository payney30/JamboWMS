"""add location hierarchy (parent_id, code, sort_order, is_active) to assets

Revision ID: 5160d845f250
Revises: 786b9f3b2554
Create Date: 2026-07-27 15:10:00.000000

Backs PRD 4.2a (hierarchical location picker) and 4.5 (admin-editable
asset hierarchy with soft-delete). assets rows already exist for every
node in the hierarchy, not just leaves (seed.py has always loaded
name_to_branch.json, which includes intermediate nodes) — this migration
just adds the columns needed to link them into a tree and to soft-delete
a node without breaking existing work_orders.asset_id references.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5160d845f250'
down_revision: Union[str, Sequence[str], None] = '786b9f3b2554'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("assets") as batch_op:
        batch_op.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("code", sa.String(), nullable=True))
        batch_op.add_column(sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0"
        ))
        batch_op.add_column(sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ))
        batch_op.create_foreign_key(
            "fk_assets_parent_id_assets", "assets", ["parent_id"], ["id"]
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("assets") as batch_op:
        batch_op.drop_constraint("fk_assets_parent_id_assets", type_="foreignkey")
        batch_op.drop_column("is_active")
        batch_op.drop_column("sort_order")
        batch_op.drop_column("code")
        batch_op.drop_column("parent_id")
