"""telegram_user_id to bigint

Revision ID: 6685f2a15204
Revises: cfc793d188fd
Create Date: 2026-03-20 20:38:57.142925
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '6685f2a15204'
down_revision: Union[str, None] = 'cfc793d188fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('workspaces', 'telegram_user_id',
               existing_type=sa.INTEGER(),
               type_=sa.BigInteger(),
               existing_nullable=True)


def downgrade() -> None:
    op.alter_column('workspaces', 'telegram_user_id',
               existing_type=sa.BigInteger(),
               type_=sa.INTEGER(),
               existing_nullable=True)
