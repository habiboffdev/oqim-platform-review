"""create conversation hydration runtime

Revision ID: d6e7f8a9b0c1
Revises: c5d8e9a1f2b3
Create Date: 2026-05-03
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "d6e7f8a9b0c1"
down_revision = "c5d8e9a1f2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_hydration_runtime",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), server_default="idle", nullable=False),
        sa.Column("reason", sa.String(length=80), server_default="chat_open", nullable=False),
        sa.Column("requested_limit", sa.Integer(), server_default="50", nullable=False),
        sa.Column("requested_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("persisted_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duplicate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "conversation_id",
            name="uq_conversation_hydration_runtime_workspace_conversation",
        ),
    )
    op.create_index(
        "ix_conversation_hydration_runtime_workspace_id",
        "conversation_hydration_runtime",
        ["workspace_id"],
    )
    op.create_index(
        "ix_conversation_hydration_runtime_conversation_id",
        "conversation_hydration_runtime",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversation_hydration_runtime_state",
        "conversation_hydration_runtime",
        ["state"],
    )
    op.create_index(
        "ix_conversation_hydration_runtime_leased_until",
        "conversation_hydration_runtime",
        ["leased_until"],
    )
    op.create_index(
        "ix_conversation_hydration_runtime_next_attempt_at",
        "conversation_hydration_runtime",
        ["next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_hydration_runtime_next_attempt_at", table_name="conversation_hydration_runtime")
    op.drop_index("ix_conversation_hydration_runtime_leased_until", table_name="conversation_hydration_runtime")
    op.drop_index("ix_conversation_hydration_runtime_state", table_name="conversation_hydration_runtime")
    op.drop_index("ix_conversation_hydration_runtime_conversation_id", table_name="conversation_hydration_runtime")
    op.drop_index("ix_conversation_hydration_runtime_workspace_id", table_name="conversation_hydration_runtime")
    op.drop_table("conversation_hydration_runtime")
