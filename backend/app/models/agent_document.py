from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class DocumentKind(str, enum.Enum):
    BUSINESS = "business"
    AGENT = "agent"
    SKILL = "skill"


class DocumentSubjectType(str, enum.Enum):
    WORKSPACE = "workspace"
    AGENT = "agent"
    SKILL = "skill"


class AgentDocumentSection(Base):
    """Structured section that contributes to a rendered .md document.

    A BUSINESS.md is the ordered concatenation of all sections where
    `document_kind = BUSINESS` and `subject_type = WORKSPACE`. An AGENT.md
    pulls sections where `subject_type = AGENT` and `subject_id = agent.id`.
    A SKILL.md pulls sections where `subject_type = SKILL` and
    `subject_id = skill.id`.

    Sections are the source of truth; the rendered Markdown is a derived view
    (see `app.modules.agent_documents.renderer`).
    """

    __tablename__ = "agent_document_sections"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "document_kind",
            "subject_type",
            "subject_id",
            "section_key",
            name="uq_agent_document_sections_subject_key",
        ),
        Index(
            "ix_agent_document_sections_lookup",
            "workspace_id",
            "document_kind",
            "subject_type",
            "subject_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    document_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Null when subject_type='workspace'; otherwise the agent_id or skill_id
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    section_key: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    source_evidence: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(40), default="system", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
