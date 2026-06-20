"""Generate a workspace BUSINESS.md from Business Brain facts.

Reads active business_brain_facts, synthesizes the 10 BUSINESS.md sections
via app.brain.llm, persists them through the existing agent_documents
service, and renders with the existing render_business_md().
"""

from __future__ import annotations

import json
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.llm import generate_structured_json
from app.brain.llm_policy import FLASH_CHAIN
from app.brain.prompt_payload import prompt_cache_payload_for_asset
from app.brain.prompt_registry import PromptAsset, get_prompt_registry
from app.models.agent_document import AgentDocumentSection
from app.models.commercial_spine import BusinessBrainFactRecord
from app.modules.agent_documents.contracts import AgentDocumentSectionInput, RenderedDocument
from app.modules.agent_documents.renderer import render_business_md
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.brain.contracts import BUSINESS_SECTIONS, BusinessDocumentDraft, BusinessSectionDraft
from app.modules.business_brain.memory import ACTIVE_STATUSES

_SPEC_BY_KEY = {s.key: s for s in BUSINESS_SECTIONS}
_ORDER_BY_KEY = {s.key: i for i, s in enumerate(BUSINESS_SECTIONS)}

_BUSINESS_MD_PROMPT_ID = "agent_documents.business_md_synthesis"
_BUSINESS_MD_PROMPT_VERSION = "1.0.0"


def _section_instructions() -> str:
    return "\n".join(f"- {s.key}: {s.title} — {s.guidance}" for s in BUSINESS_SECTIONS)


class BusinessDocumentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build_fact_context(self, *, workspace_id: int) -> str:
        stmt = (
            select(BusinessBrainFactRecord)
            .where(
                BusinessBrainFactRecord.workspace_id == workspace_id,
                BusinessBrainFactRecord.status.in_(ACTIVE_STATUSES),
            )
            .order_by(BusinessBrainFactRecord.fact_type)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        lines: list[str] = []
        for row in rows:
            value = row.value if isinstance(row.value, dict) else {"value": row.value}
            lines.append(
                f"[{row.fact_type}] ({row.fact_id}) {json.dumps(value, ensure_ascii=False)}"
            )
        return "\n".join(lines)

    async def synthesize(self, *, workspace_id: int, fact_context: str) -> BusinessDocumentDraft:
        prompt = (
            "Business facts (evidence):\n"
            f"{fact_context or '(no facts learned yet)'}\n\n"
            "Write one draft per section. Sections:\n"
            f"{_section_instructions()}"
        )
        result: dict = await generate_structured_json(
            chain=FLASH_CHAIN,
            system=_business_md_system_prompt(),
            prompt=prompt,
            response_schema=BusinessDocumentDraft,
            operation="business_md_synthesis",
            workspace_id=workspace_id,
            prompt_cache=_business_md_prompt_cache(),
        )
        return BusinessDocumentDraft.model_validate(result)

    async def edit_section(self, *, workspace_id: int, section_key: str, body: str) -> None:
        spec = _SPEC_BY_KEY[section_key]  # KeyError propagates; API layer maps to 404
        docs = AgentDocumentService(self.session)
        await docs.upsert_section(
            workspace_id=workspace_id,
            payload=AgentDocumentSectionInput(
                document_kind="business",
                subject_type="workspace",
                subject_id=None,
                section_key=spec.key,
                title=spec.title,
                body=body,
                order_index=_ORDER_BY_KEY[spec.key],
                source_evidence=[],
                generated_by="owner",
            ),
        )

    async def persist(self, *, workspace_id: int, draft: BusinessDocumentDraft) -> None:
        existing = await self._load_sections(workspace_id=workspace_id)
        owner_locked = {s.section_key for s in existing if s.generated_by == "owner"}
        docs = AgentDocumentService(self.session)
        for d in draft.sections:
            spec = _SPEC_BY_KEY.get(d.section_key)
            if spec is None or spec.key in owner_locked:
                continue
            await docs.upsert_section(
                workspace_id=workspace_id,
                payload=AgentDocumentSectionInput(
                    document_kind="business",
                    subject_type="workspace",
                    subject_id=None,
                    section_key=spec.key,
                    title=spec.title,
                    body=d.body,
                    order_index=_ORDER_BY_KEY[spec.key],
                    source_evidence=[{"ref": r} for r in d.evidence_refs],
                    generated_by="system",
                ),
            )

    async def synthesize_section(
        self, *, workspace_id: int, section_key: str, fact_context: str
    ) -> BusinessSectionDraft:
        spec = _SPEC_BY_KEY[section_key]  # KeyError → API maps to 404
        prompt = (
            "Business facts (evidence):\n"
            f"{fact_context or '(no facts learned yet)'}\n\n"
            f"Write ONLY this section:\n- {spec.key}: {spec.title} — {spec.guidance}"
        )
        result: dict = await generate_structured_json(
            chain=FLASH_CHAIN,
            system=_business_md_system_prompt(),
            prompt=prompt,
            response_schema=BusinessSectionDraft,
            operation="business_md_section",
            workspace_id=workspace_id,
            prompt_cache=_business_md_prompt_cache(),
        )
        data = dict(result)
        data["section_key"] = spec.key  # trust the spec, not the model
        return BusinessSectionDraft.model_validate(data)

    async def persist_section(self, *, workspace_id: int, draft: BusinessSectionDraft) -> None:
        existing = await self._load_sections(workspace_id=workspace_id)
        if any(s.section_key == draft.section_key and s.generated_by == "owner" for s in existing):
            return
        spec = _SPEC_BY_KEY.get(draft.section_key)
        if spec is None:
            return
        await AgentDocumentService(self.session).upsert_section(
            workspace_id=workspace_id,
            payload=AgentDocumentSectionInput(
                document_kind="business",
                subject_type="workspace",
                subject_id=None,
                section_key=spec.key,
                title=spec.title,
                body=draft.body,
                order_index=_ORDER_BY_KEY[spec.key],
                source_evidence=[{"ref": r} for r in draft.evidence_refs],
                generated_by="system",
            ),
        )

    async def _load_sections(self, *, workspace_id: int) -> list[AgentDocumentSection]:
        stmt = select(AgentDocumentSection).where(
            AgentDocumentSection.workspace_id == workspace_id,
            AgentDocumentSection.document_kind == "business",
            AgentDocumentSection.subject_type == "workspace",
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def render_current(self, *, workspace_id: int, workspace_name: str) -> RenderedDocument:
        sections = await self._load_sections(workspace_id=workspace_id)
        return render_business_md(workspace_name=workspace_name, sections=sections)

    async def generate(self, *, workspace_id: int, workspace_name: str) -> RenderedDocument:
        fact_context = await self.build_fact_context(workspace_id=workspace_id)
        draft = await self.synthesize(workspace_id=workspace_id, fact_context=fact_context)
        await self.persist(workspace_id=workspace_id, draft=draft)
        await self.session.flush()
        return await self.render_current(workspace_id=workspace_id, workspace_name=workspace_name)


def _business_md_system_prompt() -> str:
    return _business_md_prompt_asset().body.strip()


def _business_md_prompt_cache() -> dict | None:
    return prompt_cache_payload_for_asset(
        _business_md_prompt_asset(),
        cache_scope="agent_documents.business_md",
    )


@lru_cache(maxsize=1)
def _business_md_prompt_asset() -> PromptAsset:
    return get_prompt_registry().load(
        _BUSINESS_MD_PROMPT_ID,
        version=_BUSINESS_MD_PROMPT_VERSION,
    )
