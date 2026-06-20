"""create agent sessions

Revision ID: 6a7b8c9d0e12
Revises: 5f0a1b2c3d4e
Create Date: 2026-06-07 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "6a7b8c9d0e12"
down_revision = "5f0a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=40), server_default="telegram_dm", nullable=False),
        sa.Column("session_key", sa.String(length=255), nullable=False),
        sa.Column("hermes_session_id", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("last_customer_event_id", sa.BigInteger(), nullable=True),
        sa.Column("last_agent_event_id", sa.BigInteger(), nullable=True),
        sa.Column("event_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "conversation_id", "agent_id", name="uq_agent_sessions_owner_conversation"),
    )
    op.create_index("ix_agent_sessions_workspace_agent", "agent_sessions", ["workspace_id", "agent_id"])
    op.create_index("ix_agent_sessions_conversation", "agent_sessions", ["conversation_id"])
    op.create_index("ix_agent_sessions_hermes_session_id", "agent_sessions", ["hermes_session_id"], unique=True)
    op.create_index("ix_agent_sessions_state", "agent_sessions", ["state"])

    op.create_table(
        "agent_session_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("agent_session_id", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("direction", sa.String(length=24), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("hermes_run_id", sa.String(length=128), nullable=True),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["agent_session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_session_id", "sequence", name="uq_agent_session_events_sequence"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_agent_session_events_idempotency"),
    )
    op.create_index("ix_agent_session_events_session_created", "agent_session_events", ["agent_session_id", "created_at"])
    op.create_index("ix_agent_session_events_message", "agent_session_events", ["message_id"])
    op.create_index("ix_agent_session_events_hermes_run", "agent_session_events", ["hermes_run_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_session_events_hermes_run", table_name="agent_session_events")
    op.drop_index("ix_agent_session_events_message", table_name="agent_session_events")
    op.drop_index("ix_agent_session_events_session_created", table_name="agent_session_events")
    op.drop_table("agent_session_events")
    op.drop_index("ix_agent_sessions_state", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_hermes_session_id", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_conversation", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_workspace_agent", table_name="agent_sessions")
    op.drop_table("agent_sessions")
