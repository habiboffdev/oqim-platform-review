"""add crm_lead_links.pending_field_ops

Configurable multi-CRM S4: the records-agent queues worker-drained custom-field +
tag write ops on the lead link. Additive nullable jsonb with a server default of
'[]' (same pattern as pending_notes / pending_tasks). No backfill — existing
links carry an empty op queue.

Revision ID: d7e8f9a0b1c2
Revises: f2a3b4c5d6e7
Create Date: 2026-06-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d7e8f9a0b1c2"
down_revision: str | None = "ce079d08b457"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crm_lead_links",
        sa.Column("pending_field_ops", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("crm_lead_links", "pending_field_ops")
