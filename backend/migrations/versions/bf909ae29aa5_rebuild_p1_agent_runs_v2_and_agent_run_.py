"""rebuild-p1: agent_runs_v2 and agent_run_events_v2

Revision ID: bf909ae29aa5
Revises: 1981a8aa5adb
Create Date: 2026-05-20 10:55:07.842316
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'bf909ae29aa5'
down_revision: Union[str, None] = '1981a8aa5adb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create agent_runs_v2 first — agent_run_events_v2 FK depends on it.
    op.create_table(
        'agent_runs_v2',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('workspace_id', sa.BigInteger(), nullable=False),
        sa.Column('agent_id', sa.BigInteger(), nullable=False),
        sa.Column('adk_session_id', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_agent_runs_v2_adk_session_id'), 'agent_runs_v2', ['adk_session_id'], unique=False)
    op.create_index(op.f('ix_agent_runs_v2_agent_id'), 'agent_runs_v2', ['agent_id'], unique=False)
    op.create_index(op.f('ix_agent_runs_v2_workspace_id'), 'agent_runs_v2', ['workspace_id'], unique=False)

    # Create agent_run_events_v2 after agent_runs_v2 (FK: agent_run_id → agent_runs_v2.id).
    op.create_table(
        'agent_run_events_v2',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('agent_run_id', sa.BigInteger(), nullable=False),
        sa.Column('workspace_id', sa.BigInteger(), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('visibility', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=64), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(['agent_run_id'], ['agent_runs_v2.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_agent_run_events_v2_agent_run_id'), 'agent_run_events_v2', ['agent_run_id'], unique=False)
    op.create_index(op.f('ix_agent_run_events_v2_workspace_id'), 'agent_run_events_v2', ['workspace_id'], unique=False)


def downgrade() -> None:
    # Drop agent_run_events_v2 first — it holds the FK to agent_runs_v2.
    op.drop_index(op.f('ix_agent_run_events_v2_workspace_id'), table_name='agent_run_events_v2')
    op.drop_index(op.f('ix_agent_run_events_v2_agent_run_id'), table_name='agent_run_events_v2')
    op.drop_table('agent_run_events_v2')

    op.drop_index(op.f('ix_agent_runs_v2_workspace_id'), table_name='agent_runs_v2')
    op.drop_index(op.f('ix_agent_runs_v2_agent_id'), table_name='agent_runs_v2')
    op.drop_index(op.f('ix_agent_runs_v2_adk_session_id'), table_name='agent_runs_v2')
    op.drop_table('agent_runs_v2')
