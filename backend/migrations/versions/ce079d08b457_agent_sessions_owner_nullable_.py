"""agent_sessions owner nullable conversation

Owner-plane spike #439 (Option B): owner/setup turns have no Conversation, so
agent_sessions.conversation_id (and agent_session_events.conversation_id) become
nullable, and an owner_chat_id column + a partial unique index key owner sessions
by chat so owner memory stays stable. The seller hot path is untouched — it still
passes a real conversation_id, and the existing unique constraint still enforces
seller-session uniqueness on non-null rows.

Revision ID: ce079d08b457
Revises: 8df8b42769b9
Create Date: 2026-06-17 05:34:55.662413
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ce079d08b457'
down_revision: str | None = '8df8b42769b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "agent_sessions", "conversation_id", existing_type=sa.BigInteger(), nullable=True
    )
    op.add_column(
        "agent_sessions", sa.Column("owner_chat_id", sa.BigInteger(), nullable=True)
    )
    op.create_index(
        "uq_agent_sessions_owner_chat",
        "agent_sessions",
        ["workspace_id", "agent_id", "owner_chat_id"],
        unique=True,
        postgresql_where=sa.text("conversation_id IS NULL"),
    )
    op.alter_column(
        "agent_session_events",
        "conversation_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def downgrade() -> None:
    # Owner-session rows (conversation_id IS NULL) must be removed before the
    # NOT NULL can be restored.
    op.execute("DELETE FROM agent_session_events WHERE conversation_id IS NULL")
    op.execute("DELETE FROM agent_sessions WHERE conversation_id IS NULL")
    op.alter_column(
        "agent_session_events",
        "conversation_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_index("uq_agent_sessions_owner_chat", table_name="agent_sessions")
    op.drop_column("agent_sessions", "owner_chat_id")
    op.alter_column(
        "agent_sessions", "conversation_id", existing_type=sa.BigInteger(), nullable=False
    )
