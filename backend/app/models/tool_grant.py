from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class ToolGrant(Base):
    """Workspace-scoped capability grant for an agent.

    A ToolGrant is the durable record that agent X in workspace Y is allowed
    to use scope Z (e.g. `telegram.send_message`). Permission checks at action
    execution time read this table; revocation flips `revoked_at` and the next
    check denies the action. Never delete rows — keep history for audit.

    Scope strings follow `<integration>.<verb>`:
      telegram.read_messages, telegram.send_message, telegram.edit_message,
      telegram.watch_channel, telegram.fetch_media, telegram.sync_history, etc.
    """

    __tablename__ = "tool_grants"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "agent_id",
            "scope",
            name="uq_tool_grants_workspace_agent_scope_active",
        ),
        Index("ix_tool_grants_workspace_scope", "workspace_id", "scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    scope: Mapped[str] = mapped_column(String(120), nullable=False)

    granted_by: Mapped[str] = mapped_column(String(64), default="owner", nullable=False)
    grant_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    audit_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    use_count: Mapped[int] = mapped_column(default=0, nullable=False)

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
