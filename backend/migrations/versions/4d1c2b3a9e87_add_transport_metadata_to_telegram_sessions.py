"""add transport metadata to telegram sessions

Revision ID: 4d1c2b3a9e87
Revises: 3c8e2d1f9a44
Create Date: 2026-05-12 04:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d1c2b3a9e87"
down_revision: Union[str, None] = "3c8e2d1f9a44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("telegram_sessions", sa.Column("transport", sa.String(length=16), nullable=True))
    op.add_column(
        "telegram_sessions",
        sa.Column("client_profile", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telegram_sessions", "client_profile")
    op.drop_column("telegram_sessions", "transport")
