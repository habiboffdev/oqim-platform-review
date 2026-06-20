"""add fact index_state for automatic brain indexing

Revision ID: 0f3be6c1d29e
Revises: a9b0c1d2e3f4
Create Date: 2026-05-25 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0f3be6c1d29e"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "business_brain_facts",
        sa.Column("index_state", sa.String(length=16), server_default="skipped", nullable=False),
    )
    op.add_column(
        "business_brain_facts",
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_bbfr_index_pending",
        "business_brain_facts",
        ["id"],
        postgresql_where=sa.text("index_state = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_bbfr_index_pending", table_name="business_brain_facts")
    op.drop_column("business_brain_facts", "indexed_at")
    op.drop_column("business_brain_facts", "index_state")
