"""add ai_confidence, confirmed, frequency to business_knowledge

Revision ID: a1b2c3d4e5f6
Revises: 434b262995e4
Create Date: 2026-03-21 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '434b262995e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- business_knowledge: KB extraction support columns ---
    op.add_column('business_knowledge', sa.Column('ai_confidence', sa.Float(), nullable=True))
    op.add_column('business_knowledge', sa.Column('confirmed', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('business_knowledge', sa.Column('frequency', sa.Integer(), nullable=True))

    # --- messages: composite index for KB extractor correlated subquery ---
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_messages_conv_sender_id "
        "ON messages (conversation_id, sender_type, id)"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_messages_conv_sender_id"))
    op.drop_column('business_knowledge', 'frequency')
    op.drop_column('business_knowledge', 'confirmed')
    op.drop_column('business_knowledge', 'ai_confidence')
