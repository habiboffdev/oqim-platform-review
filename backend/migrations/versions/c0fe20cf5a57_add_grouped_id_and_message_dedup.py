"""add_grouped_id_and_message_dedup

Revision ID: c0fe20cf5a57
Revises: 1ae34568d11f
Create Date: 2026-04-02 19:34:10.344629
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0fe20cf5a57'
down_revision: Union[str, None] = '1ae34568d11f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("grouped_id", sa.BigInteger(), nullable=True))

    # Remove duplicates before adding unique constraint (keep lowest id per pair)
    op.execute("""
        DELETE FROM messages
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM messages
            WHERE conversation_id IS NOT NULL AND telegram_message_id IS NOT NULL
            GROUP BY conversation_id, telegram_message_id
        )
        AND conversation_id IS NOT NULL
        AND telegram_message_id IS NOT NULL
    """)

    op.create_unique_constraint(
        "uq_messages_conversation_telegram_msg",
        "messages",
        ["conversation_id", "telegram_message_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_messages_conversation_telegram_msg", "messages", type_="unique")
    op.drop_column("messages", "grouped_id")
