"""add message delivery state

Revision ID: 3b9c2d1e4f60
Revises: 2a0f9b6c8d31
Create Date: 2026-04-28 01:25:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "3b9c2d1e4f60"
down_revision = "2a0f9b6c8d31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "delivery_state",
            sa.String(length=20),
            server_default="confirmed",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_messages_conversation_delivery_state",
        "messages",
        ["conversation_id", "delivery_state"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_delivery_state", table_name="messages")
    op.drop_column("messages", "delivery_state")
