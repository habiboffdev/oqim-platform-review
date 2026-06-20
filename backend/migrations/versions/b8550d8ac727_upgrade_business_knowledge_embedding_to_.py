"""upgrade business_knowledge embedding to 3072 with halfvec + recreate learning_signals index

Revision ID: b8550d8ac727
Revises: 00de7306b68e
Create Date: 2026-03-30 17:17:25.681356
"""
from typing import Sequence, Union

from alembic import op
import pgvector.sqlalchemy.vector
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8550d8ac727'
down_revision: Union[str, None] = '00de7306b68e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old HNSW indexes (768-dim, vector_cosine_ops — incompatible with 3072)
    op.execute("DROP INDEX IF EXISTS ix_business_knowledge_embedding")
    op.execute("DROP INDEX IF EXISTS idx_knowledge_embedding")

    # Upgrade column: 768 -> 3072
    op.alter_column('business_knowledge', 'embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=768),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=3072),
               existing_nullable=True)

    # Recreate HNSW index with halfvec (pgvector HNSW limit is 2000 for vector type)
    op.execute("""
        CREATE INDEX idx_knowledge_embedding ON business_knowledge
        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # Recreate learning_signals HNSW index (dropped in e3da56800571 but never recreated)
    op.execute("""
        CREATE INDEX idx_learning_signals_embedding ON learning_signals
        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_learning_signals_embedding")
    op.execute("DROP INDEX IF EXISTS idx_knowledge_embedding")

    op.alter_column('business_knowledge', 'embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=3072),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=768),
               existing_nullable=True)

    op.execute("""
        CREATE INDEX ix_business_knowledge_embedding
        ON business_knowledge USING hnsw (embedding vector_cosine_ops)
    """)
