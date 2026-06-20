from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# "playbook" is a workspace-scoped selling-method document: the per-workspace
# override for the managed seller_playbook.md default (one row per workspace).
DocumentKindLiteral = Literal["business", "agent", "skill", "playbook"]
DocumentSubjectTypeLiteral = Literal["workspace", "agent", "skill"]


class AgentSkillInput(BaseModel):
    """Owner/extractor input for creating or updating a skill."""

    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    instructions: str = ""
    when_to_use: str = ""
    when_not_to_use: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    agent_id: int | None = None
    enabled: bool = True

    @field_validator("slug")
    @classmethod
    def _slug_is_kebab(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("slug must not be empty")
        if any(ch.isspace() for ch in normalized):
            raise ValueError("slug must not contain whitespace")
        return normalized


class AgentSkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    agent_id: int | None
    slug: str
    name: str
    description: str
    instructions: str
    when_to_use: str
    when_not_to_use: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    tools: list[str]
    examples: list[dict[str, Any]]
    enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime


class AgentDocumentSectionInput(BaseModel):
    """Section input. The (document_kind, subject_type, subject_id, section_key)
    tuple is unique per workspace.
    """

    document_kind: DocumentKindLiteral
    subject_type: DocumentSubjectTypeLiteral
    subject_id: int | None = None
    section_key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=255)
    body: str = ""
    order_index: int = 0
    source_evidence: list[dict[str, Any]] = Field(default_factory=list)
    generated_by: str = "system"

    def model_post_init(self, _context: Any) -> None:
        if self.subject_type == "workspace" and self.subject_id is not None:
            raise ValueError("subject_id must be null when subject_type='workspace'")
        if self.subject_type != "workspace" and self.subject_id is None:
            raise ValueError("subject_id is required when subject_type is 'agent' or 'skill'")
        kind_to_subject = {
            "business": "workspace",
            "agent": "agent",
            "skill": "skill",
            "playbook": "workspace",
        }
        if kind_to_subject[self.document_kind] != self.subject_type:
            raise ValueError(
                f"document_kind={self.document_kind} requires subject_type="
                f"{kind_to_subject[self.document_kind]}"
            )


class AgentDocumentSectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    document_kind: str
    subject_type: str
    subject_id: int | None
    section_key: str
    title: str
    body: str
    order_index: int
    source_evidence: list[dict[str, Any]]
    generated_by: str
    created_at: datetime
    updated_at: datetime


class RenderedDocument(BaseModel):
    """Markdown view derived from sections + (optionally) agent/skill rows."""

    kind: DocumentKindLiteral
    subject_id: int | None
    title: str
    markdown: str
    sections_used: int
