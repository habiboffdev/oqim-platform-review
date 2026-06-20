"""Add media_url to broadcasts

Revision ID: 010_broadcast_media
Revises: 009_tasks
Create Date: 2026-02-13 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "010_broadcast_media"
down_revision: Union[str, None] = "009_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("broadcasts", sa.Column("media_url", sa.String(500), nullable=True))


def downgrade():
    op.drop_column("broadcasts", "media_url")
