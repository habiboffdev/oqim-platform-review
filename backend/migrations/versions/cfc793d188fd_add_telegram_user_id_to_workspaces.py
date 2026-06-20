"""add telegram_user_id to workspaces

Revision ID: cfc793d188fd
Revises: 91e5f00dfe8b
Create Date: 2026-03-20 19:19:27.068418
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'cfc793d188fd'
down_revision: Union[str, None] = '91e5f00dfe8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workspaces', sa.Column('telegram_user_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('workspaces', 'telegram_user_id')
