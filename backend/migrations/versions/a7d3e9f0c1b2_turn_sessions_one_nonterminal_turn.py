"""turn sessions: one non-terminal turn per conversation ('continued' joins the unique index)

Revision ID: a7d3e9f0c1b2
Revises: 510962f3777d
Create Date: 2026-06-11 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7d3e9f0c1b2"
down_revision = "510962f3777d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Collapse existing duplicates: keep the NEWEST non-terminal turn per
    #    (workspace, conversation, agent) and complete the rest. These rows are
    #    stale residue of the 2026-06-11 duplicate-active-turn race; their
    #    conversations have long since moved on, so completing (not merging)
    #    the losers is correct for a one-time cleanup.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id, row_number() OVER (
                    PARTITION BY workspace_id, conversation_id, agent_id
                    ORDER BY id DESC
                ) AS rn
                FROM conversation_turn_sessions
                WHERE state IN ('open', 'starting', 'running', 'finalizing', 'continued')
            )
            UPDATE conversation_turn_sessions AS t
            SET state = 'completed',
                completed_at = NOW(),
                stale_reason = 'duplicate_turn_collapsed',
                updated_at = NOW()
            FROM ranked
            WHERE t.id = ranked.id AND ranked.rn > 1
            """
        )
    )
    # 2) Extend the active-turn unique index so the DB enforces at most ONE
    #    non-terminal turn per conversation triple ('continued' included).
    op.drop_index(
        "uq_conversation_turn_sessions_active",
        table_name="conversation_turn_sessions",
    )
    op.create_index(
        "uq_conversation_turn_sessions_active",
        "conversation_turn_sessions",
        ["workspace_id", "conversation_id", "agent_id"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('open', 'starting', 'running', 'finalizing', 'continued')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_conversation_turn_sessions_active",
        table_name="conversation_turn_sessions",
    )
    op.create_index(
        "uq_conversation_turn_sessions_active",
        "conversation_turn_sessions",
        ["workspace_id", "conversation_id", "agent_id"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('open', 'starting', 'running', 'finalizing')"
        ),
    )
