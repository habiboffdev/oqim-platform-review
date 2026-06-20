"""add unique constraints and clean ghost records

Revision ID: 32396243054e
Revises: cd1d47d1e126
Create Date: 2026-03-24 18:40:13.882584
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '32396243054e'
down_revision: Union[str, None] = 'cd1d47d1e126'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Delete ghost customers with telegram_id = 0
    op.execute("DELETE FROM customers WHERE telegram_id = 0")

    # 2. Deduplicate customers — keep the row with the highest id
    op.execute(
        "DELETE FROM customers a USING customers b "
        "WHERE a.workspace_id = b.workspace_id "
        "AND a.telegram_id = b.telegram_id "
        "AND a.id < b.id"
    )

    # 3. Deduplicate conversations — keep the row with the highest id
    op.execute(
        "DELETE FROM conversations a USING conversations b "
        "WHERE a.workspace_id = b.workspace_id "
        "AND a.telegram_chat_id = b.telegram_chat_id "
        "AND a.telegram_chat_id IS NOT NULL "
        "AND a.id < b.id"
    )

    # 4. Add UNIQUE constraint on customers(workspace_id, telegram_id)
    op.create_unique_constraint(
        "uq_customer_workspace_telegram",
        "customers",
        ["workspace_id", "telegram_id"],
    )

    # 5. Add UNIQUE constraint on conversations(workspace_id, telegram_chat_id)
    op.create_unique_constraint(
        "uq_conversation_workspace_chat",
        "conversations",
        ["workspace_id", "telegram_chat_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_conversation_workspace_chat", "conversations", type_="unique")
    op.drop_constraint("uq_customer_workspace_telegram", "customers", type_="unique")
