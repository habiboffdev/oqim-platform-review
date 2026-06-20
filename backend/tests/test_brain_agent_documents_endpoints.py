import pytest
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.workspace import Workspace

pytestmark = pytest.mark.asyncio

_FAKE = {
    "sections": [
        {"section_key": "role_mission", "body": "Sotuvchi agent.", "confidence": 0.9, "evidence_refs": []}
    ]
}


async def _make_agent(db_session: AsyncSession, workspace: Workspace, *, name: str = "Sotuvchi") -> Agent:
    agent = Agent(workspace_id=workspace.id, name=name)
    db_session.add(agent)
    await db_session.flush()
    return agent


class TestGenerateAgentMd:
    async def test_generate_returns_markdown(
        self, client: AsyncClient, auth_headers: dict[str, str],
        db_session: AsyncSession, workspace: Workspace,
    ) -> None:
        agent = await _make_agent(db_session, workspace, name="Madelyn Seller")
        with patch(
            "app.modules.brain.agent_document.generate_structured_json",
            AsyncMock(return_value=_FAKE),
        ):
            r = await client.post(
                f"/api/brain/agents/{agent.id}/agent-md/generate", headers=auth_headers
            )
        assert r.status_code == 200
        assert "AGENT.md — Madelyn Seller" in r.json()["markdown"]

    async def test_generate_requires_auth(
        self, client: AsyncClient, db_session: AsyncSession, workspace: Workspace
    ) -> None:
        agent = await _make_agent(db_session, workspace)
        r = await client.post(f"/api/brain/agents/{agent.id}/agent-md/generate")
        assert r.status_code == 401

    async def test_generate_cross_workspace_agent_returns_404(
        self, client: AsyncClient, auth_headers: dict[str, str],
        db_session: AsyncSession, workspace_b: Workspace,
    ) -> None:
        agent_b = await _make_agent(db_session, workspace_b, name="B agent")
        with patch(
            "app.modules.brain.agent_document.generate_structured_json",
            AsyncMock(return_value=_FAKE),
        ):
            r = await client.post(
                f"/api/brain/agents/{agent_b.id}/agent-md/generate", headers=auth_headers
            )
        assert r.status_code == 404


class TestGetAgentMd:
    async def test_get_empty_state(
        self, client: AsyncClient, auth_headers: dict[str, str],
        db_session: AsyncSession, workspace: Workspace,
    ) -> None:
        agent = await _make_agent(db_session, workspace)
        r = await client.get(f"/api/brain/agents/{agent.id}/agent-md", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["sections_used"] == 0

    async def test_get_cross_workspace_agent_returns_404(
        self, client: AsyncClient, auth_headers: dict[str, str],
        db_session: AsyncSession, workspace_b: Workspace,
    ) -> None:
        agent_b = await _make_agent(db_session, workspace_b)
        r = await client.get(f"/api/brain/agents/{agent_b.id}/agent-md", headers=auth_headers)
        assert r.status_code == 404


class TestEditAgentMdSection:
    async def test_patch_known_section_persists(
        self, client: AsyncClient, auth_headers: dict[str, str],
        db_session: AsyncSession, workspace: Workspace,
    ) -> None:
        agent = await _make_agent(db_session, workspace)
        r = await client.patch(
            f"/api/brain/agents/{agent.id}/agent-md/sections/role_mission",
            json={"body": "OWNER EDITED ROLE"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "OWNER EDITED ROLE" in r.json()["markdown"]

    async def test_patch_unknown_section_returns_404(
        self, client: AsyncClient, auth_headers: dict[str, str],
        db_session: AsyncSession, workspace: Workspace,
    ) -> None:
        agent = await _make_agent(db_session, workspace)
        r = await client.patch(
            f"/api/brain/agents/{agent.id}/agent-md/sections/nope",
            json={"body": "x"},
            headers=auth_headers,
        )
        assert r.status_code == 404

    async def test_patch_cross_workspace_agent_returns_404(
        self, client: AsyncClient, auth_headers: dict[str, str],
        db_session: AsyncSession, workspace_b: Workspace,
    ) -> None:
        agent_b = await _make_agent(db_session, workspace_b)
        r = await client.patch(
            f"/api/brain/agents/{agent_b.id}/agent-md/sections/role_mission",
            json={"body": "x"},
            headers=auth_headers,
        )
        assert r.status_code == 404

    async def test_patch_requires_auth(
        self, client: AsyncClient, db_session: AsyncSession, workspace: Workspace
    ) -> None:
        agent = await _make_agent(db_session, workspace)
        r = await client.patch(
            f"/api/brain/agents/{agent.id}/agent-md/sections/role_mission",
            json={"body": "x"},
        )
        assert r.status_code == 401
