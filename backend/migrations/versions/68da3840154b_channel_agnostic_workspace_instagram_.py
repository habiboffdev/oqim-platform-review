"""channel_agnostic_workspace_instagram_fields

Add workspace columns for Instagram Business API and Telegram Business Bot API.
Migrate existing channel values from 'dm' to 'telegram_dm'.

Revision ID: 68da3840154b
Revises: 9f611b1fe433
Create Date: 2026-04-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '68da3840154b'
down_revision: Union[str, None] = '9f611b1fe433'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Workspace: Instagram Business API fields
    op.add_column('workspaces', sa.Column('instagram_connected', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('workspaces', sa.Column('instagram_page_id', sa.String(length=100), nullable=True))
    op.add_column('workspaces', sa.Column('instagram_access_token', sa.String(length=500), nullable=True))
    # Workspace: Telegram Business Bot API fields
    op.add_column('workspaces', sa.Column('telegram_business_bot_connected', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('workspaces', sa.Column('business_connection_id', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('workspaces', 'business_connection_id')
    op.drop_column('workspaces', 'telegram_business_bot_connected')
    op.drop_column('workspaces', 'instagram_access_token')
    op.drop_column('workspaces', 'instagram_page_id')
    op.drop_column('workspaces', 'instagram_connected')
