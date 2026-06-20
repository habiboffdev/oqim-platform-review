"""Reviewable SKILL.md candidate produced by the learner or an upload.

A candidate is a review artifact, not a live skill. On approval it is promoted
into the agent_skills table via AgentDocumentService.upsert_skill. status moves
proposed -> approved | rejected. source is "learned" (auto-learner) or "upload".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class LearnedSkillCandidate(Base):
    __tablename__ = "learned_skill_candidates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_learned_skill_candidates_workspace_slug"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger: Mapped[str] = mapped_column(Text, default="", nullable=False)
    action: Mapped[str] = mapped_column(Text, default="", nullable=False)
    example_phrase: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dimension: Mapped[str] = mapped_column(String(60), default="general", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_conv_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="proposed", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="learned", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
