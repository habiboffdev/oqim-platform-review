"""
Agent endpoint tests — CRUD, default agent, trust modes, channel config, isolation.
"""

from httpx import AsyncClient

from app.models.agent import Agent


class TestListAgents:
    async def test_list_with_default_agent(
        self, client: AsyncClient, agent: Agent, auth_headers: dict
    ):
        res = await client.get("/api/agents", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["is_default"] is True
        assert data[0]["name"] == "Test AI"

    async def test_list_excludes_inactive(
        self, client: AsyncClient, agent: Agent, auth_headers: dict, db_session
    ):
        agent.is_active = False
        await db_session.flush()

        res = await client.get("/api/agents", headers=auth_headers)
        assert res.json() == []

    async def test_workspace_isolation(
        self, client: AsyncClient, agent: Agent, auth_headers_b: dict
    ):
        res = await client.get("/api/agents", headers=auth_headers_b)
        assert res.json() == []


class TestCreateAgent:
    async def test_create_success(self, client: AsyncClient, auth_headers: dict):
        res = await client.post("/api/agents", json={
            "name": "Support Bot",
            "persona": {"role": "Customer support", "tone": "Professional"},
            "instructions": "Handle returns and complaints.",
            "trust_mode": "autonomous",
            "auto_send_threshold": 0.90,
        }, headers=auth_headers)
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == "Support Bot"
        # Legacy 'autonomous' coerces to the two-state model -> 'disabled'.
        assert data["trust_mode"] == "disabled"
        assert data["auto_send_threshold"] == 0.90
        assert data["persona"]["role"] == "Customer support"

    async def test_create_with_channel_config(self, client: AsyncClient, auth_headers: dict):
        res = await client.post("/api/agents", json={
            "name": "Channel Bot",
            "channel_config": {"mode": "channel", "chat_ids": [-1001234567890]},
        }, headers=auth_headers)
        assert res.status_code == 201
        assert res.json()["channel_config"]["mode"] == "channel"
        assert -1001234567890 in res.json()["channel_config"]["chat_ids"]

    async def test_create_with_tools_config(self, client: AsyncClient, auth_headers: dict):
        res = await client.post("/api/agents", json={
            "name": "Limited Bot",
            "tools_config": {"enabled_tools": ["knowledge_search_catalog"]},
        }, headers=auth_headers)
        assert res.status_code == 201
        assert res.json()["tools_config"]["enabled_tools"] == ["knowledge_search_catalog"]

    async def test_create_with_example_responses(self, client: AsyncClient, auth_headers: dict):
        res = await client.post("/api/agents", json={
            "name": "Trained Bot",
            "example_responses": [
                {"input": "iPhone bormi?", "output": "Ha, iPhone 15 bor!"},
                {"input": "Narxi qancha?", "output": "12,500,000 UZS dan."},
            ],
        }, headers=auth_headers)
        assert res.status_code == 201
        assert len(res.json()["example_responses"]) == 2

    async def test_create_minimal(self, client: AsyncClient, auth_headers: dict):
        res = await client.post("/api/agents", json={
            "name": "Minimal Bot",
        }, headers=auth_headers)
        assert res.status_code == 201
        data = res.json()
        assert data["trust_mode"] == "disabled"  # default
        assert data["auto_send_threshold"] == 0.85  # default


class TestUpdateAgent:
    async def test_update_trust_mode(
        self, client: AsyncClient, agent: Agent, auth_headers: dict
    ):
        res = await client.put(f"/api/agents/{agent.id}", json={
            "trust_mode": "autopilot",
        }, headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["trust_mode"] == "autopilot"

    async def test_update_threshold(
        self, client: AsyncClient, agent: Agent, auth_headers: dict
    ):
        res = await client.put(f"/api/agents/{agent.id}", json={
            "auto_send_threshold": 0.95,
        }, headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["auto_send_threshold"] == 0.95

    async def test_update_escalation_topics(
        self, client: AsyncClient, agent: Agent, auth_headers: dict
    ):
        res = await client.put(f"/api/agents/{agent.id}", json={
            "escalation_topics": ["refund", "complaint", "legal"],
        }, headers=auth_headers)
        assert res.status_code == 200
        assert "refund" in res.json()["escalation_topics"]

    async def test_update_other_workspace_404(
        self, client: AsyncClient, agent: Agent, auth_headers_b: dict
    ):
        res = await client.put(f"/api/agents/{agent.id}", json={
            "name": "Hacked Bot",
        }, headers=auth_headers_b)
        assert res.status_code == 404


class TestDeleteAgent:
    async def test_delete_soft_deletes(
        self, client: AsyncClient, agent: Agent, auth_headers: dict
    ):
        """Delete should soft-delete (is_active=False), not hard delete."""
        res = await client.delete(f"/api/agents/{agent.id}", headers=auth_headers)
        assert res.status_code == 204

        # Agent should be gone from list (inactive)
        res = await client.get("/api/agents", headers=auth_headers)
        assert len(res.json()) == 0


class TestAgentValidation:
    async def test_create_rejects_deleted_undo_delay_field(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ):
        res = await client.post("/api/agents", json={
            "name": "Delay Bot",
            "undo_delay_seconds": 30,
        }, headers=auth_headers)
        assert res.status_code == 422

    async def test_update_rejects_deleted_undo_delay_field(
        self, client: AsyncClient, agent: Agent, auth_headers: dict
    ):
        res = await client.put(f"/api/agents/{agent.id}", json={
            "undo_delay_seconds": 45,
        }, headers=auth_headers)
        assert res.status_code == 422

    async def test_get_does_not_expose_undo_delay(
        self, client: AsyncClient, agent: Agent, auth_headers: dict
    ):
        res = await client.get(f"/api/agents/{agent.id}", headers=auth_headers)
        assert res.status_code == 200
        assert "undo_delay_seconds" not in res.json()

    async def test_create_with_agent_type_business(self, client: AsyncClient, auth_headers: dict):
        res = await client.post("/api/agents", json={
            "name": "BI Bot",
            "agent_type": "business",
            "contact_scope": "all",
        }, headers=auth_headers)
        assert res.status_code == 201
        data = res.json()
        assert data["agent_type"] == "business"
        assert data["contact_scope"] == "all"

    async def test_create_invalid_agent_type_422(self, client: AsyncClient, auth_headers: dict):
        res = await client.post("/api/agents", json={
            "name": "Bad Type Bot",
            "agent_type": "unknown",
        }, headers=auth_headers)
        assert res.status_code == 422

    async def test_create_invalid_contact_scope_422(self, client: AsyncClient, auth_headers: dict):
        res = await client.post("/api/agents", json={
            "name": "Bad Scope Bot",
            "contact_scope": "none",
        }, headers=auth_headers)
        assert res.status_code == 422

    async def test_default_agent_type_is_seller(self, client: AsyncClient, auth_headers: dict):
        res = await client.post("/api/agents", json={
            "name": "Default Type Bot",
        }, headers=auth_headers)
        assert res.status_code == 201
        assert res.json()["agent_type"] == "seller"
        assert res.json()["contact_scope"] == "business"
        assert "undo_delay_seconds" not in res.json()


class TestSetDefaultAgent:
    async def test_set_default(
        self, client: AsyncClient, agent: Agent, auth_headers: dict
    ):
        # Create a second agent
        res = await client.post("/api/agents", json={
            "name": "Secondary Bot",
        }, headers=auth_headers)
        second_id = res.json()["id"]

        # Set it as default
        res = await client.post(f"/api/agents/{second_id}/default", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["is_default"] is True

        # Original should no longer be default
        res = await client.get("/api/agents", headers=auth_headers)
        agents = res.json()
        for a in agents:
            if a["id"] == agent.id:
                assert a["is_default"] is False
            if a["id"] == second_id:
                assert a["is_default"] is True
