"""upgrade telegram auth attempts recovery

Revision ID: 6b7c8d9e0f12
Revises: 5d2a8c1e9f40
Create Date: 2026-05-11
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "6b7c8d9e0f12"
down_revision = "5d2a8c1e9f40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("telegram_auth_attempts", sa.Column("temp_session_data", sa.Text(), nullable=True))
    op.add_column("telegram_auth_attempts", sa.Column("next_recovery_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("telegram_auth_attempts", sa.Column("last_recovery_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("telegram_auth_attempts", sa.Column("recovery_state", sa.String(length=32), nullable=True))
    op.add_column(
        "telegram_auth_attempts",
        sa.Column("recovery_attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "telegram_auth_attempts",
        sa.Column("max_recovery_attempts", sa.Integer(), server_default="2", nullable=False),
    )
    op.add_column("telegram_auth_attempts", sa.Column("retry_after_seconds", sa.Integer(), nullable=True))
    op.create_index(
        "ix_telegram_auth_attempts_next_recovery_at",
        "telegram_auth_attempts",
        ["next_recovery_at"],
    )
    op.create_index(
        "ix_telegram_auth_attempts_recovery_state",
        "telegram_auth_attempts",
        ["recovery_state"],
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_auth_attempts_recovery_state", table_name="telegram_auth_attempts")
    op.drop_index("ix_telegram_auth_attempts_next_recovery_at", table_name="telegram_auth_attempts")
    op.drop_column("telegram_auth_attempts", "retry_after_seconds")
    op.drop_column("telegram_auth_attempts", "max_recovery_attempts")
    op.drop_column("telegram_auth_attempts", "recovery_attempt_count")
    op.drop_column("telegram_auth_attempts", "recovery_state")
    op.drop_column("telegram_auth_attempts", "last_recovery_at")
    op.drop_column("telegram_auth_attempts", "next_recovery_at")
    op.drop_column("telegram_auth_attempts", "temp_session_data")
