from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "conversation_id", "agent_id", name="uq_agent_sessions_owner_conversation"),
        # Owner/setup sessions have no conversation_id; key them by owner chat so
        # the owner's session memory stays stable across turns (spike #439).
        Index(
            "uq_agent_sessions_owner_chat",
            "workspace_id",
            "agent_id",
            "owner_chat_id",
            unique=True,
            postgresql_where=text("conversation_id IS NULL"),
        ),
        Index("ix_agent_sessions_workspace_agent", "workspace_id", "agent_id"),
        Index("ix_agent_sessions_conversation", "conversation_id"),
        Index("ix_agent_sessions_hermes_session_id", "hermes_session_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Owner/setup channel sessions have no Conversation; they are keyed by the
    # owner's Telegram chat id instead (spike #439).
    owner_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="telegram_dm", server_default="telegram_dm")
    session_key: Mapped[str] = mapped_column(String(255), nullable=False)
    hermes_session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active", index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    last_customer_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_agent_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    events = relationship("AgentSessionEvent", back_populates="agent_session", cascade="all, delete-orphan")


class AgentSessionEvent(Base):
    __tablename__ = "agent_session_events"
    __table_args__ = (
        UniqueConstraint("agent_session_id", "sequence", name="uq_agent_session_events_sequence"),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_agent_session_events_idempotency"),
        Index("ix_agent_session_events_session_created", "agent_session_id", "created_at"),
        Index("ix_agent_session_events_message", "message_id"),
        Index("ix_agent_session_events_hermes_run", "hermes_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
    )
    agent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    direction: Mapped[str] = mapped_column(String(24), nullable=False)
    message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    hermes_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    agent_session = relationship("AgentSession", back_populates="events")
