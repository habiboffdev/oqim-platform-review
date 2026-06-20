"""create action runtime

Revision ID: 6ac1d91f2b40
Revises: 4f7a2e8c9d10
Create Date: 2026-04-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "6ac1d91f2b40"
down_revision = "4f7a2e8c9d10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_runtime",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("source", sa.String(length=120), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("degraded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "conversation_id",
            "message_id",
            "action",
            name="uq_action_runtime_message_action",
        ),
    )
    op.create_index("ix_action_runtime_workspace_id", "action_runtime", ["workspace_id"])
    op.create_index("ix_action_runtime_conversation_id", "action_runtime", ["conversation_id"])
    op.create_index("ix_action_runtime_message_id", "action_runtime", ["message_id"])
    op.create_index("ix_action_runtime_action", "action_runtime", ["action"])
    op.create_index("ix_action_runtime_state", "action_runtime", ["state"])


def downgrade() -> None:
    op.drop_index("ix_action_runtime_state", table_name="action_runtime")
    op.drop_index("ix_action_runtime_action", table_name="action_runtime")
    op.drop_index("ix_action_runtime_message_id", table_name="action_runtime")
    op.drop_index("ix_action_runtime_conversation_id", table_name="action_runtime")
    op.drop_index("ix_action_runtime_workspace_id", table_name="action_runtime")
    op.drop_table("action_runtime")
