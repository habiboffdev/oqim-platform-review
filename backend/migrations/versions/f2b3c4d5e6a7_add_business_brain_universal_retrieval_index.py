"""add business brain universal retrieval index fields

Revision ID: f2b3c4d5e6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-05-07 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = "f2b3c4d5e6a7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "business_brain_index_records",
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "business_brain_index_records",
        sa.Column(
            "embedding_state",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column(
        "business_brain_index_records",
        sa.Column("embedding", Vector(3072), nullable=True),
    )
    op.add_column(
        "business_brain_index_records",
        sa.Column("source_text", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_business_brain_index_records_embedding_state",
        "business_brain_index_records",
        ["embedding_state"],
        unique=False,
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_business_brain_index_records_source_text_gin
        ON business_brain_index_records
        USING gin (to_tsvector('simple', coalesce(source_text, '')))
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_business_brain_index_records_embedding_hnsw
        ON business_brain_index_records
        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_business_brain_index_records_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_business_brain_index_records_source_text_gin")
    op.drop_index(
        "ix_business_brain_index_records_embedding_state",
        table_name="business_brain_index_records",
    )
    op.drop_column("business_brain_index_records", "source_text")
    op.drop_column("business_brain_index_records", "embedding")
    op.drop_column("business_brain_index_records", "embedding_state")
    op.drop_column("business_brain_index_records", "embedding_model")
