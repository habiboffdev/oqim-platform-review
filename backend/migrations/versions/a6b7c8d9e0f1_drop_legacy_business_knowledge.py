"""drop legacy business knowledge store

Revision ID: a6b7c8d9e0f1
Revises: f5e6a7b8c9d0
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "a6b7c8d9e0f1"
down_revision = "f5e6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS business_knowledge CASCADE")


def downgrade() -> None:
    op.create_table(
        "business_knowledge",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("contextual_text", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("embedding", Vector(3072), nullable=True),
        sa.Column(
            "embedding_status",
            sa.String(length=20),
            server_default="pending",
            nullable=True,
        ),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("frequency", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_business_knowledge_workspace_active",
        "business_knowledge",
        ["workspace_id", "is_active"],
        unique=False,
    )
