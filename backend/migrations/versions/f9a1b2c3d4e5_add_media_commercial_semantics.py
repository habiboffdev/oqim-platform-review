"""add media commercial semantics

Revision ID: f9a1b2c3d4e5
Revises: e8b2c3d4f5a6
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f9a1b2c3d4e5"
down_revision = "e8b2c3d4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_runtime",
        sa.Column(
            "commercial_semantics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("media_runtime", "commercial_semantics")
