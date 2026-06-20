"""create conversation turn sessions

Revision ID: 4c6d8e0f1a23
Revises: 3b5c7d9e1f20
Create Date: 2026-05-30 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "4c6d8e0f1a23"
down_revision = "3b5c7d9e1f20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_turn_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("channel", sa.String(length=40), server_default="telegram_dm", nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("turn_key", sa.String(length=255), nullable=False),
        sa.Column("turn_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("first_customer_message_id", sa.BigInteger(), nullable=False),
        sa.Column("latest_customer_message_id", sa.BigInteger(), nullable=False),
        sa.Column("latest_customer_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_hermes_run_id", sa.String(length=128), nullable=True),
        sa.Column("active_engine_run_id", sa.String(length=255), nullable=True),
        sa.Column("generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_steer_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("steer_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_model_observed_revision", sa.Integer(), nullable=True),
        sa.Column("finalized_revision", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stale_reason", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["first_customer_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["latest_customer_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_conversation_turn_sessions_active",
        "conversation_turn_sessions",
        ["workspace_id", "conversation_id", "agent_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('open', 'starting', 'running', 'finalizing')"),
    )
    op.create_index(
        "ix_conversation_turn_sessions_conversation_state",
        "conversation_turn_sessions",
        ["workspace_id", "conversation_id", "state"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_turn_sessions_turn_key",
        "conversation_turn_sessions",
        ["turn_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_turn_sessions_turn_key", table_name="conversation_turn_sessions")
    op.drop_index("ix_conversation_turn_sessions_conversation_state", table_name="conversation_turn_sessions")
    op.drop_index("uq_conversation_turn_sessions_active", table_name="conversation_turn_sessions")
    op.drop_table("conversation_turn_sessions")
