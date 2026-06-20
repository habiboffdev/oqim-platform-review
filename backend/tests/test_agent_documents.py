"""Tests for the agent-documents data contracts: AgentSkill, AgentDocumentSection,
and pure renderers for BUSINESS.md / AGENT.md / SKILL.md.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_document import AgentDocumentSection
from app.models.agent_skill import AgentSkill
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import (
    AgentDocumentSectionInput,
    AgentSkillInput,
)
from app.modules.agent_documents.renderer import (
    render_agent_md,
    render_business_md,
    render_skill_md,
)
from app.modules.agent_documents.service import AgentDocumentService


class TestAgentSkillContract:
    def test_skill_input_rejects_whitespace_in_slug(self):
        with pytest.raises(ValueError, match="whitespace"):
            AgentSkillInput(slug="bad slug", name="X")

    def test_skill_input_normalizes_slug_lowercase(self):
        skill = AgentSkillInput(slug="Order-Lookup", name="Order Lookup")
        assert skill.slug == "order-lookup"

    def test_skill_input_defaults_empty_collections(self):
        skill = AgentSkillInput(slug="foo", name="Foo")
        assert skill.tools == []
        assert skill.examples == []
        assert skill.input_schema == {}
        assert skill.enabled is True


class TestDocumentSectionContract:
    def test_section_rejects_subject_id_when_workspace_scope(self):
        with pytest.raises(ValueError, match="subject_id must be null"):
            AgentDocumentSectionInput(
                document_kind="business",
                subject_type="workspace",
                subject_id=1,
                section_key="identity",
                title="Identity",
            )

    def test_section_requires_subject_id_for_agent_scope(self):
        with pytest.raises(ValueError, match="subject_id is required"):
            AgentDocumentSectionInput(
                document_kind="agent",
                subject_type="agent",
                subject_id=None,
                section_key="role",
                title="Role",
            )

    def test_section_rejects_mismatched_kind_and_subject(self):
        with pytest.raises(ValueError, match="requires subject_type"):
            AgentDocumentSectionInput(
                document_kind="agent",
                subject_type="workspace",
                subject_id=None,
                section_key="role",
                title="Role",
            )


@pytest.mark.asyncio
class TestSkillService:
    async def test_upsert_creates_then_updates_with_version_bump(
        self, db_session: AsyncSession, workspace: Workspace
    ) -> None:
        service = AgentDocumentService(db_session)

        created = await service.upsert_skill(
            workspace_id=workspace.id,
            payload=AgentSkillInput(
                slug="catalog-lookup",
                name="Catalog lookup",
                description="Look up SKUs",
                tools=["knowledge_search_catalog"],
            ),
        )
        assert created.version == 1
        assert created.workspace_id == workspace.id
        assert created.tools == ["knowledge_search_catalog"]

        updated = await service.upsert_skill(
            workspace_id=workspace.id,
            payload=AgentSkillInput(
                slug="catalog-lookup",
                name="Catalog lookup",
                description="Now also resolves variants",
                tools=["knowledge_search_catalog", "knowledge_explain_sources"],
            ),
        )
        assert updated.id == created.id
        assert updated.version == 2
        assert updated.tools == ["knowledge_search_catalog", "knowledge_explain_sources"]

    async def test_rejects_agent_from_other_workspace(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        workspace_b: Workspace,
    ) -> None:
        foreign_agent = Agent(workspace_id=workspace_b.id, name="Other")
        db_session.add(foreign_agent)
        await db_session.flush()

        service = AgentDocumentService(db_session)
        with pytest.raises(ValueError, match="does not belong"):
            await service.upsert_skill(
                workspace_id=workspace.id,
                payload=AgentSkillInput(
                    slug="catalog-lookup",
                    name="Catalog lookup",
                    agent_id=foreign_agent.id,
                ),
            )

    async def test_unique_slug_per_workspace(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        workspace_b: Workspace,
    ) -> None:
        first = AgentSkill(workspace_id=workspace.id, slug="dup", name="A")
        second = AgentSkill(workspace_id=workspace_b.id, slug="dup", name="B")
        db_session.add_all([first, second])
        await db_session.flush()
        assert first.id != second.id

        collision = AgentSkill(workspace_id=workspace.id, slug="dup", name="A2")
        db_session.add(collision)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.asyncio
class TestDocumentSectionService:
    async def test_upsert_section_overwrites_same_key(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        service = AgentDocumentService(db_session)

        first = await service.upsert_section(
            workspace_id=workspace.id,
            payload=AgentDocumentSectionInput(
                document_kind="agent",
                subject_type="agent",
                subject_id=agent.id,
                section_key="role",
                title="Rol",
                body="Sotuv agenti.",
                order_index=10,
            ),
        )
        second = await service.upsert_section(
            workspace_id=workspace.id,
            payload=AgentDocumentSectionInput(
                document_kind="agent",
                subject_type="agent",
                subject_id=agent.id,
                section_key="role",
                title="Rol",
                body="Sotuv va support agenti.",
                order_index=10,
            ),
        )
        assert first.id == second.id
        assert second.body.startswith("Sotuv va support")

    async def test_rejects_agent_section_for_other_workspace(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        workspace_b: Workspace,
    ) -> None:
        foreign_agent = Agent(workspace_id=workspace_b.id, name="Other")
        db_session.add(foreign_agent)
        await db_session.flush()

        service = AgentDocumentService(db_session)
        with pytest.raises(ValueError, match="agent in this workspace"):
            await service.upsert_section(
                workspace_id=workspace.id,
                payload=AgentDocumentSectionInput(
                    document_kind="agent",
                    subject_type="agent",
                    subject_id=foreign_agent.id,
                    section_key="role",
                    title="Role",
                    body="x",
                ),
            )

    async def test_list_sections_returns_only_target_workspace(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        workspace_b: Workspace,
    ) -> None:
        db_session.add_all(
            [
                AgentDocumentSection(
                    workspace_id=workspace.id,
                    document_kind="business",
                    subject_type="workspace",
                    subject_id=None,
                    section_key="identity",
                    title="Identity",
                    body="ours",
                    order_index=0,
                ),
                AgentDocumentSection(
                    workspace_id=workspace_b.id,
                    document_kind="business",
                    subject_type="workspace",
                    subject_id=None,
                    section_key="identity",
                    title="Identity",
                    body="theirs",
                    order_index=0,
                ),
            ]
        )
        await db_session.flush()

        service = AgentDocumentService(db_session)
        result = await service.list_sections(
            workspace_id=workspace.id,
            document_kind="business",
            subject_type="workspace",
            subject_id=None,
        )
        bodies = [section.body for section in result]
        assert bodies == ["ours"]


class TestRenderers:
    def test_business_md_orders_sections_by_index_then_key(self):
        sections = [
            _stub_section(section_key="catalog", title="Katalog", body="iPhone 15", order_index=20),
            _stub_section(section_key="identity", title="Kim biz", body="Toshkent shop", order_index=10),
            _stub_section(section_key="voice", title="Ovoz", body="Iliq, qisqa.", order_index=10),
        ]
        result = render_business_md(workspace_name="Toshkent Shop", sections=sections)
        assert result.kind == "business"
        assert result.sections_used == 3
        assert result.markdown.startswith("# BUSINESS.md — Toshkent Shop")
        identity_index = result.markdown.index("## Kim biz")
        voice_index = result.markdown.index("## Ovoz")
        catalog_index = result.markdown.index("## Katalog")
        assert identity_index < voice_index < catalog_index

    def test_business_md_renders_placeholder_for_empty_body(self):
        sections = [_stub_section(section_key="voice", title="Ovoz", body="", order_index=0)]
        result = render_business_md(workspace_name="X", sections=sections)
        assert "_Yetishmayapti._" in result.markdown

    def test_agent_md_includes_skill_list(self):
        agent = _stub_agent(agent_id=1, name="Sotuvchi", agent_type="customer", trust_mode="disabled")
        sections = [_stub_section(section_key="role", title="Rol", body="Mijozga javob beradi.")]
        skills = [
            _stub_skill(
                skill_id=11,
                slug="catalog-lookup",
                name="Catalog lookup",
                description="Look up SKUs",
            ),
            _stub_skill(
                skill_id=12,
                slug="follow-up",
                name="Follow-up",
                description="",
            ),
        ]
        result = render_agent_md(agent=agent, sections=sections, skills=skills)
        assert result.kind == "agent"
        assert result.subject_id == 1
        assert "Type: customer · Trust mode: disabled" in result.markdown
        assert "## Skills" in result.markdown
        assert "`catalog-lookup`" in result.markdown
        assert "Look up SKUs" in result.markdown
        # skill without description should still render
        assert "`follow-up`" in result.markdown

    def test_skill_md_renders_structured_blocks(self):
        skill = _stub_skill(
            skill_id=7,
            slug="catalog-lookup",
            name="Catalog lookup",
            description="Resolve SKUs from messy text.",
            instructions="1. Parse text.\n2. Query retrieval core.",
            when_to_use="When buyer asks about a product.",
            when_not_to_use="When buyer asks about delivery.",
            tools=["knowledge_search_catalog", "knowledge_explain_sources"],
            examples=[{"input": "iPhone 15 bormi?", "output": "Mavjud"}],
        )
        result = render_skill_md(skill=skill)
        assert result.kind == "skill"
        assert result.subject_id == 7
        text = result.markdown
        assert text.startswith("# SKILL.md — Catalog lookup")
        assert "## Description" in text
        assert "## When to use" in text
        assert "## When not to use" in text
        assert "## Instructions" in text
        assert "## Tools" in text
        assert "- `knowledge_search_catalog`" in text
        assert "## Examples" in text
        assert "iPhone 15 bormi?" in text


# --- helpers --------------------------------------------------------------


class _StubSection:
    def __init__(self, *, section_key: str, title: str, body: str, order_index: int):
        self.section_key = section_key
        self.title = title
        self.body = body
        self.order_index = order_index


class _StubAgent:
    def __init__(self, *, id: int, name: str, agent_type: str, trust_mode: str):
        self.id = id
        self.name = name
        self.agent_type = agent_type
        self.trust_mode = trust_mode


class _StubSkill:
    def __init__(
        self,
        *,
        id: int,
        slug: str,
        name: str,
        description: str = "",
        instructions: str = "",
        when_to_use: str = "",
        when_not_to_use: str = "",
        tools: list[str] | None = None,
        examples: list[dict[str, object]] | None = None,
    ):
        self.id = id
        self.slug = slug
        self.name = name
        self.description = description
        self.instructions = instructions
        self.when_to_use = when_to_use
        self.when_not_to_use = when_not_to_use
        self.tools = tools or []
        self.examples = examples or []


def _stub_section(*, section_key: str, title: str, body: str, order_index: int = 0) -> _StubSection:
    return _StubSection(section_key=section_key, title=title, body=body, order_index=order_index)


def _stub_agent(*, agent_id: int, name: str, agent_type: str, trust_mode: str) -> _StubAgent:
    return _StubAgent(id=agent_id, name=name, agent_type=agent_type, trust_mode=trust_mode)


def _stub_skill(
    *,
    skill_id: int,
    slug: str,
    name: str,
    description: str = "",
    instructions: str = "",
    when_to_use: str = "",
    when_not_to_use: str = "",
    tools: list[str] | None = None,
    examples: list[dict[str, object]] | None = None,
) -> _StubSkill:
    return _StubSkill(
        id=skill_id,
        slug=slug,
        name=name,
        description=description,
        instructions=instructions,
        when_to_use=when_to_use,
        when_not_to_use=when_not_to_use,
        tools=tools,
        examples=examples,
    )
