from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_skill import AgentSkill
from app.models.learned_skill_candidate import LearnedSkillCandidate
from app.models.workspace import Workspace

pytestmark = pytest.mark.asyncio


async def _candidate(db_session: AsyncSession, workspace: Workspace, *, slug="price-handling", status="proposed") -> LearnedSkillCandidate:
    c = LearnedSkillCandidate(
        workspace_id=workspace.id, slug=slug, name="Price handling",
        trigger="asks price", action="quote range", example_phrase="100-120k",
        dimension="price", confidence=0.9, evidence_conv_ids=[1], status=status, source="learned",
    )
    db_session.add(c)
    await db_session.flush()
    return c


class TestLearnSkills:
    async def test_learn_returns_report(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        from app.modules.brain.contracts import SkillLearnReport
        with patch("app.api.routes.brain_skills.SkillLearnerService") as mock_svc:
            mock_svc.return_value.learn = AsyncMock(return_value=SkillLearnReport(pairs_used=10, clusters=3, candidates=5))
            r = await client.post("/api/brain/skills/learn", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["candidates"] == 5

    async def test_learn_requires_auth(self, client: AsyncClient) -> None:
        r = await client.post("/api/brain/skills/learn")
        assert r.status_code == 401


class TestListCandidates:
    async def test_list_returns_proposed_only(
        self, client: AsyncClient, auth_headers: dict[str, str],
        db_session: AsyncSession, workspace: Workspace,
    ) -> None:
        await _candidate(db_session, workspace, slug="price-handling", status="proposed")
        await _candidate(db_session, workspace, slug="old-one", status="rejected")
        r = await client.get("/api/brain/skills/candidates", headers=auth_headers)
        assert r.status_code == 200
        slugs = [c["slug"] for c in r.json()["items"]]
        assert "price-handling" in slugs
        assert "old-one" not in slugs

    async def test_list_isolated_by_workspace(
        self, client: AsyncClient, auth_headers_b: dict[str, str],
        db_session: AsyncSession, workspace: Workspace,
    ) -> None:
        await _candidate(db_session, workspace, slug="a-secret")
        r = await client.get("/api/brain/skills/candidates", headers=auth_headers_b)
        assert r.status_code == 200
        assert r.json()["items"] == []


class TestApproveReject:
    async def test_approve_creates_agent_skill_and_marks_approved(
        self, client: AsyncClient, auth_headers: dict[str, str],
        db_session: AsyncSession, workspace: Workspace,
    ) -> None:
        c = await _candidate(db_session, workspace, slug="price-handling")
        r = await client.post(f"/api/brain/skills/candidates/{c.id}/approve", headers=auth_headers)
        assert r.status_code == 200
        skill = (await db_session.execute(
            _select(AgentSkill).where(AgentSkill.workspace_id == workspace.id, AgentSkill.slug == "price-handling")
        )).scalar_one()
        assert skill.when_to_use == "asks price"
        await db_session.refresh(c)
        assert c.status == "approved"

    async def test_edit_and_approve_overrides(
        self, client: AsyncClient, auth_headers: dict[str, str],
        db_session: AsyncSession, workspace: Workspace,
    ) -> None:
        c = await _candidate(db_session, workspace, slug="price-handling")
        r = await client.post(
            f"/api/brain/skills/candidates/{c.id}/approve",
            json={"name": "Narx bilan ishlash", "action": "narx oralig'ini ayting"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        skill = (await db_session.execute(
            _select(AgentSkill).where(AgentSkill.workspace_id == workspace.id, AgentSkill.slug == "price-handling")
        )).scalar_one()
        assert skill.name == "Narx bilan ishlash"

    async def test_reject_marks_rejected(
        self, client: AsyncClient, auth_headers: dict[str, str],
        db_session: AsyncSession, workspace: Workspace,
    ) -> None:
        c = await _candidate(db_session, workspace, slug="price-handling")
        r = await client.post(f"/api/brain/skills/candidates/{c.id}/reject", headers=auth_headers)
        assert r.status_code == 200
        await db_session.refresh(c)
        assert c.status == "rejected"

    async def test_approve_cross_workspace_returns_404(
        self, client: AsyncClient, auth_headers_b: dict[str, str],
        db_session: AsyncSession, workspace: Workspace,
    ) -> None:
        c = await _candidate(db_session, workspace)
        r = await client.post(f"/api/brain/skills/candidates/{c.id}/approve", headers=auth_headers_b)
        assert r.status_code == 404

    async def test_approve_requires_auth(
        self, client: AsyncClient, db_session: AsyncSession, workspace: Workspace
    ) -> None:
        c = await _candidate(db_session, workspace)
        r = await client.post(f"/api/brain/skills/candidates/{c.id}/approve")
        assert r.status_code == 401


_FAKE_SYNTH = {
    "slug": "price-handling", "name": "Price handling", "trigger": "asks price",
    "action": "quote a range", "example_phrase": "100-120k", "dimension": "price", "confidence": 0.8,
}


class TestUploadSkill:
    async def test_upload_creates_upload_candidate(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        with patch("app.modules.brain.skill_upload.generate_structured_json",
                   AsyncMock(return_value=_FAKE_SYNTH)):
            r = await client.post(
                "/api/brain/skills/upload", json={"content": "--- a SKILL.md ---"}, headers=auth_headers
            )
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "upload"
        assert body["slug"] == "price-handling"

    async def test_uploaded_candidate_appears_in_list(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        with patch("app.modules.brain.skill_upload.generate_structured_json",
                   AsyncMock(return_value=_FAKE_SYNTH)):
            await client.post("/api/brain/skills/upload", json={"content": "paste"}, headers=auth_headers)
        r = await client.get("/api/brain/skills/candidates", headers=auth_headers)
        assert r.status_code == 200
        slugs = [c["slug"] for c in r.json()["items"]]
        assert "price-handling" in slugs

    async def test_upload_empty_content_returns_422(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        with patch("app.modules.brain.skill_upload.generate_structured_json", AsyncMock()) as mock_llm:
            r = await client.post("/api/brain/skills/upload", json={"content": "   "}, headers=auth_headers)
        assert r.status_code == 422
        mock_llm.assert_not_awaited()

    async def test_upload_requires_auth(self, client: AsyncClient) -> None:
        r = await client.post("/api/brain/skills/upload", json={"content": "x"})
        assert r.status_code == 401
