"""add password_hash to workspaces

Revision ID: aad34a5ee56e
Revises: 56695bde8edc
Create Date: 2026-03-20 15:51:19.145506
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'aad34a5ee56e'
down_revision: Union[str, None] = '56695bde8edc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workspaces', sa.Column('password_hash', sa.String(length=255), server_default='', nullable=False))


def downgrade() -> None:
    op.drop_column('workspaces', 'password_hash')
