"""merge all heads for fresh deploy

Revision ID: bf5eb0e00390
Revises: 01ac5939b063, 543f5fb587bc, b2c3d4e5f6a7
Create Date: 2026-04-14 06:04:00.375666
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bf5eb0e00390'
down_revision: Union[str, None] = ('01ac5939b063', '543f5fb587bc', 'b2c3d4e5f6a7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
