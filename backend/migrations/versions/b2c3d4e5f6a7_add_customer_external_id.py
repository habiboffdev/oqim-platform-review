"""add customer external_id and channel columns for multi-channel identity (#112)

Revision ID: a1b2c3d4e5f6
Revises: 68da3840154b
Create Date: 2026-04-13 22:37:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = '68da3840154b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add external_id and channel columns
    op.add_column('customers', sa.Column('external_id', sa.String(255), nullable=True))
    op.add_column('customers', sa.Column('channel', sa.String(20), server_default='telegram_dm', nullable=False))

    # Backfill external_id from telegram_id for existing customers
    op.execute("UPDATE customers SET external_id = CAST(telegram_id AS TEXT) WHERE telegram_id IS NOT NULL AND external_id IS NULL")

    # Partial unique index (excludes NULLs — PostgreSQL unique constraints treat NULLs as distinct)
    op.create_index(
        'uq_customer_workspace_external',
        'customers',
        ['workspace_id', 'external_id', 'channel'],
        unique=True,
        postgresql_where=sa.text('external_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_customer_workspace_external', table_name='customers')
    op.drop_column('customers', 'channel')
    op.drop_column('customers', 'external_id')
