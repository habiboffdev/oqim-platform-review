"""phase2: canonical Hermes run tables

Revision ID: 1d2e3f4a5b6c
Revises: 0f3be6c1d29e
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "1d2e3f4a5b6c"
down_revision = "0f3be6c1d29e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(op.f("ix_agent_run_events_v2_workspace_id"), table_name="agent_run_events_v2")
    op.drop_index(op.f("ix_agent_run_events_v2_agent_run_id"), table_name="agent_run_events_v2")
    op.drop_index(op.f("ix_agent_runs_v2_workspace_id"), table_name="agent_runs_v2")
    op.drop_index(op.f("ix_agent_runs_v2_agent_id"), table_name="agent_runs_v2")
    op.drop_index(op.f("ix_agent_runs_v2_adk_session_id"), table_name="agent_runs_v2")

    op.rename_table("agent_runs_v2", "hermes_runs")
    op.rename_table("agent_run_events_v2", "hermes_run_events")
    op.alter_column("hermes_runs", "adk_session_id", new_column_name="engine_run_id")
    op.alter_column("hermes_runs", "status", new_column_name="state")
    op.alter_column("hermes_runs", "error", new_column_name="error_message")
    op.alter_column("hermes_run_events", "agent_run_id", new_column_name="hermes_run_id")
    op.alter_column("hermes_runs", "engine_run_id", nullable=True)

    op.add_column("hermes_runs", sa.Column("run_id", sa.String(length=128), nullable=True))
    op.add_column("hermes_runs", sa.Column("tenant_id", sa.BigInteger(), nullable=True))
    op.add_column("hermes_runs", sa.Column("agent_kind", sa.String(length=80), server_default="agent", nullable=False))
    op.add_column(
        "hermes_runs",
        sa.Column("lane", sa.String(length=40), server_default="fast_interactive", nullable=False),
    )
    op.add_column("hermes_runs", sa.Column("run_mode", sa.String(length=40), server_default="reply", nullable=False))
    op.add_column("hermes_runs", sa.Column("trigger_type", sa.String(length=80), server_default="legacy", nullable=False))
    op.add_column("hermes_runs", sa.Column("trigger_id", sa.String(length=255), nullable=True))
    op.add_column("hermes_runs", sa.Column("event_id", sa.String(length=255), nullable=True))
    op.add_column("hermes_runs", sa.Column("conversation_id", sa.BigInteger(), nullable=True))
    op.add_column("hermes_runs", sa.Column("customer_id", sa.BigInteger(), nullable=True))
    op.add_column("hermes_runs", sa.Column("runtime_profile_snapshot_id", sa.String(length=255), nullable=True))
    op.add_column("hermes_runs", sa.Column("runtime_profile_cache_key", sa.String(length=255), nullable=True))
    op.add_column("hermes_runs", sa.Column("correlation_id", sa.String(length=255), nullable=True))
    op.add_column("hermes_runs", sa.Column("idempotency_key", sa.String(length=512), nullable=True))
    op.add_column("hermes_runs", sa.Column("total_latency_ms", sa.Integer(), nullable=True))
    op.add_column("hermes_runs", sa.Column("llm_latency_ms", sa.Integer(), nullable=True))
    op.add_column("hermes_runs", sa.Column("llm_calls", sa.Integer(), server_default="0", nullable=False))
    op.add_column("hermes_runs", sa.Column("tokens_in", sa.Integer(), server_default="0", nullable=False))
    op.add_column("hermes_runs", sa.Column("tokens_out", sa.Integer(), server_default="0", nullable=False))
    op.add_column("hermes_runs", sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False))
    op.add_column("hermes_runs", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("hermes_runs", sa.Column("warnings_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("hermes_runs", sa.Column("tool_errors_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("hermes_runs", sa.Column("output_action", sa.String(length=120), nullable=True))
    op.add_column("hermes_runs", sa.Column("output_ref", sa.String(length=255), nullable=True))
    op.add_column("hermes_runs", sa.Column("error_code", sa.String(length=120), nullable=True))
    op.add_column(
        "hermes_runs",
        sa.Column(
            "source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("hermes_runs", sa.Column("input_summary", sa.Text(), server_default="", nullable=False))
    op.add_column(
        "hermes_runs",
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("hermes_runs", sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.add_column("hermes_runs", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))

    op.execute("UPDATE hermes_runs SET run_id = 'hermes_run:' || id::text WHERE run_id IS NULL")
    op.execute("UPDATE hermes_runs SET tenant_id = workspace_id WHERE tenant_id IS NULL")
    op.execute("UPDATE hermes_runs SET trigger_id = COALESCE(payload->>'trigger_ref', 'legacy:' || id::text) WHERE trigger_id IS NULL")
    op.execute(
        "UPDATE hermes_runs SET correlation_id = COALESCE(payload->>'correlation_id', 'corr:legacy:' || id::text) "
        "WHERE correlation_id IS NULL"
    )
    op.execute(
        "UPDATE hermes_runs SET idempotency_key = 'hermes_run:' || workspace_id::text || ':' || "
        "COALESCE(agent_id::text, 'system') || ':' || trigger_type || ':' || trigger_id || ':' || run_mode "
        "WHERE idempotency_key IS NULL"
    )
    op.alter_column("hermes_runs", "run_id", nullable=False)
    op.alter_column("hermes_runs", "trigger_id", nullable=False)
    op.alter_column("hermes_runs", "correlation_id", nullable=False)
    op.alter_column("hermes_runs", "idempotency_key", nullable=False)
    op.alter_column("hermes_runs", "agent_id", nullable=True)
    op.alter_column("hermes_runs", "started_at", nullable=True)

    op.add_column("hermes_run_events", sa.Column("run_id", sa.String(length=128), nullable=True))
    op.add_column("hermes_run_events", sa.Column("event_id", sa.String(length=255), nullable=True))
    op.add_column("hermes_run_events", sa.Column("sequence", sa.Integer(), nullable=True))
    op.add_column("hermes_run_events", sa.Column("owner_label", sa.String(length=240), server_default="", nullable=False))
    op.add_column("hermes_run_events", sa.Column("owner_detail", sa.Text(), server_default="", nullable=False))
    op.add_column("hermes_run_events", sa.Column("tool_name", sa.String(length=120), nullable=True))
    op.add_column("hermes_run_events", sa.Column("tool_state", sa.String(length=80), nullable=True))
    op.add_column("hermes_run_events", sa.Column("action_proposal_id", sa.String(length=255), nullable=True))
    op.add_column("hermes_run_events", sa.Column("correlation_id", sa.String(length=255), nullable=True))
    op.add_column("hermes_run_events", sa.Column("idempotency_key", sa.String(length=512), nullable=True))
    op.add_column(
        "hermes_run_events",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.execute(
        "UPDATE hermes_run_events e SET run_id = r.run_id "
        "FROM hermes_runs r WHERE e.hermes_run_id = r.id"
    )
    op.execute("UPDATE hermes_run_events SET event_id = 'hermes_run_event:' || id::text WHERE event_id IS NULL")
    op.execute("UPDATE hermes_run_events SET sequence = id WHERE sequence IS NULL")
    op.execute("UPDATE hermes_run_events SET correlation_id = 'corr:legacy:event:' || id::text WHERE correlation_id IS NULL")
    op.execute("UPDATE hermes_run_events SET idempotency_key = 'hermes_run_event:' || workspace_id::text || ':' || id::text WHERE idempotency_key IS NULL")
    op.alter_column("hermes_run_events", "run_id", nullable=False)
    op.alter_column("hermes_run_events", "event_id", nullable=False)
    op.alter_column("hermes_run_events", "sequence", nullable=False)
    op.alter_column("hermes_run_events", "correlation_id", nullable=False)
    op.alter_column("hermes_run_events", "idempotency_key", nullable=False)

    op.create_unique_constraint("uq_hermes_runs_idempotency_key", "hermes_runs", ["idempotency_key"])
    op.create_index("ix_hermes_runs_run_id", "hermes_runs", ["run_id"], unique=True)
    op.create_index("ix_hermes_runs_tenant_id", "hermes_runs", ["tenant_id"])
    op.create_index("ix_hermes_runs_workspace_created", "hermes_runs", ["workspace_id", "created_at"])
    op.create_index("ix_hermes_runs_workspace_lane_state", "hermes_runs", ["workspace_id", "lane", "state", "created_at"])
    op.create_index("ix_hermes_runs_trigger", "hermes_runs", ["trigger_type", "trigger_id", "run_mode"])
    op.create_index("ix_hermes_runs_conversation_created", "hermes_runs", ["conversation_id", "created_at"])
    op.create_index("ix_hermes_runs_agent_created", "hermes_runs", ["agent_id", "created_at"])
    op.create_index("ix_hermes_runs_engine_run_id", "hermes_runs", ["engine_run_id"])
    op.create_index("ix_hermes_runs_event_id", "hermes_runs", ["event_id"])
    op.create_index("ix_hermes_runs_customer_id", "hermes_runs", ["customer_id"])
    op.create_index("ix_hermes_runs_correlation_id", "hermes_runs", ["correlation_id"])
    op.create_index("ix_hermes_runs_state", "hermes_runs", ["state"])
    op.create_index("ix_hermes_runs_lane", "hermes_runs", ["lane"])
    op.create_index("ix_hermes_runs_run_mode", "hermes_runs", ["run_mode"])
    op.create_index("ix_hermes_runs_trigger_type", "hermes_runs", ["trigger_type"])
    op.create_index("ix_hermes_runs_trigger_id", "hermes_runs", ["trigger_id"])

    op.create_unique_constraint("uq_hermes_run_events_workspace_event", "hermes_run_events", ["workspace_id", "event_id"])
    op.create_index("ix_hermes_run_events_hermes_run_id", "hermes_run_events", ["hermes_run_id"])
    op.create_index("ix_hermes_run_events_run_id", "hermes_run_events", ["run_id"])
    op.create_index("ix_hermes_run_events_workspace_id", "hermes_run_events", ["workspace_id"])
    op.create_index("ix_hermes_run_events_action_proposal_id", "hermes_run_events", ["action_proposal_id"])
    op.create_index("ix_hermes_run_events_correlation_id", "hermes_run_events", ["correlation_id"])
    op.create_index("ix_hermes_run_events_idempotency_key", "hermes_run_events", ["idempotency_key"])

    op.create_table(
        "hermes_autopilot_circuit_breakers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reason", sa.String(length=120), nullable=False, server_default="operator_disabled"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("scope_type", "scope_id", name="uq_hermes_autopilot_breaker_scope"),
    )
    op.create_index("ix_hermes_autopilot_circuit_breakers_scope_type", "hermes_autopilot_circuit_breakers", ["scope_type"])
    op.create_index("ix_hermes_autopilot_circuit_breakers_scope_id", "hermes_autopilot_circuit_breakers", ["scope_id"])
    op.create_index("ix_hermes_autopilot_circuit_breakers_active", "hermes_autopilot_circuit_breakers", ["active"])


def downgrade() -> None:
    op.drop_index("ix_hermes_autopilot_circuit_breakers_active", table_name="hermes_autopilot_circuit_breakers")
    op.drop_index("ix_hermes_autopilot_circuit_breakers_scope_id", table_name="hermes_autopilot_circuit_breakers")
    op.drop_index("ix_hermes_autopilot_circuit_breakers_scope_type", table_name="hermes_autopilot_circuit_breakers")
    op.drop_table("hermes_autopilot_circuit_breakers")

    op.drop_index("ix_hermes_run_events_idempotency_key", table_name="hermes_run_events")
    op.drop_index("ix_hermes_run_events_correlation_id", table_name="hermes_run_events")
    op.drop_index("ix_hermes_run_events_action_proposal_id", table_name="hermes_run_events")
    op.drop_index("ix_hermes_run_events_workspace_id", table_name="hermes_run_events")
    op.drop_index("ix_hermes_run_events_run_id", table_name="hermes_run_events")
    op.drop_index("ix_hermes_run_events_hermes_run_id", table_name="hermes_run_events")
    op.drop_constraint("uq_hermes_run_events_workspace_event", "hermes_run_events", type_="unique")

    op.drop_index("ix_hermes_runs_trigger_id", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_trigger_type", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_run_mode", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_lane", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_state", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_correlation_id", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_customer_id", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_event_id", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_engine_run_id", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_agent_created", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_conversation_created", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_trigger", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_workspace_lane_state", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_workspace_created", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_tenant_id", table_name="hermes_runs")
    op.drop_index("ix_hermes_runs_run_id", table_name="hermes_runs")
    op.drop_constraint("uq_hermes_runs_idempotency_key", "hermes_runs", type_="unique")

    op.drop_column("hermes_run_events", "created_at")
    op.drop_column("hermes_run_events", "idempotency_key")
    op.drop_column("hermes_run_events", "correlation_id")
    op.drop_column("hermes_run_events", "action_proposal_id")
    op.drop_column("hermes_run_events", "tool_state")
    op.drop_column("hermes_run_events", "tool_name")
    op.drop_column("hermes_run_events", "owner_detail")
    op.drop_column("hermes_run_events", "owner_label")
    op.drop_column("hermes_run_events", "sequence")
    op.drop_column("hermes_run_events", "event_id")
    op.drop_column("hermes_run_events", "run_id")

    op.drop_column("hermes_runs", "updated_at")
    op.drop_column("hermes_runs", "created_at")
    op.drop_column("hermes_runs", "details")
    op.drop_column("hermes_runs", "input_summary")
    op.drop_column("hermes_runs", "source_refs")
    op.drop_column("hermes_runs", "error_code")
    op.drop_column("hermes_runs", "output_ref")
    op.drop_column("hermes_runs", "output_action")
    op.drop_column("hermes_runs", "tool_errors_count")
    op.drop_column("hermes_runs", "warnings_count")
    op.drop_column("hermes_runs", "confidence")
    op.drop_column("hermes_runs", "total_tokens")
    op.drop_column("hermes_runs", "tokens_out")
    op.drop_column("hermes_runs", "tokens_in")
    op.drop_column("hermes_runs", "llm_calls")
    op.drop_column("hermes_runs", "llm_latency_ms")
    op.drop_column("hermes_runs", "total_latency_ms")
    op.drop_column("hermes_runs", "idempotency_key")
    op.drop_column("hermes_runs", "correlation_id")
    op.drop_column("hermes_runs", "runtime_profile_cache_key")
    op.drop_column("hermes_runs", "runtime_profile_snapshot_id")
    op.drop_column("hermes_runs", "customer_id")
    op.drop_column("hermes_runs", "conversation_id")
    op.drop_column("hermes_runs", "event_id")
    op.drop_column("hermes_runs", "trigger_id")
    op.drop_column("hermes_runs", "trigger_type")
    op.drop_column("hermes_runs", "run_mode")
    op.drop_column("hermes_runs", "lane")
    op.drop_column("hermes_runs", "agent_kind")
    op.drop_column("hermes_runs", "tenant_id")
    op.drop_column("hermes_runs", "run_id")

    op.execute("UPDATE hermes_runs SET engine_run_id = run_id WHERE engine_run_id IS NULL")
    op.alter_column("hermes_runs", "engine_run_id", nullable=False)
    op.alter_column("hermes_runs", "started_at", nullable=False)
    op.alter_column("hermes_run_events", "hermes_run_id", new_column_name="agent_run_id")
    op.alter_column("hermes_runs", "error_message", new_column_name="error")
    op.alter_column("hermes_runs", "state", new_column_name="status")
    op.alter_column("hermes_runs", "engine_run_id", new_column_name="adk_session_id")
    op.rename_table("hermes_run_events", "agent_run_events_v2")
    op.rename_table("hermes_runs", "agent_runs_v2")

    op.create_index(op.f("ix_agent_runs_v2_adk_session_id"), "agent_runs_v2", ["adk_session_id"], unique=False)
    op.create_index(op.f("ix_agent_runs_v2_agent_id"), "agent_runs_v2", ["agent_id"], unique=False)
    op.create_index(op.f("ix_agent_runs_v2_workspace_id"), "agent_runs_v2", ["workspace_id"], unique=False)
    op.create_index(op.f("ix_agent_run_events_v2_agent_run_id"), "agent_run_events_v2", ["agent_run_id"], unique=False)
    op.create_index(op.f("ix_agent_run_events_v2_workspace_id"), "agent_run_events_v2", ["workspace_id"], unique=False)
