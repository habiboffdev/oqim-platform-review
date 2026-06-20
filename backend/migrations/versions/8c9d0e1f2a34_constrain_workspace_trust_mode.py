"""constrain workspace trust mode

Revision ID: 8c9d0e1f2a34
Revises: 7b8c9d0e1f23
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8c9d0e1f2a34"
down_revision = "7b8c9d0e1f23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE workspaces
        SET trust_mode = 'draft'
        WHERE trust_mode IS NULL
           OR trust_mode = ''
           OR trust_mode IN ('manual', 'ask_always')
           OR trust_mode NOT IN ('draft', 'autonomous', 'autopilot')
        """
    )
    op.alter_column(
        "workspaces",
        "trust_mode",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default="draft",
    )
    op.create_check_constraint(
        "ck_workspaces_trust_mode_current",
        "workspaces",
        "trust_mode IN ('draft', 'autonomous', 'autopilot')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_workspaces_trust_mode_current", "workspaces", type_="check")
