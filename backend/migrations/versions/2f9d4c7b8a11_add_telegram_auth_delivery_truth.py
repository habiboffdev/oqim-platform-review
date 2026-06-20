"""add telegram auth delivery truth

Revision ID: 2f9d4c7b8a11
Revises: 6b7c8d9e0f12
Create Date: 2026-05-11
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "2f9d4c7b8a11"
down_revision = "6b7c8d9e0f12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telegram_auth_attempts",
        sa.Column("preferred_delivery_type", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "telegram_auth_attempts",
        sa.Column("delivery_degraded", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "telegram_auth_attempts",
        sa.Column("delivery_degraded_reason", sa.Text(), nullable=True),
    )
    op.alter_column("telegram_auth_attempts", "delivery_degraded", server_default=None)


def downgrade() -> None:
    op.drop_column("telegram_auth_attempts", "delivery_degraded_reason")
    op.drop_column("telegram_auth_attempts", "delivery_degraded")
    op.drop_column("telegram_auth_attempts", "preferred_delivery_type")
