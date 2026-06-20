"""merge_voice_profile_unique_constraint

Revision ID: 9f611b1fe433
Revises: c9d8e7f6a5b4, e1f2a3b4c5d6
Create Date: 2026-04-09 10:42:47.295994
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f611b1fe433'
down_revision: Union[str, None] = ('c9d8e7f6a5b4', 'e1f2a3b4c5d6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
