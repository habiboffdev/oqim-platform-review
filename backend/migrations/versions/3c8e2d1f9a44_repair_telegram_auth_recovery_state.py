"""repair telegram auth recovery state

Revision ID: 3c8e2d1f9a44
Revises: 2f9d4c7b8a11
Create Date: 2026-05-11
"""
from __future__ import annotations

from alembic import op


revision = "3c8e2d1f9a44"
down_revision = "2f9d4c7b8a11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE telegram_auth_attempts
        SET recovery_state = 'exhausted'
        WHERE state = 'recovery_sent'
          AND recovery_state = 'scheduled'
          AND next_recovery_at IS NULL
        """
    )


def downgrade() -> None:
    pass
