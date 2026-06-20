"""add customer ai_muted field

Revision ID: 543f5fb587bc
Revises: 68da3840154b
Create Date: 2026-04-14 23:33:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '543f5fb587bc'
down_revision: Union[str, None] = '68da3840154b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('customers', sa.Column('ai_muted', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    op.drop_column('customers', 'ai_muted')
