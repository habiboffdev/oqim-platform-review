"""drop legacy conversation_pairs table

Revision ID: f1a2b3c4d5e6
Revises: e6f7a8b9c0d1
Create Date: 2026-05-07
"""
from __future__ import annotations

from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conversation_pairs CASCADE")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_pairs (
            id SERIAL PRIMARY KEY,
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            conversation_id INTEGER NOT NULL REFERENCES conversations(id),
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            customer_turn TEXT NOT NULL,
            seller_turn TEXT NOT NULL,
            context_prefix TEXT,
            previous_turns TEXT,
            has_media BOOLEAN NOT NULL DEFAULT FALSE,
            media_type VARCHAR(20),
            media_bytes BYTEA,
            media_description TEXT,
            intent VARCHAR(50),
            pair_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
            customer_msg_count INTEGER NOT NULL DEFAULT 1,
            seller_msg_count INTEGER NOT NULL DEFAULT 1,
            embedding vector(3072) NOT NULL,
            tsv tsvector,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT uq_pair_conv_ts UNIQUE (conversation_id, pair_timestamp)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cp_conversation "
        "ON conversation_pairs (conversation_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_cp_customer ON conversation_pairs (customer_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cp_timestamp ON conversation_pairs (pair_timestamp)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_cp_workspace ON conversation_pairs (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_cp_tsv ON conversation_pairs USING GIN (tsv)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cp_embedding ON conversation_pairs
        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )
