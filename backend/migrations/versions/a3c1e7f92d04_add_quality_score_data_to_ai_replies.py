"""add quality_score_data to ai_replies for finalizer stage

Revision ID: a3c1e7f92d04
Revises: f96fc6aec185
Create Date: 2026-04-08 17:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c1e7f92d04'
down_revision: Union[str, None] = 'f96fc6aec185'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Stores the finalizer quality gate result per draft for trend analysis
    # and eval tracking (success criterion: <5% of drafts rated too-short).
    # Schema: {length_ok: bool, score: float, issue_flags: list[str],
    #          attempts: int, was_repaired: bool}
    op.add_column(
        'ai_replies',
        sa.Column('quality_score_data', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('ai_replies', 'quality_score_data')
