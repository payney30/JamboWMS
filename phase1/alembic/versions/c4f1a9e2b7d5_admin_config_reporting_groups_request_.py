"""admin config: reporting groups, request types, asset change log

Revision ID: c4f1a9e2b7d5
Revises: 9e2c7a41d6b3
Create Date: 2026-07-28 14:20:00.000000

Backs PRD 4.5 (admin configuration interface):
  - 4.5b: reporting_groups table + assets.reporting_group_id, replacing
    the flat assets.location_group value that was previously baked in at
    seed time. location_group itself is untouched here — it stays the
    live-resolved display column every existing query already reads, now
    kept in sync by crud.recompute_effective_groups instead of seed.py.
  - 4.5c: request_types table, replacing the hardcoded work_type
    CheckConstraint — new work orders are validated against this table at
    the application layer instead (see app/routers/public.py).
  - 4.5a: asset_change_log audit trail for live hierarchy edits.

This is schema-only, matching this repo's existing convention (see
5160d845f250) — data backfill (seeding reporting_groups/request_types rows
and setting reporting_group_id overrides on existing assets) happens by
re-running seed.py, which is idempotent, not by data migration here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f1a9e2b7d5'
down_revision: Union[str, Sequence[str], None] = '9e2c7a41d6b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'reporting_groups',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint('name', name='uq_reporting_groups_name'),
    )
    op.create_table(
        'request_types',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint('name', name='uq_request_types_name'),
    )
    op.create_table(
        'asset_change_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('asset_id', sa.Integer(), nullable=False),
        sa.Column('field_changed', sa.String(), nullable=False),
        sa.Column('from_value', sa.String(), nullable=True),
        sa.Column('to_value', sa.String(), nullable=True),
        sa.Column('changed_by', sa.Integer(), nullable=True),
        sa.Column('changed_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['changed_by'], ['users.id']),
    )
    with op.batch_alter_table('assets') as batch_op:
        batch_op.add_column(sa.Column('reporting_group_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_assets_reporting_group_id', 'reporting_groups', ['reporting_group_id'], ['id']
        )
    # Drop the old hardcoded work_type allow-list — request_types now
    # governs what's valid, validated in app/routers/public.py instead of
    # at the DB layer. batch mode for SQLite (used by the test suite)
    # compatibility, same as the earlier poc_is_requester migration.
    with op.batch_alter_table('work_orders') as batch_op:
        batch_op.drop_constraint('ck_wo_work_type', type_='check')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('work_orders') as batch_op:
        batch_op.create_check_constraint(
            'ck_wo_work_type',
            "work_type IN ('NJ IT','NJ Items/Parts','NJ Maintenance','NJ Transportation','')",
        )
    with op.batch_alter_table('assets') as batch_op:
        batch_op.drop_constraint('fk_assets_reporting_group_id', type_='foreignkey')
        batch_op.drop_column('reporting_group_id')
    op.drop_table('asset_change_log')
    op.drop_table('request_types')
    op.drop_table('reporting_groups')
