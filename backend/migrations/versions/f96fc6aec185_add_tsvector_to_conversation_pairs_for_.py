"""add tsvector to conversation_pairs for hybrid search

Revision ID: f96fc6aec185
Revises: b8550d8ac727
Create Date: 2026-03-30 19:05:04.235327
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f96fc6aec185'
down_revision: Union[str, None] = 'b8550d8ac727'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tsvector on conversation_pairs for hybrid keyword search (BM25)
    # Indexes both customer and seller turns — catches exact product names,
    # prices, phone numbers that embeddings miss.
    # Uses 'simple' config (no stemming) — best for multilingual Uzbek/Russian/English mix.
    op.execute("""
        ALTER TABLE conversation_pairs ADD COLUMN IF NOT EXISTS tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('simple',
                coalesce(customer_turn, '') || ' ' ||
                coalesce(seller_turn, '') || ' ' ||
                coalesce(context_prefix, '')
            )
        ) STORED
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_cp_tsv ON conversation_pairs USING GIN (tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_cp_tsv")
    op.execute("ALTER TABLE conversation_pairs DROP COLUMN IF EXISTS tsv")
