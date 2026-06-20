"""Generate a per-agent AGENT.md from the workspace BUSINESS.md + agent config.

Loads and workspace-isolates the agent, reads the workspace's already-generated
BUSINESS.md sections for grounding context, synthesizes the 6 AGENT.md sections
via app.brain.llm, persists them through the existing agent_documents service
(subject_type="agent"), and renders with the existing render_agent_md().
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.llm import generate_structured_json
from app.brain.llm_policy import FLASH_CHAIN
from app.brain.prompt_payload import prompt_cache_payload_for_asset
from app.brain.prompt_registry import PromptAsset
from app.brain.prompt_registry import get_prompt_registry
from app.models.agent import Agent
from app.models.agent_document import AgentDocumentSection
from app.modules.agent_documents.contracts import AgentDocumentSectionInput, RenderedDocument
from app.modules.agent_documents.renderer import render_agent_md
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.brain.contracts import AGENT_SECTIONS, AgentDocumentDraft, AgentSectionDraft

_AGENT_MD_PROMPT_ID = "agent_documents.agent_md_synthesis"
_AGENT_MD_PROMPT_VERSION = "1.0.0"


_SPEC_BY_KEY = {s.key: s for s in AGENT_SECTIONS}
_ORDER_BY_KEY = {s.key: i for i, s in enumerate(AGENT_SECTIONS)}


def _section_instructions() -> str:
    return "\n".join(f"- {s.key}: {s.title} — {s.guidance}" for s in AGENT_SECTIONS)


class AgentDocumentBuilderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _load_agent(self, *, workspace_id: int, agent_id: int) -> Agent:
        agent = await self.session.get(Agent, agent_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise LookupError(f"agent {agent_id} not found in workspace {workspace_id}")
        return agent

    async def build_business_context(self, *, workspace_id: int) -> str:
        docs = AgentDocumentService(self.session)
        sections = await docs.list_sections(
            workspace_id=workspace_id,
            document_kind="business",
            subject_type="workspace",
            subject_id=None,
        )
        return "\n\n".join(f"## {s.title}\n{s.body}".strip() for s in sections).strip()

    def _agent_context(self, agent: Agent) -> str:
        tools = (agent.tools_config or {}).get("enabled_tools", [])
        tools_line = ", ".join(tools) if tools else "(none)"
        return (
            f"Name: {agent.name}\n"
            f"Type: {agent.agent_type}\n"
            f"Trust mode: {agent.trust_mode}\n"
            f"Enabled tools: {tools_line}"
        )

    async def synthesize(
        self,
        *,
        workspace_id: int,
        agent_context: str,
        business_context: str,
        owner_input: str | None = None,
    ) -> AgentDocumentDraft:
        prompt = (
            "Agent:\n"
            f"{agent_context}\n\n"
            "BUSINESS.md (the source of truth this agent must obey):\n"
            f"{business_context or '(no BUSINESS.md generated yet)'}\n\n"
            "Owner instructions for this agent:\n"
            f"{owner_input or '(none — use defaults from the template + BUSINESS.md)'}\n\n"
            "Write one draft per section. Sections:\n"
            f"{_section_instructions()}"
        )
        result = await generate_structured_json(
            chain=FLASH_CHAIN,
            system=_agent_md_system_prompt(),
            prompt=prompt,
            response_schema=AgentDocumentDraft,
            operation="agent_md_synthesis",
            workspace_id=workspace_id,
            prompt_cache=_agent_md_prompt_cache(),
        )
        return AgentDocumentDraft.model_validate(result)

    async def _load_sections(self, *, workspace_id: int, agent_id: int) -> list[AgentDocumentSection]:
        stmt = select(AgentDocumentSection).where(
            AgentDocumentSection.workspace_id == workspace_id,
            AgentDocumentSection.document_kind == "agent",
            AgentDocumentSection.subject_type == "agent",
            AgentDocumentSection.subject_id == agent_id,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def persist(self, *, workspace_id: int, agent_id: int, draft: AgentDocumentDraft) -> None:
        existing = await self._load_sections(workspace_id=workspace_id, agent_id=agent_id)
        owner_locked = {s.section_key for s in existing if s.generated_by == "owner"}
        docs = AgentDocumentService(self.session)
        for d in draft.sections:
            spec = _SPEC_BY_KEY.get(d.section_key)
            if spec is None or spec.key in owner_locked:
                continue
            await docs.upsert_section(
                workspace_id=workspace_id,
                payload=AgentDocumentSectionInput(
                    document_kind="agent",
                    subject_type="agent",
                    subject_id=agent_id,
                    section_key=spec.key,
                    title=spec.title,
                    body=d.body,
                    order_index=_ORDER_BY_KEY[spec.key],
                    source_evidence=[{"ref": r} for r in d.evidence_refs],
                    generated_by="system",
                ),
            )

    async def synthesize_section(
        self,
        *,
        workspace_id: int,
        agent_context: str,
        business_context: str,
        section_key: str,
        owner_input: str | None = None,
    ) -> AgentSectionDraft:
        spec = _SPEC_BY_KEY[section_key]  # KeyError → API maps to 404
        prompt = (
            "Agent:\n"
            f"{agent_context}\n\n"
            "BUSINESS.md (the source of truth this agent must obey):\n"
            f"{business_context or '(no BUSINESS.md generated yet)'}\n\n"
            "Owner instructions for this agent:\n"
            f"{owner_input or '(none — use defaults from the template + BUSINESS.md)'}\n\n"
            f"Write ONLY this section:\n- {spec.key}: {spec.title} — {spec.guidance}"
        )
        result = await generate_structured_json(
            chain=FLASH_CHAIN,
            system=_agent_md_system_prompt(),
            prompt=prompt,
            response_schema=AgentSectionDraft,
            operation="agent_md_section",
            workspace_id=workspace_id,
            prompt_cache=_agent_md_prompt_cache(),
        )
        data = dict(result)
        data["section_key"] = spec.key
        return AgentSectionDraft.model_validate(data)

    async def persist_section(
        self, *, workspace_id: int, agent_id: int, draft: AgentSectionDraft
    ) -> None:
        existing = await self._load_sections(workspace_id=workspace_id, agent_id=agent_id)
        if any(s.section_key == draft.section_key and s.generated_by == "owner" for s in existing):
            return
        spec = _SPEC_BY_KEY.get(draft.section_key)
        if spec is None:
            return
        await AgentDocumentService(self.session).upsert_section(
            workspace_id=workspace_id,
            payload=AgentDocumentSectionInput(
                document_kind="agent",
                subject_type="agent",
                subject_id=agent_id,
                section_key=spec.key,
                title=spec.title,
                body=draft.body,
                order_index=_ORDER_BY_KEY[spec.key],
                source_evidence=[{"ref": r} for r in draft.evidence_refs],
                generated_by="system",
            ),
        )

    async def edit_section(
        self, *, workspace_id: int, agent_id: int, section_key: str, body: str
    ) -> None:
        await self._load_agent(workspace_id=workspace_id, agent_id=agent_id)
        spec = _SPEC_BY_KEY[section_key]  # KeyError for unknown section; API maps to 404
        docs = AgentDocumentService(self.session)
        await docs.upsert_section(
            workspace_id=workspace_id,
            payload=AgentDocumentSectionInput(
                document_kind="agent",
                subject_type="agent",
                subject_id=agent_id,
                section_key=spec.key,
                title=spec.title,
                body=body,
                order_index=_ORDER_BY_KEY[spec.key],
                source_evidence=[],
                generated_by="owner",
            ),
        )

    async def render_current(self, *, workspace_id: int, agent_id: int) -> RenderedDocument:
        agent = await self._load_agent(workspace_id=workspace_id, agent_id=agent_id)
        sections = await self._load_sections(workspace_id=workspace_id, agent_id=agent_id)
        docs = AgentDocumentService(self.session)
        skills = await docs.list_skills(workspace_id=workspace_id, agent_id=agent_id)
        return render_agent_md(agent, sections, skills)

    async def generate(
        self, *, workspace_id: int, agent_id: int, owner_input: str | None = None
    ) -> RenderedDocument:
        agent = await self._load_agent(workspace_id=workspace_id, agent_id=agent_id)
        business_context = await self.build_business_context(workspace_id=workspace_id)
        draft = await self.synthesize(
            workspace_id=workspace_id,
            agent_context=self._agent_context(agent),
            business_context=business_context,
            owner_input=owner_input,
        )
        await self.persist(workspace_id=workspace_id, agent_id=agent_id, draft=draft)
        await self.session.flush()
        return await self.render_current(workspace_id=workspace_id, agent_id=agent_id)


def _agent_md_system_prompt() -> str:
    return _agent_md_prompt_asset().body.strip()


def _agent_md_prompt_cache() -> dict | None:
    return prompt_cache_payload_for_asset(
        _agent_md_prompt_asset(),
        cache_scope="agent_documents.agent_md",
    )


@lru_cache(maxsize=1)
def _agent_md_prompt_asset() -> PromptAsset:
    return get_prompt_registry().load(
        _AGENT_MD_PROMPT_ID,
        version=_AGENT_MD_PROMPT_VERSION,
    )
