"""create delivery runtime

Revision ID: 4f7a2e8c9d10
Revises: 3b9c2d1e4f60
Create Date: 2026-04-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "4f7a2e8c9d10"
down_revision = "3b9c2d1e4f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_runtime",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("ai_reply_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("channel_conversation_id", sa.String(length=255), nullable=True),
        sa.Column("client_idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=24), server_default="requested", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sending_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unknown_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["ai_reply_id"], ["ai_replies.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "client_idempotency_key", name="uq_delivery_runtime_workspace_key"),
    )
    op.create_index("ix_delivery_runtime_workspace_id", "delivery_runtime", ["workspace_id"])
    op.create_index("ix_delivery_runtime_conversation_id", "delivery_runtime", ["conversation_id"])
    op.create_index("ix_delivery_runtime_message_id", "delivery_runtime", ["message_id"])
    op.create_index("ix_delivery_runtime_ai_reply_id", "delivery_runtime", ["ai_reply_id"])
    op.create_index("ix_delivery_runtime_state", "delivery_runtime", ["state"])


def downgrade() -> None:
    op.drop_index("ix_delivery_runtime_state", table_name="delivery_runtime")
    op.drop_index("ix_delivery_runtime_ai_reply_id", table_name="delivery_runtime")
    op.drop_index("ix_delivery_runtime_message_id", table_name="delivery_runtime")
    op.drop_index("ix_delivery_runtime_conversation_id", table_name="delivery_runtime")
    op.drop_index("ix_delivery_runtime_workspace_id", table_name="delivery_runtime")
    op.drop_table("delivery_runtime")
