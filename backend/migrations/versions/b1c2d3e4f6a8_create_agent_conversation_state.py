"""create agent conversation state snapshots

Revision ID: b1c2d3e4f6a8
Revises: a0b1c2d3e4f5
Create Date: 2026-06-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b1c2d3e4f6a8"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_conversation_state_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_session_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("hermes_run_id", sa.String(length=128), nullable=True),
        sa.Column("stage", sa.String(length=80), server_default="unknown", nullable=False),
        sa.Column("active_intent", sa.String(length=120), nullable=True),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_agent_conversation_state_idempotency",
        ),
    )
    op.create_index(
        "ix_agent_conversation_state_agent_id",
        "agent_conversation_state_snapshots",
        ["agent_id"],
    )
    op.create_index(
        "ix_agent_conversation_state_agent_session_id",
        "agent_conversation_state_snapshots",
        ["agent_session_id"],
    )
    op.create_index(
        "ix_agent_conversation_state_conversation_id",
        "agent_conversation_state_snapshots",
        ["conversation_id"],
    )
    op.create_index(
        "ix_agent_conversation_state_customer_id",
        "agent_conversation_state_snapshots",
        ["customer_id"],
    )
    op.create_index(
        "ix_agent_conversation_state_hermes_run",
        "agent_conversation_state_snapshots",
        ["hermes_run_id"],
    )
    op.create_index(
        "ix_agent_conversation_state_session_created",
        "agent_conversation_state_snapshots",
        ["agent_session_id", "created_at"],
    )
    op.create_index(
        "ix_agent_conversation_state_workspace_conversation",
        "agent_conversation_state_snapshots",
        ["workspace_id", "conversation_id"],
    )
    op.create_index(
        "ix_agent_conversation_state_workspace_id",
        "agent_conversation_state_snapshots",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_conversation_state_workspace_id", table_name="agent_conversation_state_snapshots")
    op.drop_index("ix_agent_conversation_state_workspace_conversation", table_name="agent_conversation_state_snapshots")
    op.drop_index("ix_agent_conversation_state_session_created", table_name="agent_conversation_state_snapshots")
    op.drop_index("ix_agent_conversation_state_hermes_run", table_name="agent_conversation_state_snapshots")
    op.drop_index("ix_agent_conversation_state_customer_id", table_name="agent_conversation_state_snapshots")
    op.drop_index("ix_agent_conversation_state_conversation_id", table_name="agent_conversation_state_snapshots")
    op.drop_index("ix_agent_conversation_state_agent_session_id", table_name="agent_conversation_state_snapshots")
    op.drop_index("ix_agent_conversation_state_agent_id", table_name="agent_conversation_state_snapshots")
    op.drop_table("agent_conversation_state_snapshots")
