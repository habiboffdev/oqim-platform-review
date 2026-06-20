"""add conversation_turn_sessions.latest_customer_typing_at (typing-aware coalescing)

Revises: 8b0b56854df6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "63b0f949527f"
down_revision = "8b0b56854df6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_turn_sessions",
        sa.Column("latest_customer_typing_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_turn_sessions", "latest_customer_typing_at")
