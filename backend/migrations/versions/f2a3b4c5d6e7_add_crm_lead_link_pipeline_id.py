"""add crm_lead_links.pipeline_id

Configurable multi-CRM S1 (#437): a lead is pinned to its pipeline so a
non-default pipeline's stage ladder is used on reconcile. Nullable, no backfill —
existing links resolve to the connection's default pipeline via the read shim.

Revision ID: f2a3b4c5d6e7
Revises: c3d4e5f6a7b8
Create Date: 2026-06-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crm_lead_links",
        sa.Column("pipeline_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("crm_lead_links", "pipeline_id")
