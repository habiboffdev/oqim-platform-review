from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_document import AgentDocumentSection
from app.models.commercial_spine import BusinessBrainFactRecord
from app.models.workspace import Workspace
from app.modules.brain.business_document import BusinessDocumentService
from app.modules.brain.contracts import (
    BUSINESS_SECTIONS,
    BusinessDocumentDraft,
    BusinessSectionDraft,
)


def test_business_sections_are_the_ten_spec_sections() -> None:
    keys = [s.key for s in BUSINESS_SECTIONS]
    assert keys == [
        "overview",
        "what_we_sell",
        "catalog_sku_rules",
        "voice_style",
        "price_payment_policy",
        "delivery_promises",
        "followup_policy",
        "do_not_guess",
        "source_priority",
        "missing_data_behavior",
    ]
    assert all(s.title for s in BUSINESS_SECTIONS)


def test_business_document_draft_holds_one_draft_per_section() -> None:
    draft = BusinessDocumentDraft(
        sections=[
            BusinessSectionDraft(
                section_key="overview",
                body="We sell premium dresses.",
                evidence_refs=["fact:123"],
                confidence=0.9,
            )
        ]
    )
    assert draft.sections[0].section_key == "overview"
    assert draft.sections[0].confidence == 0.9


async def _add_fact(
    session: AsyncSession,
    workspace: Workspace,
    *,
    fact_type: str,
    entity_ref: str,
    value: dict,
    status: str = "confirmed",
) -> BusinessBrainFactRecord:
    rec = BusinessBrainFactRecord(
        fact_id=f"{fact_type}:{entity_ref}",
        workspace_id=workspace.id,
        fact_type=fact_type,
        entity_ref=entity_ref,
        value=value,
        confidence=0.9,
        status=status,
        risk_tier="low",
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        idempotency_key=f"test:{fact_type}:{entity_ref}",
    )
    session.add(rec)
    await session.flush()
    return rec


@pytest.mark.asyncio
async def test_fact_context_includes_active_facts_only(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    # "confirmed" fact — must appear
    await _add_fact(
        db_session,
        workspace,
        fact_type="catalog_product",
        entity_ref="sku:1",
        value={"name": "Silk Gown", "price": "1.45M"},
        status="confirmed",
    )
    # "active" fact — this is the dominant real-world status; must also appear
    await _add_fact(
        db_session,
        workspace,
        fact_type="business_rule",
        entity_ref="rule:1",
        value={"rule": "no_refund_after_7_days"},
        status="active",
    )
    # "superseded" fact — must be excluded
    await _add_fact(
        db_session,
        workspace,
        fact_type="kb_answer",
        entity_ref="kb:1",
        value={"q": "delivery?", "a": "2 days"},
        status="superseded",
    )
    service = BusinessDocumentService(db_session)
    context = await service.build_fact_context(workspace_id=workspace.id)
    assert "Silk Gown" in context  # confirmed fact included
    assert "no_refund_after_7_days" in context  # active fact included (was silently broken)
    assert "delivery?" not in context  # superseded fact excluded


@pytest.mark.asyncio
async def test_fact_context_empty_when_no_facts(
    db_session: AsyncSession, workspace_b: Workspace
) -> None:
    service = BusinessDocumentService(db_session)
    context = await service.build_fact_context(workspace_id=workspace_b.id)
    assert context.strip() == ""


@pytest.mark.asyncio
async def test_synthesize_returns_drafts_for_sections(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    # generate_structured_json returns a plain dict (not a wrapped response object)
    fake_dict = {
        "sections": [
            {"section_key": "overview", "body": "Premium dresses.", "evidence_refs": [], "confidence": 0.9}
        ]
    }

    service = BusinessDocumentService(db_session)
    with patch(
        "app.modules.brain.business_document.generate_structured_json",
        AsyncMock(return_value=fake_dict),
    ) as mock_llm:
        result = await service.synthesize(workspace_id=workspace.id, fact_context="[catalog] ...")

    mock_llm.assert_awaited_once()
    kwargs = mock_llm.call_args.kwargs
    assert kwargs["workspace_id"] == workspace.id  # budget enforcement wired
    assert kwargs["response_schema"] is BusinessDocumentDraft
    assert result.sections[0].section_key == "overview"


@pytest.mark.asyncio
async def test_persist_writes_one_section_row_per_draft(db_session, workspace) -> None:
    draft = BusinessDocumentDraft(sections=[
        BusinessSectionDraft(section_key="overview", body="Premium dresses.", evidence_refs=["fact:1"], confidence=0.9),
        BusinessSectionDraft(section_key="what_we_sell", body="Dresses, suits.", confidence=0.8),
    ])
    service = BusinessDocumentService(db_session)
    await service.persist(workspace_id=workspace.id, draft=draft)
    await db_session.flush()

    rows = (await db_session.execute(
        _select(AgentDocumentSection).where(
            AgentDocumentSection.workspace_id == workspace.id,
            AgentDocumentSection.document_kind == "business",
        )
    )).scalars().all()
    by_key = {r.section_key: r for r in rows}
    assert set(by_key) == {"overview", "what_we_sell"}
    assert by_key["overview"].generated_by == "system"
    assert by_key["overview"].order_index < by_key["what_we_sell"].order_index
    # source_evidence is list[dict] — evidence_refs stored as [{"ref": r} for r in ...]
    assert by_key["overview"].source_evidence == [{"ref": "fact:1"}]
    assert by_key["what_we_sell"].source_evidence == []


@pytest.mark.asyncio
async def test_generate_end_to_end_renders_markdown(db_session, workspace) -> None:
    await _add_fact(db_session, workspace, fact_type="catalog_product",
                    entity_ref="sku:1", value={"name": "Silk Gown"})
    fake = {"sections": [{"section_key": "overview", "body": "Premium dresses.", "confidence": 0.9}]}
    service = BusinessDocumentService(db_session)
    with patch("app.modules.brain.business_document.generate_structured_json",
               AsyncMock(return_value=fake)):
        rendered = await service.generate(workspace_id=workspace.id, workspace_name=workspace.name)
    assert "BUSINESS.md" in rendered.markdown
    assert "Premium dresses." in rendered.markdown
    assert rendered.sections_used >= 1


@pytest.mark.asyncio
async def test_render_current_with_no_sections_is_honest_empty(db_session, workspace_b) -> None:
    service = BusinessDocumentService(db_session)
    rendered = await service.render_current(workspace_id=workspace_b.id, workspace_name=workspace_b.name)
    assert rendered.sections_used == 0


@pytest.mark.asyncio
async def test_owner_edit_is_not_overwritten_by_regenerate(db_session, workspace) -> None:
    service = BusinessDocumentService(db_session)
    await service.edit_section(workspace_id=workspace.id, section_key="overview", body="OWNER TEXT")
    await db_session.flush()
    fake = {"sections": [{"section_key": "overview", "body": "LLM TEXT", "confidence": 0.9}]}
    with patch("app.modules.brain.business_document.generate_structured_json",
               AsyncMock(return_value=fake)):
        await service.generate(workspace_id=workspace.id, workspace_name=workspace.name)
    await db_session.flush()
    rendered = await service.render_current(workspace_id=workspace.id, workspace_name=workspace.name)
    assert "OWNER TEXT" in rendered.markdown
    assert "LLM TEXT" not in rendered.markdown


@pytest.mark.asyncio
async def test_owner_edit_unknown_section_key_raises(db_session, workspace) -> None:
    service = BusinessDocumentService(db_session)
    with pytest.raises(KeyError):
        await service.edit_section(workspace_id=workspace.id, section_key="nonexistent_key", body="text")


async def test_synthesize_section_calls_llm_for_one_section(db_session, workspace, monkeypatch):
    svc = BusinessDocumentService(db_session)
    async def fake_gen(**kwargs):
        assert kwargs["operation"] == "business_md_section"
        assert "overview" in kwargs["prompt"]
        return {"section_key": "overview", "body": "Biznes haqida matn", "evidence_refs": ["fact:1"], "confidence": 0.8}
    monkeypatch.setattr("app.modules.brain.business_document.generate_structured_json", fake_gen)
    draft = await svc.synthesize_section(workspace_id=workspace.id, section_key="overview", fact_context="[x] fact")
    assert draft.section_key == "overview" and draft.body == "Biznes haqida matn"


async def test_persist_section_writes_one_system_section(db_session, workspace):
    svc = BusinessDocumentService(db_session)
    await svc.persist_section(workspace_id=workspace.id, draft=BusinessSectionDraft(section_key="overview", body="X", evidence_refs=["fact:1"]))
    await db_session.flush()
    rows = await svc._load_sections(workspace_id=workspace.id)
    assert any(r.section_key == "overview" and r.generated_by == "system" for r in rows)


async def test_persist_section_skips_owner_locked(db_session, workspace):
    svc = BusinessDocumentService(db_session)
    await svc.edit_section(workspace_id=workspace.id, section_key="overview", body="owner text")
    await svc.persist_section(workspace_id=workspace.id, draft=BusinessSectionDraft(section_key="overview", body="system text"))
    await db_session.flush()
    rows = [r for r in await svc._load_sections(workspace_id=workspace.id) if r.section_key == "overview"]
    assert rows[0].generated_by == "owner" and rows[0].body == "owner text"


@pytest.mark.asyncio
async def test_non_owner_sections_still_updated_on_regen(db_session, workspace) -> None:
    service = BusinessDocumentService(db_session)
    # Only lock "overview" as owner-edited
    await service.edit_section(workspace_id=workspace.id, section_key="overview", body="OWNER TEXT")
    await db_session.flush()
    # Regen provides both overview (should be skipped) and what_we_sell (should update)
    fake = {
        "sections": [
            {"section_key": "overview", "body": "LLM TEXT OVERVIEW", "confidence": 0.9},
            {"section_key": "what_we_sell", "body": "LLM TEXT WHAT WE SELL", "confidence": 0.8},
        ]
    }
    with patch("app.modules.brain.business_document.generate_structured_json",
               AsyncMock(return_value=fake)):
        await service.generate(workspace_id=workspace.id, workspace_name=workspace.name)
    await db_session.flush()
    rendered = await service.render_current(workspace_id=workspace.id, workspace_name=workspace.name)
    assert "OWNER TEXT" in rendered.markdown
    assert "LLM TEXT OVERVIEW" not in rendered.markdown
    assert "LLM TEXT WHAT WE SELL" in rendered.markdown


@pytest.mark.asyncio
async def test_full_business_md_lifecycle(db_session, workspace) -> None:
    # 1. facts exist
    await _add_fact(db_session, workspace, fact_type="catalog_product",
                    entity_ref="sku:1", value={"name": "Silk Gown", "price": "1.45M"})
    # 2. generate
    fake = {"sections": [
        {"section_key": "overview", "body": "Premium dresses.", "confidence": 0.9},
        {"section_key": "price_payment_policy", "body": "1.45M, installments.", "confidence": 0.8},
    ]}
    service = BusinessDocumentService(db_session)
    with patch("app.modules.brain.business_document.generate_structured_json",
               AsyncMock(return_value=fake)):
        r1 = await service.generate(workspace_id=workspace.id, workspace_name=workspace.name)
    assert "Premium dresses." in r1.markdown
    # 3. owner edits one section
    await service.edit_section(workspace_id=workspace.id, section_key="overview", body="Madelyn-Co premium.")
    await db_session.flush()
    # 4. regenerate — owner edit survives, other section updates
    fake2 = {"sections": [
        {"section_key": "overview", "body": "SHOULD NOT APPEAR", "confidence": 0.9},
        {"section_key": "price_payment_policy", "body": "Updated price policy.", "confidence": 0.8},
    ]}
    with patch("app.modules.brain.business_document.generate_structured_json",
               AsyncMock(return_value=fake2)):
        await service.generate(workspace_id=workspace.id, workspace_name=workspace.name)
    await db_session.flush()
    final = await service.render_current(workspace_id=workspace.id, workspace_name=workspace.name)
    assert "Madelyn-Co premium." in final.markdown
    assert "SHOULD NOT APPEAR" not in final.markdown
    assert "Updated price policy." in final.markdown
