from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.workspace import Workspace


class TrustMode(StrEnum):
    # Two states only: DISABLED (agent is off — no LLM run, no reply, no draft)
    # or AUTOPILOT (agent runs and sends). The legacy DRAFT/AUTONOMOUS modes were
    # collapsed into DISABLED (migration f1a2b3c4d5e6); neither ever auto-sent.
    DISABLED = "disabled"
    AUTOPILOT = "autopilot"


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_default: Mapped[bool] = mapped_column(default=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    # Persona
    persona: Mapped[dict] = mapped_column(JSON, default=dict)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    example_responses: Mapped[list] = mapped_column(JSON, default=list)

    # Knowledge scope
    knowledge_config: Mapped[dict] = mapped_column(
        JSON, default=lambda: {"use_catalog": True, "use_knowledge": True}
    )

    # Channel config
    channel_config: Mapped[dict] = mapped_column(
        JSON, default=lambda: {"mode": "dm", "chat_ids": []}
    )

    # Tools
    tools_config: Mapped[dict] = mapped_column(
        JSON, default=lambda: {"enabled_tools": ["knowledge_search_catalog"]}
    )

    # Trust/autonomy
    trust_mode: Mapped[str] = mapped_column(String(20), default=TrustMode.DISABLED.value)
    auto_send_threshold: Mapped[float] = mapped_column(Float, default=0.85)
    escalation_topics: Mapped[list] = mapped_column(JSON, default=list)

    # v6: Two-agent architecture
    agent_type: Mapped[str] = mapped_column(String(20), default="customer", nullable=False)
    contact_scope: Mapped[str] = mapped_column(String(20), default="business", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Relationships
    workspace: Mapped[Workspace] = relationship(back_populates="agents")
