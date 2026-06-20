"""add_catalog_variants_flagged

Revision ID: a32922c4e933
Revises: a1b2c3d4e5f6
Create Date: 2026-03-21 21:55:25.800388
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a32922c4e933'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('catalog_items', sa.Column('flagged', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('catalog_items', sa.Column('variants', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('catalog_items', 'variants')
    op.drop_column('catalog_items', 'flagged')
