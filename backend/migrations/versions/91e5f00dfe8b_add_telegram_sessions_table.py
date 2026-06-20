"""add telegram_sessions table

Revision ID: 91e5f00dfe8b
Revises: aad34a5ee56e
Create Date: 2026-03-20 16:15:39.163363
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '91e5f00dfe8b'
down_revision: Union[str, None] = 'aad34a5ee56e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('telegram_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('session_data', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id')
    )


def downgrade() -> None:
    op.drop_table('telegram_sessions')
