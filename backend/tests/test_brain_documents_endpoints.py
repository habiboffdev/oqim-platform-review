import pytest
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

_FAKE = {
    "sections": [
        {"section_key": "overview", "body": "Dresses.", "confidence": 0.9, "evidence_refs": []}
    ]
}


class TestGenerateBusinessMd:
    async def test_generate_business_md_returns_markdown(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        with patch(
            "app.modules.brain.business_document.generate_structured_json",
            AsyncMock(return_value=_FAKE),
        ):
            r = await client.post(
                "/api/brain/business-md/generate", headers=auth_headers
            )
        assert r.status_code == 200
        assert "BUSINESS.md" in r.json()["markdown"]

    async def test_generate_requires_auth(self, client: AsyncClient) -> None:
        r = await client.post("/api/brain/business-md/generate")
        assert r.status_code == 401


class TestGetBusinessMd:
    async def test_get_business_md_empty_state(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        r = await client.get("/api/brain/business-md", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["sections_used"] == 0

    async def test_get_requires_auth(self, client: AsyncClient) -> None:
        r = await client.get("/api/brain/business-md")
        assert r.status_code == 401

    async def test_business_md_workspace_isolation(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        auth_headers_b: dict[str, str],
    ) -> None:
        _fake_a = {
            "sections": [
                {
                    "section_key": "overview",
                    "body": "A-only-secret",
                    "confidence": 0.9,
                    "evidence_refs": [],
                }
            ]
        }
        with patch(
            "app.modules.brain.business_document.generate_structured_json",
            AsyncMock(return_value=_fake_a),
        ):
            await client.post(
                "/api/brain/business-md/generate", headers=auth_headers
            )
        r = await client.get("/api/brain/business-md", headers=auth_headers_b)
        assert r.status_code == 200
        assert "A-only-secret" not in r.json()["markdown"]


class TestEditBusinessMdSection:
    async def test_patch_known_section_persists_and_returns_rendered(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        r = await client.patch(
            "/api/brain/business-md/sections/overview",
            json={"body": "OWNER EDITED OVERVIEW"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "OWNER EDITED OVERVIEW" in r.json()["markdown"]

        # GET afterward also reflects the edit
        r2 = await client.get("/api/brain/business-md", headers=auth_headers)
        assert r2.status_code == 200
        assert "OWNER EDITED OVERVIEW" in r2.json()["markdown"]

    async def test_patch_unknown_section_key_returns_404(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        r = await client.patch(
            "/api/brain/business-md/sections/nonexistent_key",
            json={"body": "some text"},
            headers=auth_headers,
        )
        assert r.status_code == 404

    async def test_patch_requires_auth(self, client: AsyncClient) -> None:
        r = await client.patch(
            "/api/brain/business-md/sections/overview",
            json={"body": "text"},
        )
        assert r.status_code == 401

    async def test_owner_edit_survives_regen(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        # Edit the section as owner
        r = await client.patch(
            "/api/brain/business-md/sections/overview",
            json={"body": "OWNER SURVIVES REGEN"},
            headers=auth_headers,
        )
        assert r.status_code == 200

        # Regen tries to overwrite with LLM text
        _fake_regen = {
            "sections": [
                {"section_key": "overview", "body": "LLM WOULD OVERWRITE", "confidence": 0.9}
            ]
        }
        with patch(
            "app.modules.brain.business_document.generate_structured_json",
            AsyncMock(return_value=_fake_regen),
        ):
            r2 = await client.post("/api/brain/business-md/generate", headers=auth_headers)
        assert r2.status_code == 200
        assert "OWNER SURVIVES REGEN" in r2.json()["markdown"]
        assert "LLM WOULD OVERWRITE" not in r2.json()["markdown"]
