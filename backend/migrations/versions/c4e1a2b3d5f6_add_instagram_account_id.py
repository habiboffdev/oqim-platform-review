"""add workspaces.instagram_account_id

Instagram-Login exposes two ids for one professional account (``user_id`` and
``id``) and Meta's docs don't say which the webhook ``entry.id`` carries — a
documented, unresolved mismatch. We already store ``user_id`` in
``instagram_page_id``; this adds ``instagram_account_id`` for the ``id`` so the
webhook resolver can match either and never drop a real DM.

Nullable, no backfill: already-connected workspaces repopulate both ids on their
next OAuth reconnect (or via a one-off owner script).

Revision ID: c4e1a2b3d5f6
Revises: ce7a81de98e8
Create Date: 2026-06-13 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4e1a2b3d5f6'
down_revision: str | None = 'ce7a81de98e8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("instagram_account_id", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "instagram_account_id")
