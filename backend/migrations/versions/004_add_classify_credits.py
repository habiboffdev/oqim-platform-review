"""add classify_credits to sellers

Revision ID: 004_credits
Revises: 305b749516b0
Create Date: 2026-02-08 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '004_credits'
down_revision: Union[str, None] = '305b749516b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sellers',
        sa.Column('classify_credits', sa.Integer(), nullable=False, server_default='50'),
    )


def downgrade() -> None:
    op.drop_column('sellers', 'classify_credits')
