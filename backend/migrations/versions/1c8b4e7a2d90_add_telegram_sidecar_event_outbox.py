"""add telegram sidecar event outbox

Revision ID: 1c8b4e7a2d90
Revises: 7f2a9c8d1e0b
Create Date: 2026-04-26 02:05:00.000000
"""
from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "1c8b4e7a2d90"
down_revision: Union[str, None] = "7f2a9c8d1e0b"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_sidecar_event_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_unique_constraint(
        "uq_telegram_sidecar_event_outbox_idempotency",
        "telegram_sidecar_event_outbox",
        ["idempotency_key"],
    )
    op.create_index(
        "idx_telegram_sidecar_event_outbox_due",
        "telegram_sidecar_event_outbox",
        ["next_attempt_at", "workspace_id", "id"],
    )


def downgrade() -> None:
    op.drop_index("idx_telegram_sidecar_event_outbox_due", table_name="telegram_sidecar_event_outbox")
    op.drop_constraint(
        "uq_telegram_sidecar_event_outbox_idempotency",
        "telegram_sidecar_event_outbox",
        type_="unique",
    )
    op.drop_table("telegram_sidecar_event_outbox")
