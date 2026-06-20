"""Add workspaces.control_bot_user_id — authoritative ingest guard peer id.

The workspace's own control bot must never be ingested as a customer: its
Telegram user id is stored at provisioning time so the persist consumer can
drop inbound events from that peer even when the sidecar's entity cache is
cold (the hot-path bot filter only works on cached entities — live incident
conv 4, 2026-06-10: agent<->control-bot infinite loop).

Revision ID: f0d41a77c2be
Revises: e5e803cef4c5
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f0d41a77c2be"
down_revision = "e5e803cef4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("control_bot_user_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "control_bot_user_id")
