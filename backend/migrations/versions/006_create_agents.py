"""Create agents table and migrate trust_config data into default agents.

Revision ID: 006_agents
Revises: 005_workspaces
Create Date: 2026-02-13 12:10:00.000000
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "006_agents"
down_revision: Union[str, None] = "005_workspaces"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create agents table
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("persona", sa.JSON(), server_default='{}', nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("example_responses", sa.JSON(), server_default='[]', nullable=False),
        sa.Column("knowledge_config", sa.JSON(), server_default='{"use_catalog": true, "use_knowledge": true}', nullable=False),
        sa.Column("channel_config", sa.JSON(), server_default='{"mode": "dm", "chat_ids": []}', nullable=False),
        sa.Column("tools_config", sa.JSON(), server_default='{"enabled_tools": ["catalog_search", "escalate"]}', nullable=False),
        sa.Column("trust_mode", sa.String(20), server_default="draft", nullable=False),
        sa.Column("auto_send_threshold", sa.Float(), server_default="0.85", nullable=False),
        sa.Column("escalation_topics", sa.JSON(), server_default='[]', nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 2. Add agent_id FK to ai_replies
    op.add_column("ai_replies", sa.Column("agent_id", sa.Integer(), nullable=True))

    # 3. Create a default agent for each workspace, pulling trust config data
    conn = op.get_bind()
    workspaces = conn.execute(sa.text("SELECT id, name FROM workspaces")).fetchall()
    for ws in workspaces:
        # Check if trust_config exists
        tc = conn.execute(
            sa.text("SELECT mode, auto_send_threshold, escalation_topics FROM trust_configs WHERE workspace_id = :wid"),
            {"wid": ws.id},
        ).fetchone()

        trust_mode = tc.mode if tc else "draft"
        threshold = tc.auto_send_threshold if tc else 0.85
        # escalation_topics comes as a Python list from JSON column — serialize to string for raw SQL
        raw_escalation = tc.escalation_topics if tc else []
        escalation_json = json.dumps(raw_escalation) if isinstance(raw_escalation, list) else str(raw_escalation)

        conn.execute(
            sa.text("""
                INSERT INTO agents (workspace_id, name, is_default, is_active, persona, instructions,
                    example_responses, knowledge_config, channel_config, tools_config,
                    trust_mode, auto_send_threshold, escalation_topics)
                VALUES (:wid, :name, true, true,
                    CAST('{"role": "sales assistant", "tone": "friendly and warm"}' AS json), NULL,
                    CAST('[]' AS json), CAST('{"use_catalog": true, "use_knowledge": true}' AS json),
                    CAST('{"mode": "dm", "chat_ids": []}' AS json),
                    CAST('{"enabled_tools": ["catalog_search", "escalate"]}' AS json),
                    :trust_mode, :threshold, CAST(:escalation AS json))
            """),
            {"wid": ws.id, "name": f"{ws.name} Assistant", "trust_mode": trust_mode, "threshold": threshold, "escalation": escalation_json},
        )

    # 4. Set agent_id on existing ai_replies (match via conversation -> workspace -> default agent)
    conn.execute(sa.text("""
        UPDATE ai_replies SET agent_id = agents.id
        FROM conversations, agents
        WHERE ai_replies.conversation_id = conversations.id
        AND agents.workspace_id = conversations.workspace_id
        AND agents.is_default = true
    """))

    # 5. Add FK constraint on ai_replies.agent_id
    op.create_foreign_key("ai_replies_agent_id_fkey", "ai_replies", "agents", ["agent_id"], ["id"])

    # 6. Drop trust_configs table
    op.drop_table("trust_configs")


def downgrade() -> None:
    # Recreate trust_configs
    op.create_table(
        "trust_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), unique=True, nullable=False),
        sa.Column("mode", sa.String(20), server_default="draft"),
        sa.Column("auto_send_threshold", sa.Float(), server_default="0.85"),
        sa.Column("escalation_topics", sa.JSON(), server_default="[]"),
        sa.Column("working_hours_start", sa.String(10), server_default="09:00"),
        sa.Column("working_hours_end", sa.String(10), server_default="18:00"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.drop_constraint("ai_replies_agent_id_fkey", "ai_replies", type_="foreignkey")
    op.drop_column("ai_replies", "agent_id")
    op.drop_table("agents")
