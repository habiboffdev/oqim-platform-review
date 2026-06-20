from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now


class ConversationTurnSession(Base):
    """Durable concurrency boundary for one live customer-visible reply turn."""

    __tablename__ = "conversation_turn_sessions"
    __table_args__ = (
        Index(
            "uq_conversation_turn_sessions_active",
            "workspace_id",
            "conversation_id",
            "agent_id",
            unique=True,
            postgresql_where=text(
                "state IN ('open', 'starting', 'running', 'finalizing', 'continued')"
            ),
        ),
        Index(
            "ix_conversation_turn_sessions_conversation_state",
            "workspace_id",
            "conversation_id",
            "state",
        ),
        Index("ix_conversation_turn_sessions_turn_key", "turn_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="telegram_dm", server_default="telegram_dm")
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="open", index=True)
    turn_key: Mapped[str] = mapped_column(String(255), nullable=False)
    turn_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    first_customer_message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    latest_customer_message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    latest_customer_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # transient "yozmoqda..." signal from the sidecar: the lease holds while
    # the customer is typing so bursts coalesce without a long fixed window
    latest_customer_typing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_hermes_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active_engine_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_steer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    steer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_model_observed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finalized_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Bounded-retry guard: a turn that keeps failing dispatch is quarantined
    # instead of being re-leased forever (poisoned-turn crash-loop, #415). The
    # lease path must NOT reset this — it persists across lease cycles.
    failed_dispatch_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    stale_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    conversation = relationship("Conversation")
    first_customer_message = relationship("Message", foreign_keys=[first_customer_message_id])
    latest_customer_message = relationship("Message", foreign_keys=[latest_customer_message_id])
