"""voice_profile_rebuild_columns

Revision ID: cd1d47d1e126
Revises: 7f0194b032ce
Create Date: 2026-03-24 16:28:14.598484
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'cd1d47d1e126'
down_revision: Union[str, None] = '7f0194b032ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # VoiceProfile: 4 new columns for voice profile rebuild
    op.add_column('voice_profiles', sa.Column('quality_score', sa.String(20), server_default='weak', nullable=False))
    op.add_column('voice_profiles', sa.Column('anti_patterns', sa.JSON(), server_default='[]', nullable=False))
    op.add_column('voice_profiles', sa.Column('delay_profiles', sa.JSON(), server_default='{}', nullable=False))
    op.add_column('voice_profiles', sa.Column('language_rules', sa.JSON(), server_default='{}', nullable=False))

    # Workspace: correction counter for auto-refresh trigger
    op.add_column('workspaces', sa.Column('corrections_since_refresh', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('workspaces', 'corrections_since_refresh')
    op.drop_column('voice_profiles', 'language_rules')
    op.drop_column('voice_profiles', 'delay_profiles')
    op.drop_column('voice_profiles', 'anti_patterns')
    op.drop_column('voice_profiles', 'quality_score')
