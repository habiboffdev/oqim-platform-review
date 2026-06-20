"""add replier fields to ai_replies

Revision ID: 7f0194b032ce
Revises: a32922c4e933
Create Date: 2026-03-23 01:26:21.484432
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7f0194b032ce'
down_revision: Union[str, None] = 'a32922c4e933'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_replies', sa.Column('intent', sa.String(length=32), nullable=True))
    op.add_column('ai_replies', sa.Column('is_auto_sent', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('ai_replies', sa.Column('suppressed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('ai_replies', 'suppressed_at')
    op.drop_column('ai_replies', 'is_auto_sent')
    op.drop_column('ai_replies', 'intent')
