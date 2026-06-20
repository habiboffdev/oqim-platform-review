"""Typed CRM connection layer + per-conversation lead links.

Provider-neutral from day one (a second CRM is approved scope): credentials live
here, not on ``Workspace``. ``CrmLeadLink`` is the desired-state row the
deterministic hooks write and the supervised ``CrmSyncWorker`` reconciles —
one lead per conversation, enforced by a unique constraint, not code discipline.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    pass


class CrmConnection(Base):
    __tablename__ = "crm_connections"
    __table_args__ = (
        # one ACTIVE connection per workspace; disconnected/degraded rows kept
        # for audit and do not block a reconnect.
        Index(
            "uq_crm_connections_workspace_active",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # the same external CRM account can't be actively bound to two workspaces.
        Index(
            "uq_crm_connections_provider_account_active",
            "provider",
            "provider_account_ref",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        UniqueConstraint("webhook_token", name="uq_crm_connections_webhook_token"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # "amocrm"
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )  # active | disconnected | degraded
    provider_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    # amoCRM access tokens are JWTs > 900 chars and refresh tokens are single-use
    # — Text, never String(500) (would truncate).
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pipeline_config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    webhook_token: Mapped[str] = mapped_column(String(64), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class CrmLeadLink(Base):
    __tablename__ = "crm_lead_links"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "conversation_id",
            name="uq_crm_lead_links_connection_conversation",
        ),
        Index("ix_crm_lead_links_scan", "sync_state", "next_attempt_at"),
        Index("ix_crm_lead_links_provider_lead", "connection_id", "provider_lead_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("crm_connections.id"), nullable=False, index=True
    )
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # desired state (written by hooks, monotonic over ROLE_ORDER)
    desired_stage_role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="new", server_default="new"
    )
    stage_authority: Mapped[str] = mapped_column(
        String(8), nullable=False, default="oqim", server_default="oqim"
    )  # oqim | human (human-touch latch)
    # which pipeline this lead lives in (S1 #437). Null = legacy / default pipeline;
    # the read shim resolves null to the connection's default_pipeline_id.
    pipeline_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_notes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # reconciliation bookkeeping (owned by the worker)
    sync_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )  # pending | synced | degraded
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # provider-side ids + observed state
    provider_lead_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_contact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synced_stage_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_synced_stage_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_observed_stage_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synced_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    synced_value: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    pending_tasks: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # S4: worker-drained custom-field + tag ops the records-agent queues. Each op
    # is {"kind":"custom_field","field_id":str,"value":Any} or {"kind":"tag","name":str}.
    pending_field_ops: Mapped[list] = mapped_column(
        JSONB, default=list, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
