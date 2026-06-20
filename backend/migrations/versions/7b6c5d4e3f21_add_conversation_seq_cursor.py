"""add conversation sequence cursor

Revision ID: 7b6c5d4e3f21
Revises: 4c2d8a9b7e11
Create Date: 2026-04-22 16:35:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7b6c5d4e3f21"
down_revision = "4c2d8a9b7e11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("message_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("messages", sa.Column("conversation_seq", sa.Integer(), nullable=True))

    op.execute(
        """
        WITH ordered AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY conversation_id
                    ORDER BY COALESCE(telegram_timestamp, created_at), id
                ) AS seq
            FROM messages
        )
        UPDATE messages
        SET conversation_seq = ordered.seq
        FROM ordered
        WHERE messages.id = ordered.id
        """
    )
    op.execute(
        """
        UPDATE conversations
        SET message_sequence = conversation_max.max_seq
        FROM (
            SELECT conversation_id, MAX(conversation_seq) AS max_seq
            FROM messages
            GROUP BY conversation_id
        ) AS conversation_max
        WHERE conversations.id = conversation_max.conversation_id
        """
    )
    op.create_index(
        "ix_messages_conversation_id_conversation_seq",
        "messages",
        ["conversation_id", "conversation_seq"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_id_conversation_seq", table_name="messages")
    op.drop_column("messages", "conversation_seq")
    op.drop_column("conversations", "message_sequence")
