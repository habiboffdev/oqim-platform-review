"""Add workspaces.owner_control_chat_id for owner control-bot delivery.

Revision ID: a7b8c9d0e1f2
Revises: b1c2d3e4f6a8
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "b1c2d3e4f6a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("owner_control_chat_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "owner_control_chat_id")
