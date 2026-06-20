"""add trace_data to ai_replies for draft trace logging

Merges d5e6f7a8b9c0 (indexes) + a3c1e7f92d04 (quality_score_data) into single head.

Revision ID: c9d8e7f6a5b4
Revises: d5e6f7a8b9c0, a3c1e7f92d04
Create Date: 2026-04-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c9d8e7f6a5b4"
down_revision: Union[str, tuple] = ("d5e6f7a8b9c0", "a3c1e7f92d04")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Stores structured trace events captured during draft generation.
    # Schema: {"debug": {...}, "events": [{sequence, at, stage, event, ...}, ...]}
    # Populated by draft_trace_session context in agent.py.
    # Used by /api/ai-replies/{id}/trace — dev/debug panel only.
    op.add_column(
        "ai_replies",
        sa.Column("trace_data", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_replies", "trace_data")
