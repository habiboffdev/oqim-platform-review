"""v6.1: catalog source refs

Revision ID: 1ae34568d11f
Revises: 1bde71d7c2cb
Create Date: 2026-03-31 23:27:26.098278
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ae34568d11f'
down_revision: Union[str, None] = '1bde71d7c2cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('catalog_items', sa.Column('source_post_id', sa.Integer(), nullable=True))
    op.add_column('catalog_items', sa.Column('source_channel_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('catalog_items', 'source_channel_id')
    op.drop_column('catalog_items', 'source_post_id')
