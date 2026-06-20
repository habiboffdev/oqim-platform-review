"""Phase 4 — Intelligence API for Skills + Agents + AGENT.md sections."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.agent import Agent
from app.models.agent_document import AgentDocumentSection
from app.models.agent_skill import AgentSkill
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.tool_grant import ToolGrant
from app.models.trigger import Trigger
from app.models.workspace import Workspace

pytestmark = pytest.mark.asyncio


class TestSkillsCatalog:
    async def test_list_returns_empty_for_fresh_workspace(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        response = await client.get("/api/intelligence/skills", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == "intelligence_skills.v1"
        assert body["items"] == []

    async def test_upsert_creates_then_updates_in_place(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        first = await client.post(
            "/api/intelligence/skills",
            headers=auth_headers,
            json={
                "slug": "catalog-lookup",
                "name": "Catalog lookup",
                "description": "Resolve product titles to canonical SKUs.",
            },
        )
        assert first.status_code == 200
        first_id = first.json()["skill"]["id"]

        second = await client.post(
            "/api/intelligence/skills",
            headers=auth_headers,
            json={
                "slug": "catalog-lookup",
                "name": "Catalog lookup",
                "description": "Now also resolves variants.",
                "tools": ["knowledge_search_catalog", "knowledge_explain_sources"],
            },
        )
        assert second.status_code == 200
        assert second.json()["skill"]["id"] == first_id
        assert second.json()["skill"]["tools"] == [
            "knowledge_search_catalog",
            "knowledge_explain_sources",
        ]

    async def test_filter_by_agent_id(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent_a = Agent(workspace_id=workspace.id, name="A")
        agent_b = Agent(workspace_id=workspace.id, name="B")
        db_session.add_all([agent_a, agent_b])
        await db_session.flush()

        db_session.add_all(
            [
                AgentSkill(
                    workspace_id=workspace.id, agent_id=agent_a.id, slug="for-a", name="A"
                ),
                AgentSkill(
                    workspace_id=workspace.id, agent_id=agent_b.id, slug="for-b", name="B"
                ),
            ]
        )
        await db_session.flush()

        response = await client.get(
            f"/api/intelligence/skills?agent_id={agent_a.id}", headers=auth_headers
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["slug"] == "for-a"

    async def test_delete_skill(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        await client.post(
            "/api/intelligence/skills",
            headers=auth_headers,
            json={"slug": "to-delete", "name": "Doomed"},
        )
        response = await client.delete(
            "/api/intelligence/skills/to-delete", headers=auth_headers
        )
        assert response.status_code == 204
        # Subsequent delete on the same slug returns 404.
        again = await client.delete(
            "/api/intelligence/skills/to-delete", headers=auth_headers
        )
        assert again.status_code == 404

    async def test_workspace_isolation_for_skills(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        auth_headers_b: dict[str, str],
    ) -> None:
        await client.post(
            "/api/intelligence/skills",
            headers=auth_headers,
            json={"slug": "a-only", "name": "A only"},
        )
        response_b = await client.get(
            "/api/intelligence/skills", headers=auth_headers_b
        )
        assert response_b.json()["items"] == []

    async def test_requires_auth(self, client: AsyncClient) -> None:
        response = await client.get("/api/intelligence/skills")
        assert response.status_code == 401


class TestAgentsListAndDetail:
    async def test_create_custom_agent_package_directly_creates_agent(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        response = await client.post(
            "/api/intelligence/agents/custom",
            headers=auth_headers,
            json={
                "name": "Uchrashuv agenti",
                "agent_kind": "support",
                "mission": "Mijoz uchrashuv so'raganda vaqt va keyingi qadamni taklif qiladi.",
                "permission_mode": "ask_always",
                "brain_scopes": ["knowledge", "rules", "voice"],
                "tool_scopes": ["telegram.send_message"],
                "trigger_sources": ["channel_message_received"],
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["schema_version"] == "custom_agent_package.v1"
        assert body["created"] is True
        agent_summary = body["agent"]
        assert agent_summary["agent_type"] == "support"
        assert agent_summary["id"] > 0
        assert body["document_section_count"] >= 6
        assert body["skill_count"] == 1
        assert body["tool_grant_count"] == 2  # read added as trigger invariant
        assert body["trigger_count"] == 1
        assert body["package_key"].startswith("custom:")

        # Agent exists immediately — no approval round-trip.
        agent = await db_session.scalar(
            select(Agent).where(
                Agent.workspace_id == workspace.id,
                Agent.name == "Uchrashuv agenti",
            )
        )
        assert agent is not None
        assert agent.agent_type == "support"

        detail = await client.get(
            f"/api/intelligence/agents/{agent.id}", headers=auth_headers
        )
        assert detail.status_code == 200
        markdown = detail.json()["rendered"]["markdown"]
        assert "## Rol" in markdown
        assert "## Skills" in markdown

    async def test_create_custom_agent_is_idempotent(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        payload = {
            "name": "Kanal kuzatuvchi",
            "agent_kind": "follow_up",
            "mission": "Kanal postlaridan foydali biznes ishlarini taklif qiladi.",
            "permission_mode": "ask_always",
            "idempotency_key": "custom-agent-test-key",
        }
        first = await client.post(
            "/api/intelligence/agents/custom", headers=auth_headers, json=payload
        )
        second = await client.post(
            "/api/intelligence/agents/custom", headers=auth_headers, json=payload
        )

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["created"] is True
        assert second.json()["created"] is False
        assert first.json()["agent"]["id"] == second.json()["agent"]["id"]
        assert first.json()["package_key"] == second.json()["package_key"]
        agents = (
            await db_session.scalars(
                select(Agent).where(
                    Agent.workspace_id == workspace.id,
                    Agent.name == "Kanal kuzatuvchi",
                )
            )
        ).all()
        assert len(agents) == 1

    async def test_list_returns_workspace_agents_with_skill_count(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent = Agent(workspace_id=workspace.id, name="Seller", agent_type="seller")
        db_session.add(agent)
        await db_session.flush()
        db_session.add(
            AgentSkill(
                workspace_id=workspace.id,
                agent_id=agent.id,
                slug="catalog-lookup",
                name="Catalog lookup",
            )
        )
        await db_session.flush()

        response = await client.get("/api/intelligence/agents", headers=auth_headers)
        assert response.status_code == 200
        items = response.json()["items"]
        assert any(item["name"] == "Seller" and item["skill_count"] == 1 for item in items)

    async def test_detail_renders_agent_md_with_sections_and_skills(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent = Agent(workspace_id=workspace.id, name="Seller", agent_type="seller")
        db_session.add(agent)
        await db_session.flush()
        db_session.add_all(
            [
                AgentDocumentSection(
                    workspace_id=workspace.id,
                    document_kind="agent",
                    subject_type="agent",
                    subject_id=agent.id,
                    section_key="role",
                    title="Rol",
                    body="Sotuv agenti sifatida mijozlarga javob beradi.",
                    order_index=0,
                ),
                AgentDocumentSection(
                    workspace_id=workspace.id,
                    document_kind="agent",
                    subject_type="agent",
                    subject_id=agent.id,
                    section_key="runtime_config",
                    title="Runtime config",
                    body="Permission mode: full_access\nTool scopes: telegram.read_messages",
                    order_index=90,
                ),
                AgentSkill(
                    workspace_id=workspace.id,
                    agent_id=agent.id,
                    slug="catalog-lookup",
                    name="Catalog lookup",
                    description="Resolve product titles.",
                ),
                ToolGrant(
                    workspace_id=workspace.id,
                    agent_id=agent.id,
                    scope="telegram.read_messages",
                ),
                Trigger(
                    workspace_id=workspace.id,
                    owner_agent_id=agent.id,
                    event_source="channel_message_received",
                    action_proposal_type="conversation.propose_reply",
                    idempotency_key="test-agent-detail-trigger",
                ),
                CommercialActionProposalRecord(
                    proposal_id="agent-action-1",
                    workspace_id=workspace.id,
                    conversation_id=1,
                    customer_id=1,
                    action_type="send_reply",
                    lifecycle_state="waiting_approval",
                    execution_mode="approval_required",
                    risk_level="low",
                    requires_approval=True,
                    priority="medium",
                    confidence=0.9,
                    reason_code="sales_followup",
                    source_refs=[],
                    payload={"agent_id": agent.id},
                    idempotency_key="agent-action-1",
                    raw_proposal={"agent_id": agent.id},
                ),
            ]
        )
        await db_session.flush()

        response = await client.get(
            f"/api/intelligence/agents/{agent.id}", headers=auth_headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["agent"]["name"] == "Seller"
        assert body["agent"]["permission_mode"] == "ask_always"
        assert body["enforced_config"]["permission_mode"] == "ask_always"
        assert len(body["sections"]) == 2
        assert len(body["skills"]) == 1
        assert len(body["tool_grants"]) == 1
        assert len(body["triggers"]) == 1
        assert len(body["recent_actions"]) == 1
        assert body["drift_warnings"][0]["code"] == "permission_mode_drift"
        markdown = body["rendered"]["markdown"]
        assert "## Rol" in markdown
        assert "## Skills" in markdown
        assert "catalog-lookup" in markdown

    async def test_detail_404_for_other_workspace(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace_b: Workspace,
    ) -> None:
        foreign_agent = Agent(workspace_id=workspace_b.id, name="Foreign")
        db_session.add(foreign_agent)
        await db_session.flush()

        response = await client.get(
            f"/api/intelligence/agents/{foreign_agent.id}", headers=auth_headers
        )
        assert response.status_code == 404

    async def test_create_sets_agent_type_from_kind(
        self, db_session, workspace: Workspace
    ) -> None:
        from app.modules.workspace_os.custom_agent import (
            CustomAgentPackageInput,
            CustomAgentPackageService,
        )

        service = CustomAgentPackageService(db_session)
        result = await service.create(
            workspace_id=workspace.id,
            payload=CustomAgentPackageInput(
                name="Qollab agent",
                mission="Mijoz muammosini hal qiladi va kerak bo'lsa egaga uzatadi.",
                agent_kind="support",
            ),
        )
        assert result.agent.agent_type == "support"

    async def test_create_uses_reviewed_sections_verbatim(
        self, db_session, workspace: Workspace
    ) -> None:
        from app.modules.agent_documents.service import AgentDocumentService
        from app.modules.workspace_os.custom_agent import (
            CustomAgentPackageInput,
            CustomAgentPackageService,
            WizardSectionDraft,
        )

        service = CustomAgentPackageService(db_session)
        result = await service.create(
            workspace_id=workspace.id,
            payload=CustomAgentPackageInput(
                name="Tahrir agent",
                mission="Asl vazifa matni — bu override qilinishi kerak.",
                agent_kind="custom",
                sections=[
                    WizardSectionDraft(
                        section_key="role",
                        title="Rol",
                        body="EGA TAHRIRLAGAN ROL MATNI",
                        order_index=10,
                    ),
                    WizardSectionDraft(
                        section_key="never_guess",
                        title="Nimani taxmin qilmaydi",
                        body="EGA TAHRIRLAGAN CHEKLOV",
                        order_index=14,
                    ),
                ],
            ),
        )
        sections = await AgentDocumentService(db_session).list_sections(
            workspace_id=workspace.id,
            document_kind="agent",
            subject_type="agent",
            subject_id=result.agent.id,
        )
        bodies = {s.section_key: s.body for s in sections}
        assert bodies["role"] == "EGA TAHRIRLAGAN ROL MATNI"
        assert bodies["never_guess"] == "EGA TAHRIRLAGAN CHEKLOV"

    async def test_same_inputs_different_kind_are_not_deduplicated(
        self, db_session, workspace: Workspace
    ) -> None:
        from app.modules.workspace_os.custom_agent import (
            CustomAgentPackageInput,
            CustomAgentPackageService,
        )

        service = CustomAgentPackageService(db_session)
        base = dict(
            name="Bir xil agent",
            mission="Bir xil vazifa matni, faqat tur farq qiladi.",
            brain_scopes=["knowledge", "rules"],
            tool_scopes=["telegram.read_messages"],
            trigger_sources=[],
        )
        seller = await service.create(
            workspace_id=workspace.id,
            payload=CustomAgentPackageInput(agent_kind="seller", **base),
        )
        support = await service.create(
            workspace_id=workspace.id,
            payload=CustomAgentPackageInput(agent_kind="support", **base),
        )
        assert seller.agent.id != support.agent.id
        assert seller.agent.agent_type == "seller"
        assert support.agent.agent_type == "support"


class TestToolGrantList:
    async def test_tool_catalog_returns_telegram_mcp_tool_contract(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        response = await client.get(
            "/api/intelligence/tool-catalog?connector=telegram", headers=auth_headers
        )

        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == "intelligence_tool_catalog.v1"
        tools = {item["scope"]: item for item in body["items"]}
        assert set(tools) == {
            "telegram.read_messages",
            "telegram.send_message",
            "telegram.edit_message",
            "telegram.watch_channel",
            "telegram.fetch_media",
            "telegram.sync_history",
        }
        assert tools["telegram.send_message"]["runtime_boundary"] == "telegram_tool_runtime"
        assert tools["telegram.send_message"]["mutates_external_state"] is True
        assert tools["telegram.send_message"]["requires_action_proposal"] is True
        assert tools["telegram.send_message"]["risk_level"] == "high"
        assert tools["telegram.edit_message"]["label_uz"] == "Yuborilgan javobni tahrirlash"
        assert tools["telegram.read_messages"]["mutates_external_state"] is False
        assert tools["telegram.read_messages"]["requires_action_proposal"] is False

    async def test_returns_empty_for_fresh_workspace(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        response = await client.get(
            "/api/intelligence/tool-grants", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_returns_grants_for_workspace_only(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        auth_headers_b: dict[str, str],
        db_session,
        workspace: Workspace,
        workspace_b: Workspace,
    ) -> None:
        from app.models.tool_grant import ToolGrant
        agent_a = Agent(workspace_id=workspace.id, name="Seller A")
        agent_b = Agent(workspace_id=workspace_b.id, name="Seller B")
        db_session.add_all([agent_a, agent_b])
        await db_session.flush()
        db_session.add_all(
            [
                ToolGrant(
                    workspace_id=workspace.id,
                    agent_id=agent_a.id,
                    scope="telegram.send_message",
                ),
                ToolGrant(
                    workspace_id=workspace_b.id,
                    agent_id=agent_b.id,
                    scope="telegram.send_message",
                ),
            ]
        )
        await db_session.flush()

        response_a = await client.get(
            "/api/intelligence/tool-grants", headers=auth_headers
        )
        response_b = await client.get(
            "/api/intelligence/tool-grants", headers=auth_headers_b
        )
        assert len(response_a.json()["items"]) == 1
        assert len(response_b.json()["items"]) == 1
        grant_item = response_a.json()["items"][0]
        assert grant_item["workspace_id"] == workspace.id
        assert grant_item["connector"] == "telegram"
        assert grant_item["scope_label"] == "Javob yuborish"
        assert "yuboradi" in grant_item["scope_description"]
        assert grant_item["risk_level"] == "high"
        assert grant_item["mutates_external_state"] is True
        assert grant_item["requires_action_proposal"] is True
        assert grant_item["runtime_boundary"] == "telegram_tool_runtime"

    async def test_tool_grant_changes_are_action_proposals_before_runtime_mutation(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent = Agent(
            workspace_id=workspace.id,
            name="Media helper",
            agent_type="custom",
            tools_config={"tool_scopes": []},
        )
        db_session.add(agent)
        await db_session.flush()

        grant_response = await client.post(
            f"/api/intelligence/agents/{agent.id}/tool-grants/propose",
            headers=auth_headers,
            json={
                "action": "grant",
                "scope": "telegram.fetch_media",
                "reason": "Receipts and product images must be opened by the agent.",
            },
        )
        assert grant_response.status_code == 201, grant_response.text
        grant_body = grant_response.json()
        assert grant_body["schema_version"] == "agent_tool_grant_proposal.v1"
        assert grant_body["created"] is True
        proposal = grant_body["proposal"]
        assert proposal["action_type"] == "agent.update_tool_grant"
        assert proposal["lifecycle_state"] == "waiting_approval"
        assert proposal["requires_approval"] is True
        assert proposal["payload"]["operation"] == "grant"
        assert proposal["payload"]["tool_scope"] == "telegram.fetch_media"

        before_approval = await db_session.scalar(
            select(ToolGrant).where(
                ToolGrant.workspace_id == workspace.id,
                ToolGrant.agent_id == agent.id,
                ToolGrant.scope == "telegram.fetch_media",
            )
        )
        assert before_approval is None

        blocked = await client.post(
            f"/api/action-runtime/proposals/{proposal['proposal_id']}/execute",
            headers=auth_headers,
            json={
                "actor_ref": "owner:test",
                "correlation_id": "corr:test:tool-grant:blocked",
            },
        )
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "blocked"
        assert blocked.json()["reason_code"] == "approval_required_before_execution"

        approved = await client.post(
            f"/api/action-runtime/proposals/{proposal['proposal_id']}/approve",
            headers=auth_headers,
            json={
                "actor_ref": "owner:test",
                "correlation_id": "corr:test:tool-grant:approve",
            },
        )
        assert approved.status_code == 200

        executed = await client.post(
            f"/api/action-runtime/proposals/{proposal['proposal_id']}/execute",
            headers=auth_headers,
            json={
                "actor_ref": "owner:test",
                "correlation_id": "corr:test:tool-grant:execute",
            },
        )
        assert executed.status_code == 200
        assert executed.json()["status"] == "executed"
        assert executed.json()["reason_code"] == "agent_tool_grant_granted"

        after_grant = await db_session.scalar(
            select(ToolGrant).where(
                ToolGrant.workspace_id == workspace.id,
                ToolGrant.agent_id == agent.id,
                ToolGrant.scope == "telegram.fetch_media",
            )
        )
        assert after_grant is not None
        assert after_grant.active is True
        await db_session.refresh(agent)
        assert "telegram.fetch_media" in agent.tools_config["tool_scopes"]

        revoke_response = await client.post(
            f"/api/intelligence/agents/{agent.id}/tool-grants/propose",
            headers=auth_headers,
            json={
                "action": "revoke",
                "scope": "telegram.fetch_media",
                "reason": "Owner no longer wants this agent opening media.",
            },
        )
        assert revoke_response.status_code == 201, revoke_response.text
        revoke_proposal = revoke_response.json()["proposal"]
        assert revoke_proposal["payload"]["operation"] == "revoke"

        await client.post(
            f"/api/action-runtime/proposals/{revoke_proposal['proposal_id']}/approve",
            headers=auth_headers,
            json={
                "actor_ref": "owner:test",
                "correlation_id": "corr:test:tool-grant:revoke:approve",
            },
        )
        revoked = await client.post(
            f"/api/action-runtime/proposals/{revoke_proposal['proposal_id']}/execute",
            headers=auth_headers,
            json={
                "actor_ref": "owner:test",
                "correlation_id": "corr:test:tool-grant:revoke:execute",
            },
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "executed"
        assert revoked.json()["reason_code"] == "agent_tool_grant_revoked"

        await db_session.refresh(after_grant)
        await db_session.refresh(agent)
        assert after_grant.active is False
        assert "telegram.fetch_media" not in agent.tools_config["tool_scopes"]

    async def test_tool_grant_proposal_rejects_unsupported_scope(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent = Agent(workspace_id=workspace.id, name="Unsafe", agent_type="custom")
        db_session.add(agent)
        await db_session.flush()

        response = await client.post(
            f"/api/intelligence/agents/{agent.id}/tool-grants/propose",
            headers=auth_headers,
            json={"action": "grant", "scope": "telegram.delete_everything"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "unsupported_tool_scope"


class TestAgentTriggers:
    async def test_list_returns_empty_for_fresh_agent(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent = Agent(workspace_id=workspace.id, name="Seller")
        db_session.add(agent)
        await db_session.flush()

        response = await client.get(
            f"/api/intelligence/agents/{agent.id}/triggers", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_create_then_list_trigger(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent = Agent(workspace_id=workspace.id, name="Catalog Update")
        db_session.add(agent)
        await db_session.flush()

        create_response = await client.post(
            f"/api/intelligence/agents/{agent.id}/triggers",
            headers=auth_headers,
            json={
                "owner_agent_id": agent.id,
                "event_source": "channel_message_received",
                "action_proposal_type": "catalog.update_product",
                "matching_scope": {"channel": "@mybiz"},
                "permission_mode": "ask_always",
            },
        )
        assert create_response.status_code == 200
        trigger = create_response.json()["trigger"]
        assert trigger["active"] is True

        list_response = await client.get(
            f"/api/intelligence/agents/{agent.id}/triggers", headers=auth_headers
        )
        assert len(list_response.json()["items"]) == 1

    async def test_trigger_changes_are_action_proposals_before_runtime_mutation(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent = Agent(workspace_id=workspace.id, name="Reply Agent", agent_type="custom")
        db_session.add(agent)
        await db_session.flush()

        create_response = await client.post(
            f"/api/intelligence/agents/{agent.id}/triggers/propose",
            headers=auth_headers,
            json={
                "operation": "create",
                "event_source": "channel_message_received",
                "action_proposal_type": "conversation.propose_reply",
                "matching_scope": {"channel": "@sales"},
                "permission_mode": "ask_always",
                "notes": "Yangi xabarlarda javob taklif qiladi.",
            },
        )
        assert create_response.status_code == 201, create_response.text
        proposal = create_response.json()["proposal"]
        assert proposal["action_type"] == "agent.update_trigger"
        assert proposal["lifecycle_state"] == "waiting_approval"
        assert proposal["payload"]["operation"] == "create"

        before_approval = await db_session.scalar(
            select(Trigger).where(
                Trigger.workspace_id == workspace.id,
                Trigger.owner_agent_id == agent.id,
                Trigger.event_source == "channel_message_received",
            )
        )
        assert before_approval is None

        blocked = await client.post(
            f"/api/action-runtime/proposals/{proposal['proposal_id']}/execute",
            headers=auth_headers,
            json={
                "actor_ref": "owner:test",
                "correlation_id": "corr:test:trigger:blocked",
            },
        )
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "blocked"
        assert blocked.json()["reason_code"] == "approval_required_before_execution"

        approved = await client.post(
            f"/api/action-runtime/proposals/{proposal['proposal_id']}/approve",
            headers=auth_headers,
            json={
                "actor_ref": "owner:test",
                "correlation_id": "corr:test:trigger:approve",
            },
        )
        assert approved.status_code == 200
        executed = await client.post(
            f"/api/action-runtime/proposals/{proposal['proposal_id']}/execute",
            headers=auth_headers,
            json={
                "actor_ref": "owner:test",
                "correlation_id": "corr:test:trigger:execute",
            },
        )
        assert executed.status_code == 200
        assert executed.json()["status"] == "executed"
        assert executed.json()["reason_code"] == "agent_trigger_upserted"

        trigger = await db_session.scalar(
            select(Trigger).where(
                Trigger.workspace_id == workspace.id,
                Trigger.owner_agent_id == agent.id,
                Trigger.event_source == "channel_message_received",
            )
        )
        assert trigger is not None
        assert trigger.active is True

        deactivate_response = await client.post(
            f"/api/intelligence/agents/{agent.id}/triggers/propose",
            headers=auth_headers,
            json={
                "operation": "deactivate",
                "trigger_id": trigger.id,
                "notes": "Owner disabled this automation.",
            },
        )
        assert deactivate_response.status_code == 201, deactivate_response.text
        deactivate_proposal = deactivate_response.json()["proposal"]
        assert deactivate_proposal["payload"]["operation"] == "deactivate"

        await client.post(
            f"/api/action-runtime/proposals/{deactivate_proposal['proposal_id']}/approve",
            headers=auth_headers,
            json={
                "actor_ref": "owner:test",
                "correlation_id": "corr:test:trigger:deactivate:approve",
            },
        )
        deactivated = await client.post(
            f"/api/action-runtime/proposals/{deactivate_proposal['proposal_id']}/execute",
            headers=auth_headers,
            json={
                "actor_ref": "owner:test",
                "correlation_id": "corr:test:trigger:deactivate:execute",
            },
        )
        assert deactivated.status_code == 200
        assert deactivated.json()["status"] == "executed"
        assert deactivated.json()["reason_code"] == "agent_trigger_deactivated"

        await db_session.refresh(trigger)
        assert trigger.active is False

    async def test_create_rejects_agent_id_mismatch(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent = Agent(workspace_id=workspace.id, name="Seller")
        db_session.add(agent)
        await db_session.flush()

        response = await client.post(
            f"/api/intelligence/agents/{agent.id}/triggers",
            headers=auth_headers,
            json={
                "owner_agent_id": agent.id + 999,
                "event_source": "schedule",
                "action_proposal_type": "task.daily_review",
            },
        )
        assert response.status_code == 400

    async def test_delete_trigger(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent = Agent(workspace_id=workspace.id, name="Seller")
        db_session.add(agent)
        await db_session.flush()

        create_response = await client.post(
            f"/api/intelligence/agents/{agent.id}/triggers",
            headers=auth_headers,
            json={
                "owner_agent_id": agent.id,
                "event_source": "schedule",
                "action_proposal_type": "task.daily_review",
            },
        )
        trigger_id = create_response.json()["trigger"]["id"]

        delete_response = await client.delete(
            f"/api/intelligence/triggers/{trigger_id}", headers=auth_headers
        )
        assert delete_response.status_code == 204

        list_response = await client.get(
            f"/api/intelligence/agents/{agent.id}/triggers", headers=auth_headers
        )
        assert list_response.json()["items"][0]["active"] is False

    async def test_workspace_isolation_for_triggers(
        self,
        client: AsyncClient,
        auth_headers_b: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        # Workspace A agent — workspace B tries to list its triggers.
        agent = Agent(workspace_id=workspace.id, name="Seller")
        db_session.add(agent)
        await db_session.flush()

        response = await client.get(
            f"/api/intelligence/agents/{agent.id}/triggers", headers=auth_headers_b
        )
        assert response.status_code == 404


class TestAgentSectionEditor:
    async def test_upsert_section_for_agent(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent = Agent(workspace_id=workspace.id, name="Seller")
        db_session.add(agent)
        await db_session.flush()

        response = await client.post(
            f"/api/intelligence/agents/{agent.id}/sections",
            headers=auth_headers,
            json={
                "document_kind": "agent",
                "subject_type": "agent",
                "subject_id": agent.id,
                "section_key": "role",
                "title": "Rol",
                "body": "Yangi rol matni.",
                "order_index": 0,
                "generated_by": "owner",
            },
        )
        assert response.status_code == 200, response.text
        section = response.json()["section"]
        assert section["body"] == "Yangi rol matni."
        assert section["generated_by"] == "owner"

    async def test_rejects_subject_id_mismatch(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace: Workspace,
    ) -> None:
        agent = Agent(workspace_id=workspace.id, name="Seller")
        db_session.add(agent)
        await db_session.flush()

        response = await client.post(
            f"/api/intelligence/agents/{agent.id}/sections",
            headers=auth_headers,
            json={
                "document_kind": "agent",
                "subject_type": "agent",
                "subject_id": agent.id + 9999,
                "section_key": "role",
                "title": "Rol",
                "body": "x",
                "order_index": 0,
            },
        )
        assert response.status_code == 400

    async def test_cannot_edit_other_workspace_agent(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace_b: Workspace,
    ) -> None:
        foreign_agent = Agent(workspace_id=workspace_b.id, name="Foreign")
        db_session.add(foreign_agent)
        await db_session.flush()

        response = await client.post(
            f"/api/intelligence/agents/{foreign_agent.id}/sections",
            headers=auth_headers,
            json={
                "document_kind": "agent",
                "subject_type": "agent",
                "subject_id": foreign_agent.id,
                "section_key": "role",
                "title": "Rol",
                "body": "x",
                "order_index": 0,
            },
        )
        assert response.status_code == 404


class TestCustomAgentDraft:
    async def test_draft_returns_sections_and_defaults(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        fake = {
            "role": "Mahsulot savollariga javob beradi.",
            "when_to_act": "Yangi xabar kelganda javob beradi.",
            "never_guess": "Narxni taxmin qilmaydi.",
        }
        with patch(
            "app.modules.workspace_os.custom_agent_draft.generate_structured_json",
            AsyncMock(return_value=fake),
        ):
            r = await client.post(
                "/api/intelligence/agents/custom/draft",
                headers=auth_headers,
                json={
                    "agent_kind": "seller",
                    "name": "Sotuvchi agent",
                    "does_what": "Mahsulot va narx savollariga javob beradi",
                    "when_replies": "Yangi xabar kelganda",
                    "never_does": "Narxni taxmin qilmaydi",
                },
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["schema_version"] == "custom_agent_draft.v1"
        assert next(s["section_key"] for s in body["sections"]) == "role"
        assert "telegram.send_message" in body["tool_scopes"]
        assert body["trust_mode"] == "autopilot"

    async def test_draft_persists_no_agent(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session, workspace: Workspace
    ) -> None:
        with patch(
            "app.modules.workspace_os.custom_agent_draft.generate_structured_json",
            AsyncMock(return_value={"role": "x", "when_to_act": "y", "never_guess": "z"}),
        ):
            await client.post(
                "/api/intelligence/agents/custom/draft",
                headers=auth_headers,
                json={"agent_kind": "custom", "name": "Test", "does_what": "Nimadir qiladi"},
            )
        count = await db_session.scalar(
            select(func.count()).select_from(Agent).where(Agent.workspace_id == workspace.id)
        )
        assert count == 0

    async def test_draft_requires_auth(self, client: AsyncClient) -> None:
        r = await client.post(
            "/api/intelligence/agents/custom/draft",
            json={"agent_kind": "custom", "name": "Test", "does_what": "Nimadir qiladi"},
        )
        assert r.status_code == 401

    async def test_draft_validates_short_name(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        r = await client.post(
            "/api/intelligence/agents/custom/draft",
            headers=auth_headers,
            json={"agent_kind": "custom", "name": "x", "does_what": "Nimadir qiladi"},
        )
        assert r.status_code == 422

    async def test_draft_returns_503_when_llm_chain_exhausted(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        with patch(
            "app.modules.workspace_os.custom_agent_draft.generate_structured_json",
            AsyncMock(side_effect=RuntimeError("All models in fallback chain failed")),
        ):
            r = await client.post(
                "/api/intelligence/agents/custom/draft",
                headers=auth_headers,
                json={"agent_kind": "custom", "name": "Test agent", "does_what": "Nimadir qiladi"},
            )
        assert r.status_code == 503
        assert r.json()["detail"] == "draft_unavailable"

    async def test_draft_returns_429_when_budget_exceeded(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        from app.modules.agent_runtime_v2.budget import BudgetExceededError

        with patch(
            "app.modules.workspace_os.custom_agent_draft.generate_structured_json",
            AsyncMock(side_effect=BudgetExceededError("daily cap reached")),
        ):
            r = await client.post(
                "/api/intelligence/agents/custom/draft",
                headers=auth_headers,
                json={"agent_kind": "custom", "name": "Test agent", "does_what": "Nimadir qiladi"},
            )
        assert r.status_code == 429
        assert r.json()["detail"] == "budget_exceeded"
