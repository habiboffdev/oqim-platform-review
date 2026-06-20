"""add performance indexes for conversations messages and conversation_pairs

Revision ID: 01ac5939b063
Revises: da3e999ed495
Create Date: 2026-04-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "01ac5939b063"
down_revision: Union[str, None] = "da3e999ed495"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Conversations: fast chat list query (ordered by last_message_at)
    op.create_index(
        "ix_conversations_workspace_last_msg",
        "conversations",
        ["workspace_id", sa.text("last_message_at DESC NULLS LAST")],
    )

    # 2. Messages: fast dedup for non-Telegram channels (partial index)
    op.create_index(
        "ix_messages_conversation_external_id",
        "messages",
        ["conversation_id", "external_message_id"],
        postgresql_where=sa.text("external_message_id IS NOT NULL"),
    )

    # 3. Conversation pairs: HNSW vector index for style retrieval
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cp_embedding_hnsw
        ON conversation_pairs
        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_cp_embedding_hnsw")
    op.drop_index("ix_messages_conversation_external_id", table_name="messages")
    op.drop_index("ix_conversations_workspace_last_msg", table_name="conversations")
