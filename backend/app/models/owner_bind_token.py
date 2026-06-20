"""Owner-bot bind tokens (#451): one-time deep-link tokens that bind the human
owner's Telegram chat to a workspace, plus an append-only bind audit log.

The owner taps a single-use ``t.me/<bot>?start=<token>`` link minted in the web
app; the control bot redeems it on the workspace's dedicated lane. The token is
scoped to one workspace AND must arrive on that workspace's lane to bind."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class OwnerBindToken(Base):
    """A single-use, expiring bind token scoped to one workspace."""

    __tablename__ = "owner_bind_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bound_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class OwnerBindEvent(Base):
    """Append-only audit of bind lifecycle events: mint, bind, failed_bind,
    unbind, rebind."""

    __tablename__ = "owner_bind_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lane_workspace_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    token_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
