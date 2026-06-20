"""add_tsvector_columns_for_hybrid_search

Revision ID: 56695bde8edc
Revises: f02b301eaf7d
Create Date: 2026-03-19 23:57:04.333675
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '56695bde8edc'
down_revision: Union[str, None] = 'f02b301eaf7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tsvector on catalog_items for hybrid keyword search
    op.execute("""
        ALTER TABLE catalog_items ADD COLUMN IF NOT EXISTS tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('simple',
                coalesce(name, '') || ' ' ||
                coalesce(description, '') || ' ' ||
                coalesce(category, '')
            )
        ) STORED
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_catalog_tsv ON catalog_items USING GIN (tsv)")

    # tsvector on business_knowledge for hybrid keyword search
    op.execute("""
        ALTER TABLE business_knowledge ADD COLUMN IF NOT EXISTS tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('simple',
                coalesce(title, '') || ' ' ||
                coalesce(content, '')
            )
        ) STORED
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_tsv ON business_knowledge USING GIN (tsv)")

    # Verify HNSW indexes exist on embedding columns (may already exist)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_catalog_embedding
        ON catalog_items USING hnsw (embedding vector_cosine_ops)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_knowledge_embedding
        ON business_knowledge USING hnsw (embedding vector_cosine_ops)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_catalog_tsv")
    op.execute("ALTER TABLE catalog_items DROP COLUMN IF EXISTS tsv")
    op.execute("DROP INDEX IF EXISTS idx_knowledge_tsv")
    op.execute("ALTER TABLE business_knowledge DROP COLUMN IF EXISTS tsv")
