"""Workspace-scoped CRUD for skills and document sections.

All queries filter by ``workspace_id``. The service raises ``ValueError`` for
input violations (cross-workspace agent_id, conflicting subject_type) and lets
the database raise on unique-constraint violations so the caller can map them
to HTTP semantics.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_document import AgentDocumentSection
from app.models.agent_skill import AgentSkill
from app.modules.agent_documents.contracts import (
    AgentDocumentSectionInput,
    AgentDocumentSectionRead,
    AgentSkillInput,
    AgentSkillRead,
)


class AgentDocumentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- skills ---------------------------------------------------------

    async def upsert_skill(
        self, *, workspace_id: int, payload: AgentSkillInput
    ) -> AgentSkillRead:
        if payload.agent_id is not None:
            agent = await self._session.get(Agent, payload.agent_id)
            if agent is None or agent.workspace_id != workspace_id:
                raise ValueError("agent_id does not belong to this workspace")

        existing = await self._session.scalar(
            select(AgentSkill).where(
                AgentSkill.workspace_id == workspace_id,
                AgentSkill.slug == payload.slug,
            )
        )
        if existing is None:
            skill = AgentSkill(
                workspace_id=workspace_id,
                agent_id=payload.agent_id,
                slug=payload.slug,
                name=payload.name,
                description=payload.description,
                instructions=payload.instructions,
                when_to_use=payload.when_to_use,
                when_not_to_use=payload.when_not_to_use,
                input_schema=payload.input_schema,
                output_schema=payload.output_schema,
                tools=payload.tools,
                examples=payload.examples,
                enabled=payload.enabled,
            )
            self._session.add(skill)
            await self._session.flush()
            return AgentSkillRead.model_validate(skill)

        existing.agent_id = payload.agent_id
        existing.name = payload.name
        existing.description = payload.description
        existing.instructions = payload.instructions
        existing.when_to_use = payload.when_to_use
        existing.when_not_to_use = payload.when_not_to_use
        existing.input_schema = payload.input_schema
        existing.output_schema = payload.output_schema
        existing.tools = payload.tools
        existing.examples = payload.examples
        existing.enabled = payload.enabled
        existing.version += 1
        await self._session.flush()
        return AgentSkillRead.model_validate(existing)

    async def list_skills(
        self, *, workspace_id: int, agent_id: int | None = None
    ) -> list[AgentSkillRead]:
        query = select(AgentSkill).where(AgentSkill.workspace_id == workspace_id)
        if agent_id is not None:
            query = query.where(AgentSkill.agent_id == agent_id)
        query = query.order_by(AgentSkill.slug)
        result = await self._session.scalars(query)
        return [AgentSkillRead.model_validate(skill) for skill in result.all()]

    async def delete_skill(self, *, workspace_id: int, slug: str) -> bool:
        existing = await self._session.scalar(
            select(AgentSkill).where(
                AgentSkill.workspace_id == workspace_id, AgentSkill.slug == slug
            )
        )
        if existing is None:
            return False
        await self._session.delete(existing)
        await self._session.flush()
        return True

    # --- sections -------------------------------------------------------

    async def upsert_section(
        self, *, workspace_id: int, payload: AgentDocumentSectionInput
    ) -> AgentDocumentSectionRead:
        if payload.subject_type == "agent":
            agent = await self._session.get(Agent, payload.subject_id)
            if agent is None or agent.workspace_id != workspace_id:
                raise ValueError("subject_id does not point at an agent in this workspace")
        elif payload.subject_type == "skill":
            skill = await self._session.get(AgentSkill, payload.subject_id)
            if skill is None or skill.workspace_id != workspace_id:
                raise ValueError("subject_id does not point at a skill in this workspace")

        query = select(AgentDocumentSection).where(
            AgentDocumentSection.workspace_id == workspace_id,
            AgentDocumentSection.document_kind == payload.document_kind,
            AgentDocumentSection.subject_type == payload.subject_type,
            AgentDocumentSection.section_key == payload.section_key,
        )
        if payload.subject_id is None:
            query = query.where(AgentDocumentSection.subject_id.is_(None))
        else:
            query = query.where(AgentDocumentSection.subject_id == payload.subject_id)

        existing = await self._session.scalar(query)
        if existing is None:
            section = AgentDocumentSection(
                workspace_id=workspace_id,
                document_kind=payload.document_kind,
                subject_type=payload.subject_type,
                subject_id=payload.subject_id,
                section_key=payload.section_key,
                title=payload.title,
                body=payload.body,
                order_index=payload.order_index,
                source_evidence=payload.source_evidence,
                generated_by=payload.generated_by,
            )
            self._session.add(section)
            await self._session.flush()
            return AgentDocumentSectionRead.model_validate(section)

        existing.title = payload.title
        existing.body = payload.body
        existing.order_index = payload.order_index
        existing.source_evidence = payload.source_evidence
        existing.generated_by = payload.generated_by
        await self._session.flush()
        return AgentDocumentSectionRead.model_validate(existing)

    async def list_sections(
        self,
        *,
        workspace_id: int,
        document_kind: str,
        subject_type: str,
        subject_id: int | None,
    ) -> list[AgentDocumentSectionRead]:
        query = select(AgentDocumentSection).where(
            AgentDocumentSection.workspace_id == workspace_id,
            AgentDocumentSection.document_kind == document_kind,
            AgentDocumentSection.subject_type == subject_type,
        )
        if subject_id is None:
            query = query.where(AgentDocumentSection.subject_id.is_(None))
        else:
            query = query.where(AgentDocumentSection.subject_id == subject_id)
        query = query.order_by(
            AgentDocumentSection.order_index, AgentDocumentSection.section_key
        )
        result = await self._session.scalars(query)
        return [AgentDocumentSectionRead.model_validate(row) for row in result.all()]
