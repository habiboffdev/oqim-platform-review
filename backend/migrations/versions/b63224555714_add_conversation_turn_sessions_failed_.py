"""add conversation_turn_sessions.failed_dispatch_count (poisoned-turn quarantine)

Revises: 63b0f949527f
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b63224555714"
down_revision = "63b0f949527f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Bounded-retry guard for poisoned turns (#415). server_default "0"
    # backfills existing rows and keeps the NOT NULL satisfiable.
    op.add_column(
        "conversation_turn_sessions",
        sa.Column(
            "failed_dispatch_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("conversation_turn_sessions", "failed_dispatch_count")
