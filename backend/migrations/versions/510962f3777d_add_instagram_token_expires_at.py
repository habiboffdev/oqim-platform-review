"""add instagram_token_expires_at to workspaces

Revises: b63224555714
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "510962f3777d"
down_revision = "b63224555714"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("instagram_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "instagram_token_expires_at")
