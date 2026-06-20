"""add onboarding profile to workspaces

Revision ID: e6f7a8b9c0d1
Revises: d0a1b2c3d4e5
Create Date: 2026-05-06
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e6f7a8b9c0d1"
down_revision = "d0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("onboarding_profile", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "onboarding_profile")
