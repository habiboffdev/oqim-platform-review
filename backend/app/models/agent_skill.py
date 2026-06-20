from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class AgentSkill(Base):
    """Skill is a reusable capability scoped to one workspace.

    A skill may be attached to a specific agent (agent_id non-null) or stay
    workspace-level for reuse across agents (agent_id null). Skills are
    deterministic structured records; SKILL.md is rendered from them via
    `app.modules.agent_documents.renderer`.
    """

    __tablename__ = "agent_skills"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "slug",
            name="uq_agent_skills_workspace_slug",
        ),
        Index("ix_agent_skills_workspace_agent", "workspace_id", "agent_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    when_to_use: Mapped[str] = mapped_column(Text, default="", nullable=False)
    when_not_to_use: Mapped[str] = mapped_column(Text, default="", nullable=False)

    input_schema: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    output_schema: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    tools: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    examples: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
