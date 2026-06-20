"""create business brain index records

Revision ID: d0a1b2c3d4e5
Revises: c8f0d1e2a3b4
Create Date: 2026-05-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d0a1b2c3d4e5"
down_revision = "c8f0d1e2a3b4"
branch_labels = None
depends_on = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "business_brain_index_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("index_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("fact_id", sa.String(length=255), nullable=False),
        sa.Column("unit_ref", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("embedding_ref", sa.String(length=255), nullable=True),
        sa.Column("degraded_reason", sa.String(length=255), nullable=True),
        sa.Column("source_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("raw_index", _jsonb(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "index_id", name="uq_business_brain_index_records_workspace_index"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_business_brain_index_records_workspace_idempotency"),
    )
    for column in ("workspace_id", "fact_id", "unit_ref", "state"):
        op.create_index(
            f"ix_business_brain_index_records_{column}",
            "business_brain_index_records",
            [column],
        )


def downgrade() -> None:
    for column in ("state", "unit_ref", "fact_id", "workspace_id"):
        op.drop_index(
            f"ix_business_brain_index_records_{column}",
            table_name="business_brain_index_records",
        )
    op.drop_table("business_brain_index_records")
