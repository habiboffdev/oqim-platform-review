"""add onboarding_completed to workspaces

Revision ID: 434b262995e4
Revises: 6685f2a15204
Create Date: 2026-03-20 22:33:36.547316
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '434b262995e4'
down_revision: Union[str, None] = '6685f2a15204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workspaces', sa.Column('onboarding_completed', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    op.drop_column('workspaces', 'onboarding_completed')
