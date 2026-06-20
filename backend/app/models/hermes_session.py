from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class HermesSessionRecord(Base):
    __tablename__ = "hermes_sessions"
    __table_args__ = (
        UniqueConstraint("hermes_session_id", name="uq_hermes_sessions_session_id"),
        Index("ix_hermes_sessions_workspace_agent_session", "workspace_id", "agent_session_id"),
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
    hermes_session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="oqim", server_default="oqim")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    token_counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ended_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class HermesSessionMessageRecord(Base):
    __tablename__ = "hermes_session_messages"
    __table_args__ = (
        UniqueConstraint("hermes_session_id", "sequence", name="uq_hermes_session_messages_sequence"),
        Index("ix_hermes_session_messages_session_created", "hermes_session_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hermes_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("hermes_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
