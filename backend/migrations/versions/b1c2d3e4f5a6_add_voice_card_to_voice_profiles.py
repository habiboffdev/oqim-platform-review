"""add voice_card to voice_profiles

Revision ID: b1c2d3e4f5a6
Revises: 32396243054e
Create Date: 2026-03-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = '32396243054e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('voice_profiles', sa.Column('voice_card', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('voice_profiles', 'voice_card')
