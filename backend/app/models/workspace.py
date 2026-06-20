from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.conversation import Conversation
    from app.models.customer import Customer


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "trust_mode IN ('disabled', 'autopilot')",
            name="ck_workspaces_trust_mode_current",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    type: Mapped[str] = mapped_column(String(50), default="ecommerce")
    monthly_revenue_band: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    onboarding_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pipeline_stages: Mapped[list] = mapped_column(
        JSON, default=lambda: ["new", "qualified", "negotiation", "won", "lost"]
    )
    working_hours: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    subscription_tier: Mapped[str] = mapped_column(String(20), default="free")
    trust_mode: Mapped[str] = mapped_column(String(20), default="disabled", server_default="disabled", nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    telegram_connected: Mapped[bool] = mapped_column(default=False)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    # Owner control bot: Telegram chat where this workspace's owner receives
    # agent-created notifications and Approve/Reject cards (bound via the bot).
    owner_control_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    # Bot API token of this workspace's self-provisioned control bot (created
    # by OQIM itself via the BotFather conversation — see telegram_control_bot/
    # provisioner.py). Pilot-grade plaintext storage; encrypt before multi-tenant GA.
    control_bot_token: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    control_bot_username: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    # The control bot's Telegram user id — the persist consumer drops inbound
    # events from this peer so the bot is never ingested as a customer.
    control_bot_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    onboarding_completed: Mapped[bool] = mapped_column(default=False)
    corrections_since_refresh: Mapped[int] = mapped_column(Integer, default=0)
    # Instagram Business API
    instagram_connected: Mapped[bool] = mapped_column(default=False)
    # Instagram-Login exposes two ids for one account and Meta's docs don't say
    # which the webhook entry.id carries (a documented, unresolved mismatch), so
    # we store BOTH and the webhook resolver matches either:
    #   instagram_page_id    = /me user_id
    #   instagram_account_id = /me id
    instagram_page_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    instagram_account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    instagram_access_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    instagram_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Telegram Business Bot API (auto-send only)
    telegram_business_bot_connected: Mapped[bool] = mapped_column(default=False)
    business_connection_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Relationships
    agents: Mapped[list[Agent]] = relationship(back_populates="workspace")
    customers: Mapped[list[Customer]] = relationship(back_populates="workspace")
    conversations: Mapped[list[Conversation]] = relationship(back_populates="workspace")
