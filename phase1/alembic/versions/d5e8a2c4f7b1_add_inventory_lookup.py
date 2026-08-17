"""inventory / supply lookup (PRD 4.5e)

Revision ID: d5e8a2c4f7b1
Revises: c4d6e8f0a2b4
Create Date: 2026-08-17 15:00:00.000000

Backs PRD §4.5e (inventory / supply lookup in LOC triage):
  - inventory_items: warehouse SKU catalog, refreshed via the admin
    Inventory tab's CSV diff/import (crud.diff_inventory_import /
    apply_inventory_import). Schema-only here — the table starts empty;
    the first real import happens from the admin UI, not a data
    migration.
  - wo_suggested_supplies: structured SKU attachments per work order.
  - wo_notes.note_type gains 'supply_request', for the auto-generated
    note mirroring an attach action into the note timeline.
  - request_types.show_inventory_lookup: admin-toggleable flag
    controlling which request types show the inventory widget in
    triage, defaulting on for the existing 'NJ Items/Parts' row so
    go-live doesn't require a manual admin click first (see seed.py).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e8a2c4f7b1'
down_revision: Union[str, Sequence[str], None] = 'c4d6e8f0a2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'inventory_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sku', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('subcategory', sa.String(), nullable=True),
        sa.Column('qty_on_hand', sa.Integer(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_updated_at', sa.TIMESTAMP(), nullable=True),
        sa.UniqueConstraint('sku', name='uq_inventory_items_sku'),
    )
    op.create_table(
        'wo_suggested_supplies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('work_order_id', sa.Integer(), nullable=False),
        sa.Column('sku', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=False),
        sa.Column('qty_requested', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('added_by_id', sa.Integer(), nullable=True),
        sa.Column('added_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['work_order_id'], ['work_orders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['added_by_id'], ['users.id']),
    )
    with op.batch_alter_table('wo_notes') as batch_op:
        batch_op.drop_constraint('ck_note_type', type_='check')
        batch_op.create_check_constraint(
            'ck_note_type',
            "note_type IN ('internal','instruction','work_note','supply_request')",
        )
    with op.batch_alter_table('request_types') as batch_op:
        batch_op.add_column(
            sa.Column('show_inventory_lookup', sa.Boolean(), nullable=False, server_default=sa.false())
        )
    # Default the widget on for the existing 'NJ Items/Parts' row (if
    # present — a fresh install seeds it via seed.py, which also sets
    # this going forward; this covers an already-seeded database).
    # Bug fix (Render deploy, 8/17/26): '1' is a valid boolean literal on
    # SQLite (booleans are just integers there) but Postgres has a real
    # boolean type and rejects an integer literal for it outright
    # ("column is of type boolean but expression is of type integer").
    # TRUE/FALSE are standard SQL boolean literals both dialects accept
    # (SQLite has treated them as aliases for 1/0 since 3.23).
    op.execute(
        "UPDATE request_types SET show_inventory_lookup = TRUE "
        "WHERE name = 'NJ Items/Parts'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('request_types') as batch_op:
        batch_op.drop_column('show_inventory_lookup')
    with op.batch_alter_table('wo_notes') as batch_op:
        batch_op.drop_constraint('ck_note_type', type_='check')
        batch_op.create_check_constraint(
            'ck_note_type',
            "note_type IN ('internal','instruction','work_note')",
        )
    op.drop_table('wo_suggested_supplies')
    op.drop_table('inventory_items')
