"""create hermes sessions

Revision ID: 9d0e1f2a3b45
Revises: 8c9d0e1f2a34
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "9d0e1f2a3b45"
down_revision = "8c9d0e1f2a34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hermes_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_session_id", sa.BigInteger(), nullable=False),
        sa.Column("hermes_session_id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=80), server_default="oqim", nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("system_prompt", sa.Text(), server_default="", nullable=False),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "token_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ended_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hermes_session_id", name="uq_hermes_sessions_session_id"),
    )
    op.create_index("ix_hermes_sessions_workspace_id", "hermes_sessions", ["workspace_id"])
    op.create_index("ix_hermes_sessions_agent_session_id", "hermes_sessions", ["agent_session_id"])
    op.create_index(
        "ix_hermes_sessions_workspace_agent_session",
        "hermes_sessions",
        ["workspace_id", "agent_session_id"],
    )

    op.create_table(
        "hermes_session_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("hermes_session_id", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_session_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.String(length=120), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tool_call_id", sa.String(length=255), nullable=True),
        sa.Column("finish_reason", sa.String(length=120), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["hermes_session_id"], ["hermes_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hermes_session_id", "sequence", name="uq_hermes_session_messages_sequence"),
    )
    op.create_index("ix_hermes_session_messages_hermes_session_id", "hermes_session_messages", ["hermes_session_id"])
    op.create_index("ix_hermes_session_messages_workspace_id", "hermes_session_messages", ["workspace_id"])
    op.create_index("ix_hermes_session_messages_agent_session_id", "hermes_session_messages", ["agent_session_id"])
    op.create_index(
        "ix_hermes_session_messages_session_created",
        "hermes_session_messages",
        ["hermes_session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_hermes_session_messages_session_created", table_name="hermes_session_messages")
    op.drop_index("ix_hermes_session_messages_agent_session_id", table_name="hermes_session_messages")
    op.drop_index("ix_hermes_session_messages_workspace_id", table_name="hermes_session_messages")
    op.drop_index("ix_hermes_session_messages_hermes_session_id", table_name="hermes_session_messages")
    op.drop_table("hermes_session_messages")
    op.drop_index("ix_hermes_sessions_workspace_agent_session", table_name="hermes_sessions")
    op.drop_index("ix_hermes_sessions_agent_session_id", table_name="hermes_sessions")
    op.drop_index("ix_hermes_sessions_workspace_id", table_name="hermes_sessions")
    op.drop_table("hermes_sessions")
