"""phase4: knowledge chunk embeddings

Revision ID: 3b5c7d9e1f20
Revises: 2a4b6c8d0e12
Create Date: 2026-05-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "3b5c7d9e1f20"
down_revision = "2a4b6c8d0e12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "embedding_state",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding_degraded_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding", Vector(3072), nullable=True),
    )
    op.create_index(
        "ix_knowledge_chunks_embedding_state",
        "knowledge_chunks",
        ["embedding_state"],
        unique=False,
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding_hnsw
        ON knowledge_chunks
        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_knowledge_chunks_embedding_hnsw")
    op.drop_index("ix_knowledge_chunks_embedding_state", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "embedding")
    op.drop_column("knowledge_chunks", "embedding_degraded_reason")
    op.drop_column("knowledge_chunks", "embedding_state")
    op.drop_column("knowledge_chunks", "embedding_model")
