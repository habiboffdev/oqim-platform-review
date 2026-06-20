from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class AgentConversationStateSnapshot(Base):
    """Compact Hermes-authored state for one Agent Session."""

    __tablename__ = "agent_conversation_state_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_agent_conversation_state_idempotency"),
        Index("ix_agent_conversation_state_session_created", "agent_session_id", "created_at"),
        Index("ix_agent_conversation_state_workspace_conversation", "workspace_id", "conversation_id"),
        Index("ix_agent_conversation_state_hermes_run", "hermes_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hermes_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stage: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown", server_default="unknown")
    active_intent: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
