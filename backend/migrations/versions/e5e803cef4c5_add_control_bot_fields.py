"""Add workspaces.control_bot_token + control_bot_username (owner control bot).

Revision ID: e5e803cef4c5
Revises: a7b8c9d0e1f2
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5e803cef4c5"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("control_bot_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column("control_bot_username", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "control_bot_username")
    op.drop_column("workspaces", "control_bot_token")
