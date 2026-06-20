"""Knowledge base, pgvector extension, embedding columns, HNSW indexes.

Revision ID: 003_knowledge
Revises: 002_phase1
Create Date: 2026-02-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "003_knowledge"
down_revision: Union[str, None] = "002_phase1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Add embedding column to products
    op.add_column("products", sa.Column("embedding", Vector(768), nullable=True))

    # Add summary_updated_at to conversations
    op.add_column(
        "conversations",
        sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create business_knowledge table
    op.create_table(
        "business_knowledge",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "seller_id",
            sa.Integer(),
            sa.ForeignKey("sellers.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(50), server_default="faq"),
        sa.Column("source", sa.String(50), server_default="manual"),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # HNSW indexes for fast cosine similarity search
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_embedding "
        "ON products USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_business_knowledge_embedding "
        "ON business_knowledge USING hnsw (embedding vector_cosine_ops)"
    )
    op.create_index(
        "ix_business_knowledge_seller_active",
        "business_knowledge",
        ["seller_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_business_knowledge_seller_active")
    op.execute("DROP INDEX IF EXISTS ix_business_knowledge_embedding")
    op.execute("DROP INDEX IF EXISTS ix_products_embedding")
    op.drop_table("business_knowledge")
    op.drop_column("conversations", "summary_updated_at")
    op.drop_column("products", "embedding")
