"""add unique lifecycle trigger constraint to ai_replies

Revision ID: 9a2f4c7d1e88
Revises: 6e1a0d4f9c21
Create Date: 2026-04-15 16:40:00.000000
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9a2f4c7d1e88"
down_revision: Union[str, None] = "6e1a0d4f9c21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_ai_replies_lifecycle_trigger",
        "ai_replies",
        ["conversation_id", "trigger_type", "trigger_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_ai_replies_lifecycle_trigger", "ai_replies", type_="unique")
