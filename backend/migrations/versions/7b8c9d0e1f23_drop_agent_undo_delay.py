"""drop agent undo delay

Revision ID: 7b8c9d0e1f23
Revises: 6a7b8c9d0e12
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7b8c9d0e1f23"
down_revision = "6a7b8c9d0e12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("agents", "undo_delay_seconds")


def downgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("undo_delay_seconds", sa.Integer(), nullable=False, server_default="15"),
    )
