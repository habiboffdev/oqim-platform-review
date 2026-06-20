"""Promoter outreach: campaigns + per-contact target state machine.

Person-level truth (opt-out) lives on Customer; the target carries a contact
snapshot and is the lightweight unit the drip worker drains.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class OutreachCampaign(Base):
    __tablename__ = "outreach_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("crm_connections.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    goal: Mapped[str] = mapped_column(String(64), nullable=False)
    segment_spec: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    base_message: Mapped[str] = mapped_column(Text, nullable=False)
    caps: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # overrides only
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )  # draft | approved | running | paused | completed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class OutreachTarget(Base):
    __tablename__ = "outreach_targets"
    __table_args__ = (
        UniqueConstraint("campaign_id", "phone", name="uq_outreach_targets_campaign_phone"),
        UniqueConstraint("idempotency_key", name="uq_outreach_targets_idem"),
        Index("ix_outreach_targets_scan", "state", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("outreach_campaigns.id"), nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # denormalized soft ref (matches CrmLeadLink); app-level workspace isolation
    provider_contact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # filled lazily at send
    tier: Mapped[str] = mapped_column(String(8), nullable=False)  # warm | cold
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
