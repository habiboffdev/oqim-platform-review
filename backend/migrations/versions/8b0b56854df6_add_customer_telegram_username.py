"""add customers.telegram_username for owner-card t.me jump links

Revision ID: 8b0b56854df6
Revises: f0d41a77c2be
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8b0b56854df6"
down_revision = "f0d41a77c2be"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("telegram_username", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("customers", "telegram_username")
