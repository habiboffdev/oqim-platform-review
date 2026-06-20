from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.llm_policy import FLASH_CHAIN
from app.models.learned_skill_candidate import LearnedSkillCandidate
from app.models.workspace import Workspace
from app.modules.brain.skill_upload import SkillUploadService

_FAKE = {
    "slug": "Price Handling", "name": "Price handling", "trigger": "asks price",
    "action": "quote a range", "example_phrase": "100-120k", "dimension": "price", "confidence": 0.8,
}


@pytest.mark.asyncio
async def test_import_from_text_persists_upload_candidate(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    service = SkillUploadService(db_session)
    with patch("app.modules.brain.skill_upload.generate_structured_json",
               AsyncMock(return_value=_FAKE)) as mock_llm:
        cand = await service.import_from_text(workspace_id=workspace.id, content="--- a pasted SKILL.md ---")
    assert mock_llm.call_args.kwargs["workspace_id"] == workspace.id
    assert mock_llm.call_args.kwargs["chain"] is FLASH_CHAIN
    assert mock_llm.call_args.kwargs["system"].startswith("Normalize a provided")
    prompt_cache = mock_llm.call_args.kwargs["prompt_cache"]
    assert prompt_cache["prompt_asset"]["prompt_id"] == "learning.skill_upload_normalize"
    assert prompt_cache["runtime_context"]["cache_scope"] == "learning.skill_upload_normalize"
    assert cand.source == "upload"
    assert cand.status == "proposed"
    assert cand.slug == "price-handling"  # normalized to kebab
    rows = (await db_session.execute(
        select(LearnedSkillCandidate).where(LearnedSkillCandidate.workspace_id == workspace.id)
    )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_import_from_text_empty_raises(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    service = SkillUploadService(db_session)
    with pytest.raises(ValueError):
        await service.import_from_text(workspace_id=workspace.id, content="   ")


@pytest.mark.asyncio
async def test_import_from_text_decollides_slug(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    db_session.add(LearnedSkillCandidate(
        workspace_id=workspace.id, slug="price-handling", name="x", trigger="", action="",
        status="proposed", source="learned",
    ))
    await db_session.flush()
    service = SkillUploadService(db_session)
    with patch("app.modules.brain.skill_upload.generate_structured_json",
               AsyncMock(return_value=_FAKE)):
        cand = await service.import_from_text(workspace_id=workspace.id, content="some text")
    assert cand.slug == "price-handling-2"


@pytest.mark.asyncio
async def test_import_then_approve_promotes_to_agent_skill(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    from app.models.agent_skill import AgentSkill
    from app.modules.agent_documents.contracts import AgentSkillInput
    from app.modules.agent_documents.renderer import render_skill_md
    from app.modules.agent_documents.service import AgentDocumentService

    service = SkillUploadService(db_session)
    with patch("app.modules.brain.skill_upload.generate_structured_json",
               AsyncMock(return_value=_FAKE)):
        cand = await service.import_from_text(workspace_id=workspace.id, content="paste")
    docs = AgentDocumentService(db_session)
    await docs.upsert_skill(workspace_id=workspace.id, payload=AgentSkillInput(
        slug=cand.slug, name=cand.name, description=cand.action, instructions=cand.action, when_to_use=cand.trigger,
    ))
    await db_session.flush()
    skill = (await db_session.execute(
        select(AgentSkill).where(AgentSkill.workspace_id == workspace.id, AgentSkill.slug == cand.slug)
    )).scalar_one()
    rendered = render_skill_md(skill)
    assert "SKILL.md" in rendered.markdown
    assert cand.name in rendered.markdown
