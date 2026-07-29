"""add app_settings table for global admin config (timezone)

Enhancement backlog Phase 4 (NJ2026_Work_Order_System_PRD.md §15#1):
key-value store for small admin-configurable settings, starting with the
display time zone every screen formats dates/times against.

Revision ID: c8e2f4a1b6d3
Revises: b7f3d1a9c2e4
Create Date: 2026-07-30 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8e2f4a1b6d3'
down_revision: Union[str, Sequence[str], None] = 'b7f3d1a9c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("app_settings")
