from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_document import AgentDocumentSection
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import AgentDocumentSectionInput, AgentSkillInput
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.brain.agent_document import AgentDocumentBuilderService
from app.modules.brain.contracts import (
    AGENT_SECTIONS,
    AgentDocumentDraft,
    AgentSectionDraft,
)


def test_agent_sections_are_the_six_spec_sections() -> None:
    keys = [s.key for s in AGENT_SECTIONS]
    assert keys == [
        "role_mission",
        "capabilities",
        "behavior_rules",
        "approval_rules",
        "examples",
        "must_never",
    ]
    assert all(s.title for s in AGENT_SECTIONS)


def test_agent_document_draft_holds_one_draft_per_section() -> None:
    draft = AgentDocumentDraft(
        sections=[
            AgentSectionDraft(
                section_key="role_mission",
                body="Sotuvchi agent.",
                evidence_refs=["business:overview"],
                confidence=0.9,
            )
        ]
    )
    assert draft.sections[0].section_key == "role_mission"
    assert draft.sections[0].confidence == 0.9


async def _create_agent(session: AsyncSession, workspace: Workspace, *, name: str = "Sotuvchi") -> Agent:
    agent = Agent(workspace_id=workspace.id, name=name)
    session.add(agent)
    await session.flush()
    return agent


async def _seed_business_section(
    session: AsyncSession, workspace: Workspace, *, section_key: str, title: str, body: str
) -> None:
    docs = AgentDocumentService(session)
    await docs.upsert_section(
        workspace_id=workspace.id,
        payload=AgentDocumentSectionInput(
            document_kind="business",
            subject_type="workspace",
            subject_id=None,
            section_key=section_key,
            title=title,
            body=body,
            order_index=0,
            source_evidence=[],
            generated_by="system",
        ),
    )
    await session.flush()


@pytest.mark.asyncio
async def test_load_agent_rejects_cross_workspace(
    db_session: AsyncSession, workspace: Workspace, workspace_b: Workspace
) -> None:
    agent_b = await _create_agent(db_session, workspace_b, name="B's agent")
    service = AgentDocumentBuilderService(db_session)
    with pytest.raises(LookupError):
        await service._load_agent(workspace_id=workspace.id, agent_id=agent_b.id)


@pytest.mark.asyncio
async def test_load_agent_missing_raises(db_session: AsyncSession, workspace: Workspace) -> None:
    service = AgentDocumentBuilderService(db_session)
    with pytest.raises(LookupError):
        await service._load_agent(workspace_id=workspace.id, agent_id=999999)


@pytest.mark.asyncio
async def test_business_context_includes_business_sections(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    await _seed_business_section(
        db_session, workspace,
        section_key="overview", title="Biznes haqida", body="Madelyn-Co — to'ylar uchun liboslar.",
    )
    service = AgentDocumentBuilderService(db_session)
    context = await service.build_business_context(workspace_id=workspace.id)
    assert "Madelyn-Co" in context
    assert "Biznes haqida" in context


@pytest.mark.asyncio
async def test_business_context_empty_when_no_business_md(
    db_session: AsyncSession, workspace_b: Workspace
) -> None:
    service = AgentDocumentBuilderService(db_session)
    context = await service.build_business_context(workspace_id=workspace_b.id)
    assert context.strip() == ""


@pytest.mark.asyncio
async def test_synthesize_returns_drafts_for_sections(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    fake_dict = {
        "sections": [
            {"section_key": "role_mission", "body": "Sotuvchi agent.", "evidence_refs": [], "confidence": 0.9}
        ]
    }
    service = AgentDocumentBuilderService(db_session)
    with patch(
        "app.modules.brain.agent_document.generate_structured_json",
        AsyncMock(return_value=fake_dict),
    ) as mock_llm:
        result = await service.synthesize(
            workspace_id=workspace.id,
            agent_context="Name: Sotuvchi\nType: customer",
            business_context="## Biznes haqida\nMadelyn-Co.",
            owner_input="Doim hurmat bilan gaplash.",
        )

    mock_llm.assert_awaited_once()
    kwargs = mock_llm.call_args.kwargs
    assert kwargs["workspace_id"] == workspace.id
    assert kwargs["response_schema"] is AgentDocumentDraft
    assert "Doim hurmat bilan gaplash." in kwargs["prompt"]
    assert result.sections[0].section_key == "role_mission"


@pytest.mark.asyncio
async def test_persist_writes_one_section_row_per_agent(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    agent = await _create_agent(db_session, workspace)
    draft = AgentDocumentDraft(sections=[
        AgentSectionDraft(section_key="role_mission", body="Sotuvchi.", evidence_refs=["business:overview"], confidence=0.9),
        AgentSectionDraft(section_key="capabilities", body="Xabarlarga javob beradi.", confidence=0.8),
    ])
    service = AgentDocumentBuilderService(db_session)
    await service.persist(workspace_id=workspace.id, agent_id=agent.id, draft=draft)
    await db_session.flush()

    rows = (await db_session.execute(
        _select(AgentDocumentSection).where(
            AgentDocumentSection.workspace_id == workspace.id,
            AgentDocumentSection.document_kind == "agent",
            AgentDocumentSection.subject_id == agent.id,
        )
    )).scalars().all()
    by_key = {r.section_key: r for r in rows}
    assert set(by_key) == {"role_mission", "capabilities"}
    assert all(r.subject_type == "agent" for r in rows)
    assert by_key["role_mission"].generated_by == "system"
    assert by_key["role_mission"].order_index < by_key["capabilities"].order_index
    assert by_key["role_mission"].source_evidence == [{"ref": "business:overview"}]
    assert by_key["capabilities"].source_evidence == []


@pytest.mark.asyncio
async def test_generate_end_to_end_renders_markdown(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    agent = await _create_agent(db_session, workspace, name="Madelyn Seller")
    await _seed_business_section(
        db_session, workspace, section_key="overview", title="Biznes haqida", body="To'y liboslari.",
    )
    fake = {"sections": [
        {"section_key": "role_mission", "body": "To'y liboslari sotuvchisi.", "confidence": 0.9},
    ]}
    service = AgentDocumentBuilderService(db_session)
    with patch("app.modules.brain.agent_document.generate_structured_json",
               AsyncMock(return_value=fake)):
        rendered = await service.generate(workspace_id=workspace.id, agent_id=agent.id)
    assert "AGENT.md — Madelyn Seller" in rendered.markdown
    assert "To'y liboslari sotuvchisi." in rendered.markdown
    assert rendered.sections_used >= 1


@pytest.mark.asyncio
async def test_render_current_with_no_sections_is_honest_empty(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    agent = await _create_agent(db_session, workspace)
    service = AgentDocumentBuilderService(db_session)
    rendered = await service.render_current(workspace_id=workspace.id, agent_id=agent.id)
    assert rendered.sections_used == 0


@pytest.mark.asyncio
async def test_render_current_includes_attached_skills(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    agent = await _create_agent(db_session, workspace)
    docs = AgentDocumentService(db_session)
    await docs.upsert_skill(
        workspace_id=workspace.id,
        payload=AgentSkillInput(slug="price-handling", name="Narx bilan ishlash", agent_id=agent.id),
    )
    await db_session.flush()
    service = AgentDocumentBuilderService(db_session)
    rendered = await service.render_current(workspace_id=workspace.id, agent_id=agent.id)
    assert "Narx bilan ishlash" in rendered.markdown


@pytest.mark.asyncio
async def test_generate_rejects_cross_workspace_agent(
    db_session: AsyncSession, workspace: Workspace, workspace_b: Workspace
) -> None:
    agent_b = await _create_agent(db_session, workspace_b)
    service = AgentDocumentBuilderService(db_session)
    with pytest.raises(LookupError):
        await service.generate(workspace_id=workspace.id, agent_id=agent_b.id)


@pytest.mark.asyncio
async def test_owner_edit_is_not_overwritten_by_regenerate(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    agent = await _create_agent(db_session, workspace)
    service = AgentDocumentBuilderService(db_session)
    await service.edit_section(
        workspace_id=workspace.id, agent_id=agent.id, section_key="role_mission", body="OWNER TEXT"
    )
    await db_session.flush()
    fake = {"sections": [{"section_key": "role_mission", "body": "LLM TEXT", "confidence": 0.9}]}
    with patch("app.modules.brain.agent_document.generate_structured_json",
               AsyncMock(return_value=fake)):
        await service.generate(workspace_id=workspace.id, agent_id=agent.id)
    await db_session.flush()
    rendered = await service.render_current(workspace_id=workspace.id, agent_id=agent.id)
    assert "OWNER TEXT" in rendered.markdown
    assert "LLM TEXT" not in rendered.markdown


@pytest.mark.asyncio
async def test_edit_unknown_section_key_raises(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    agent = await _create_agent(db_session, workspace)
    service = AgentDocumentBuilderService(db_session)
    with pytest.raises(KeyError):
        await service.edit_section(
            workspace_id=workspace.id, agent_id=agent.id, section_key="nope", body="x"
        )


@pytest.mark.asyncio
async def test_edit_cross_workspace_agent_raises(
    db_session: AsyncSession, workspace: Workspace, workspace_b: Workspace
) -> None:
    agent_b = await _create_agent(db_session, workspace_b)
    service = AgentDocumentBuilderService(db_session)
    with pytest.raises(LookupError):
        await service.edit_section(
            workspace_id=workspace.id, agent_id=agent_b.id, section_key="role_mission", body="x"
        )


@pytest.mark.asyncio
async def test_full_agent_md_lifecycle(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    # 0. BUSINESS.md context exists (a sibling slice's output)
    await _seed_business_section(
        db_session, workspace, section_key="overview", title="Biznes haqida", body="To'y liboslari.",
    )
    agent = await _create_agent(db_session, workspace, name="Madelyn Seller")
    service = AgentDocumentBuilderService(db_session)
    # 1. generate from defaults
    fake = {"sections": [
        {"section_key": "role_mission", "body": "To'y liboslari sotuvchisi.", "confidence": 0.9},
        {"section_key": "approval_rules", "body": "To'lovni tasdiqlashdan oldin egadan so'rang.", "confidence": 0.8},
    ]}
    with patch("app.modules.brain.agent_document.generate_structured_json",
               AsyncMock(return_value=fake)):
        r1 = await service.generate(workspace_id=workspace.id, agent_id=agent.id)
    assert "To'y liboslari sotuvchisi." in r1.markdown
    # 2. owner edits one section
    await service.edit_section(
        workspace_id=workspace.id, agent_id=agent.id, section_key="role_mission", body="Madelyn-Co premium sotuvchi.",
    )
    await db_session.flush()
    # 3. regenerate with owner input — owner edit survives, other section updates
    fake2 = {"sections": [
        {"section_key": "role_mission", "body": "SHOULD NOT APPEAR", "confidence": 0.9},
        {"section_key": "approval_rules", "body": "Yangilangan tasdiqlash qoidasi.", "confidence": 0.8},
    ]}
    with patch("app.modules.brain.agent_document.generate_structured_json",
               AsyncMock(return_value=fake2)):
        await service.generate(
            workspace_id=workspace.id, agent_id=agent.id, owner_input="Premium ohangda gaplash.",
        )
    await db_session.flush()
    final = await service.render_current(workspace_id=workspace.id, agent_id=agent.id)
    assert "Madelyn-Co premium sotuvchi." in final.markdown
    assert "SHOULD NOT APPEAR" not in final.markdown
    assert "Yangilangan tasdiqlash qoidasi." in final.markdown


# ---------------------------------------------------------------------------
# synthesize_section / persist_section (per-section path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_section_calls_llm_for_one_agent_section(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    captured: dict = {}

    async def fake_gen(**kwargs):  # type: ignore[override]
        captured.update(kwargs)
        assert kwargs["operation"] == "agent_md_section"
        assert "role_mission" in kwargs["prompt"]
        return {
            "section_key": "role_mission",
            "body": "Rol matni",
            "evidence_refs": [],
            "confidence": 0.7,
        }

    service = AgentDocumentBuilderService(db_session)
    import app.modules.brain.agent_document as _mod

    _mod.generate_structured_json = fake_gen  # type: ignore[assignment]
    try:
        draft = await service.synthesize_section(
            workspace_id=workspace.id,
            agent_context="Name: A",
            business_context="## Biz\nx",
            section_key="role_mission",
        )
    finally:
        from app.brain.llm import generate_structured_json as _orig

        _mod.generate_structured_json = _orig  # type: ignore[assignment]

    assert draft.section_key == "role_mission"
    assert draft.body == "Rol matni"


@pytest.mark.asyncio
async def test_persist_section_writes_one_system_agent_section(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    agent = await _create_agent(db_session, workspace)
    service = AgentDocumentBuilderService(db_session)
    await service.persist_section(
        workspace_id=workspace.id,
        agent_id=agent.id,
        draft=AgentSectionDraft(section_key="role_mission", body="X"),
    )
    await db_session.flush()
    sections = await service._load_sections(workspace_id=workspace.id, agent_id=agent.id)
    assert len(sections) == 1
    row = sections[0]
    assert row.section_key == "role_mission"
    assert row.generated_by == "system"
    assert row.subject_id == agent.id


@pytest.mark.asyncio
async def test_persist_section_skips_owner_locked(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    agent = await _create_agent(db_session, workspace)
    service = AgentDocumentBuilderService(db_session)
    # owner locks the section first
    await service.edit_section(
        workspace_id=workspace.id,
        agent_id=agent.id,
        section_key="role_mission",
        body="owner text",
    )
    await db_session.flush()
    # system tries to overwrite via persist_section
    await service.persist_section(
        workspace_id=workspace.id,
        agent_id=agent.id,
        draft=AgentSectionDraft(section_key="role_mission", body="system text"),
    )
    await db_session.flush()
    sections = await service._load_sections(workspace_id=workspace.id, agent_id=agent.id)
    row = next(s for s in sections if s.section_key == "role_mission")
    assert row.generated_by == "owner"
    assert row.body == "owner text"
