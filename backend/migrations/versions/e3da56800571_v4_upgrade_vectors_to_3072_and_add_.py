"""v4 upgrade vectors to 3072 and add telegram_timestamp

Revision ID: e3da56800571
Revises: d1e2f3a4b5c6
Create Date: 2026-03-29 23:26:59.533632
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy.vector

# revision identifiers, used by Alembic.
revision: str = 'e3da56800571'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. DROP existing HNSW indexes FIRST (they block ALTER COLUMN beyond 2000 dims)
    op.execute("DROP INDEX IF EXISTS idx_catalog_embedding")
    op.execute("DROP INDEX IF EXISTS idx_catalog_image_embedding")
    op.execute("DROP INDEX IF EXISTS idx_learning_signals_embedding")

    # 2. Upgrade vector dimensions: 768 -> 3072
    op.alter_column('catalog_items', 'embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=768),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=3072),
               existing_nullable=True)
    op.alter_column('catalog_items', 'image_embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=768),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=3072),
               existing_nullable=True)
    op.alter_column('learning_signals', 'embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=768),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=3072),
               existing_nullable=True)

    # 3. Recreate HNSW indexes with halfvec (pgvector HNSW limit is 2000 for vector type)
    op.execute("""
        CREATE INDEX idx_catalog_embedding ON catalog_items
        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)
    op.execute("""
        CREATE INDEX idx_catalog_image_embedding ON catalog_items
        USING hnsw ((image_embedding::halfvec(3072)) halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # 4. Add telegram_timestamp to messages
    op.add_column('messages', sa.Column('telegram_timestamp', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'telegram_timestamp')

    op.execute("DROP INDEX IF EXISTS idx_catalog_embedding")
    op.execute("DROP INDEX IF EXISTS idx_catalog_image_embedding")

    op.alter_column('learning_signals', 'embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=3072),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=768),
               existing_nullable=True)
    op.alter_column('catalog_items', 'image_embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=3072),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=768),
               existing_nullable=True)
    op.alter_column('catalog_items', 'embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=3072),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=768),
               existing_nullable=True)
