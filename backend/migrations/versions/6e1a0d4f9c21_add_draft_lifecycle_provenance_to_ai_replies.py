"""add draft lifecycle provenance to ai_replies

Revision ID: 6e1a0d4f9c21
Revises: f4b8d7c2a111
Create Date: 2026-04-15 10:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6e1a0d4f9c21"
down_revision: Union[str, None] = "f4b8d7c2a111"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_replies",
        sa.Column(
            "trigger_type",
            sa.String(length=24),
            nullable=False,
            server_default="inbound_reply",
        ),
    )
    op.add_column(
        "ai_replies",
        sa.Column(
            "channel",
            sa.String(length=20),
            nullable=False,
            server_default="telegram_dm",
        ),
    )
    op.add_column(
        "ai_replies",
        sa.Column(
            "trigger_id",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE ai_replies
            SET trigger_id = CAST(trigger_message_id AS TEXT)
            WHERE trigger_id = ''
            """
        )
    )


def downgrade() -> None:
    op.drop_column("ai_replies", "trigger_id")
    op.drop_column("ai_replies", "channel")
    op.drop_column("ai_replies", "trigger_type")
