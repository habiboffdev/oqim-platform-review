"""Add missing performance indexes for common query patterns.

Covers: tasks, business_knowledge, ai_replies, messages, draft_actions.
These tables had no indexes on their primary query filter columns.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-04-08
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tasks: every list query filters by workspace_id + status
    op.create_index(
        "ix_tasks_workspace_status",
        "tasks",
        ["workspace_id", "status"],
    )

    # business_knowledge: every retrieval query filters by workspace_id + is_active
    op.create_index(
        "ix_business_knowledge_workspace_active",
        "business_knowledge",
        ["workspace_id", "is_active"],
    )

    # ai_replies: draft inbox query on every WS push filters conversation_id + status
    op.create_index(
        "ix_ai_replies_conversation_status",
        "ai_replies",
        ["conversation_id", "status"],
    )

    # messages: pagination queries order by created_at within conversation_id
    op.create_index(
        "ix_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at"],
    )

    # draft_actions: FK lookup by ai_reply_id (no existing index)
    op.create_index(
        "ix_draft_actions_ai_reply_id",
        "draft_actions",
        ["ai_reply_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_draft_actions_ai_reply_id", table_name="draft_actions")
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_index("ix_ai_replies_conversation_status", table_name="ai_replies")
    op.drop_index("ix_business_knowledge_workspace_active", table_name="business_knowledge")
    op.drop_index("ix_tasks_workspace_status", table_name="tasks")
