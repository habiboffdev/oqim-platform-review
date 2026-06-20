from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class Trigger(Base):
    """Durable runtime rule that starts agent work.

    Triggers are the audit-grade entry point for agent action. A trigger ties
    a workspace + owner_agent_id to an event_source (channel_message_received,
    conversation_state_changed, schedule, integration_webhook, etc.) and
    declares what kind of action proposal it produces. Permission gating uses
    `ToolGrant` (Phase 1); a trigger never bypasses scope grants.

    `idempotency_key` is computed at create time from the matching scope, so
    two identical triggers in the same workspace collapse to one row.
    """

    __tablename__ = "triggers"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_triggers_workspace_idempotency",
        ),
        Index("ix_triggers_workspace_event", "workspace_id", "event_source"),
        Index("ix_triggers_owner_agent", "workspace_id", "owner_agent_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    event_source: Mapped[str] = mapped_column(String(64), nullable=False)
    matching_scope: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    permission_mode: Mapped[str] = mapped_column(
        String(32), default="ask_always", nullable=False
    )
    action_proposal_type: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    retry_policy: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    last_run_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    run_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    audit_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
