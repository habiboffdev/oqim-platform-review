"""create telegram auth attempts

Revision ID: 5d2a8c1e9f40
Revises: f4d5e6a7b8c9
Create Date: 2026-05-11
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "5d2a8c1e9f40"
down_revision = "f4d5e6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_auth_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("temp_session_id", sa.String(length=120), nullable=True),
        sa.Column("phone_code_hash", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), server_default="requested", nullable=False),
        sa.Column("delivery_type", sa.String(length=80), nullable=True),
        sa.Column("next_delivery_type", sa.String(length=80), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_step", sa.String(length=40), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivery_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("temp_session_id", name="uq_telegram_auth_attempt_temp_session"),
    )
    op.create_index("ix_telegram_auth_attempts_workspace_id", "telegram_auth_attempts", ["workspace_id"])
    op.create_index("ix_telegram_auth_attempts_phone_number", "telegram_auth_attempts", ["phone_number"])
    op.create_index("ix_telegram_auth_attempts_temp_session_id", "telegram_auth_attempts", ["temp_session_id"])
    op.create_index("ix_telegram_auth_attempts_state", "telegram_auth_attempts", ["state"])


def downgrade() -> None:
    op.drop_index("ix_telegram_auth_attempts_state", table_name="telegram_auth_attempts")
    op.drop_index("ix_telegram_auth_attempts_temp_session_id", table_name="telegram_auth_attempts")
    op.drop_index("ix_telegram_auth_attempts_phone_number", table_name="telegram_auth_attempts")
    op.drop_index("ix_telegram_auth_attempts_workspace_id", table_name="telegram_auth_attempts")
    op.drop_table("telegram_auth_attempts")
