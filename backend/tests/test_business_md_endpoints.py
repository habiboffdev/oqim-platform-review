"""Phase 3 task #20 — BUSINESS.md API endpoints.

GET /api/business-brain/business-md returns the rendered Markdown + section
rows. POST /api/business-brain/business-md/sections upserts a section through
AgentDocumentService with generated_by="owner". Workspace isolation is enforced.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.agent_document import AgentDocumentSection


pytestmark = pytest.mark.asyncio


class TestGetBusinessMd:
    async def test_returns_empty_document_when_no_sections(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        response = await client.get(
            "/api/business-brain/business-md", headers=auth_headers
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["schema_version"] == "business_md_document.v1"
        assert payload["sections"] == []
        rendered = payload["rendered"]
        assert rendered["kind"] == "business"
        assert "BUSINESS.md" in rendered["title"]
        assert "BUSINESS.md" in rendered["markdown"]
        assert rendered["sections_used"] == 0

    async def test_renders_sections_in_order(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace,
    ) -> None:
        db_session.add_all(
            [
                AgentDocumentSection(
                    workspace_id=workspace.id,
                    document_kind="business",
                    subject_type="workspace",
                    subject_id=None,
                    section_key="rules",
                    title="Qoidalar",
                    body="Qaytarish 3 kun ichida.",
                    order_index=30,
                ),
                AgentDocumentSection(
                    workspace_id=workspace.id,
                    document_kind="business",
                    subject_type="workspace",
                    subject_id=None,
                    section_key="identity",
                    title="Identifikator",
                    body="Toshkent shahri, Yunusobod.",
                    order_index=0,
                ),
            ]
        )
        await db_session.flush()

        response = await client.get(
            "/api/business-brain/business-md", headers=auth_headers
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["sections"]) == 2
        # Sections come back ordered by order_index.
        keys = [section["section_key"] for section in payload["sections"]]
        assert keys == ["identity", "rules"]
        # Markdown also reflects the ordering.
        markdown = payload["rendered"]["markdown"]
        assert markdown.index("Identifikator") < markdown.index("Qoidalar")

    async def test_requires_auth(self, client: AsyncClient) -> None:
        response = await client.get("/api/business-brain/business-md")
        assert response.status_code == 401

    async def test_workspace_isolation(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        auth_headers_b: dict[str, str],
        db_session,
        workspace,
    ) -> None:
        db_session.add(
            AgentDocumentSection(
                workspace_id=workspace.id,
                document_kind="business",
                subject_type="workspace",
                subject_id=None,
                section_key="identity",
                title="Identifikator",
                body="Workspace A specific content.",
                order_index=0,
            )
        )
        await db_session.flush()

        response_a = await client.get(
            "/api/business-brain/business-md", headers=auth_headers
        )
        response_b = await client.get(
            "/api/business-brain/business-md", headers=auth_headers_b
        )

        assert len(response_a.json()["sections"]) == 1
        assert response_b.json()["sections"] == []


class TestUpsertBusinessMdSection:
    async def test_creates_new_section_with_owner_attribution(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = await client.post(
            "/api/business-brain/business-md/sections",
            headers=auth_headers,
            json={
                "section_key": "identity",
                "title": "Identifikator",
                "body": "Toshkent shahri.",
                "order_index": 0,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        section = payload["section"]
        assert section["section_key"] == "identity"
        assert section["body"] == "Toshkent shahri."
        assert section["generated_by"] == "owner"

    async def test_overwrites_existing_section_in_place(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        first = await client.post(
            "/api/business-brain/business-md/sections",
            headers=auth_headers,
            json={
                "section_key": "rules",
                "title": "Qoidalar",
                "body": "Birinchi versiya.",
            },
        )
        second = await client.post(
            "/api/business-brain/business-md/sections",
            headers=auth_headers,
            json={
                "section_key": "rules",
                "title": "Qoidalar",
                "body": "Ikkinchi versiya.",
            },
        )
        assert first.json()["section"]["id"] == second.json()["section"]["id"]
        assert second.json()["section"]["body"] == "Ikkinchi versiya."

    async def test_rejects_unauth(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/business-brain/business-md/sections",
            json={"section_key": "identity", "title": "x"},
        )
        assert response.status_code == 401

    async def test_validates_payload(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = await client.post(
            "/api/business-brain/business-md/sections",
            headers=auth_headers,
            json={"section_key": "", "title": "x"},
        )
        assert response.status_code == 422

    async def test_workspace_isolation(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        auth_headers_b: dict[str, str],
    ) -> None:
        await client.post(
            "/api/business-brain/business-md/sections",
            headers=auth_headers,
            json={
                "section_key": "identity",
                "title": "Identifikator",
                "body": "Workspace A.",
            },
        )
        await client.post(
            "/api/business-brain/business-md/sections",
            headers=auth_headers_b,
            json={
                "section_key": "identity",
                "title": "Identifikator",
                "body": "Workspace B.",
            },
        )
        response_a = await client.get(
            "/api/business-brain/business-md", headers=auth_headers
        )
        response_b = await client.get(
            "/api/business-brain/business-md", headers=auth_headers_b
        )
        assert response_a.json()["sections"][0]["body"] == "Workspace A."
        assert response_b.json()["sections"][0]["body"] == "Workspace B."
