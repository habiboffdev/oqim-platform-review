from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.workspace_os.projection import WorkspaceOSProjectionService
from app.modules.workspace_os.provisioner import WorkspaceOSProvisioner

pytestmark = pytest.mark.asyncio


async def test_workspace_os_projection_reports_not_provisioned(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    projection = await WorkspaceOSProjectionService(db_session).build(workspace=workspace)

    assert projection.schema_version == "workspace_os_projection.v1"
    assert projection.readiness.status == "not_provisioned"
    assert projection.readiness.percent == 0
    assert {agent.package_key for agent in projection.agents} == {
        "seller",
        "support",
        "catalog_update",
        "follow_up",
        "bi",
    }
    assert all(not agent.present for agent in projection.agents)
    assert not any(issue.code == "agent_missing" for issue in projection.readiness.issues)


async def test_workspace_os_projection_reports_missing_agents_after_completed_onboarding(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    workspace.onboarding_completed = True
    await db_session.commit()

    projection = await WorkspaceOSProjectionService(db_session).build(workspace=workspace)

    assert projection.readiness.status == "not_provisioned"
    assert any(issue.code == "agent_missing" and issue.severity == "critical" for issue in projection.readiness.issues)


async def test_workspace_os_projection_summarizes_provisioned_os(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    await WorkspaceOSProvisioner(db_session).provision(
        workspace=workspace,
        profile={"business_profile": {"offer_summary": "Kurslar va mentorlik"}},
        preferences={
            "default_agents": [
                "seller",
                "support",
                "catalog_update",
                "follow_up",
                "bi",
            ],
            "permission_mode": "ask_always",
        },
    )

    projection = await WorkspaceOSProjectionService(db_session).build(workspace=workspace)

    assert projection.readiness.status == "ready"
    assert projection.readiness.percent == 100
    assert projection.documents.business_md_ready is True
    assert projection.documents.business_section_count >= 8
    assert projection.documents.sections_preview
    assert projection.documents.sections_preview[0].title
    assert "Kurslar va mentorlik" in " ".join(
        section.body_preview for section in projection.documents.sections_preview
    )
    assert all(agent.present for agent in projection.agents)
    assert all(agent.health == "ready" for agent in projection.agents)
    seller = next(agent for agent in projection.agents if agent.package_key == "seller")
    assert seller.document_preview
    assert seller.skill_names
    assert seller.permission_mode == "ask_always"
    assert seller.capability_count >= seller.active_tool_grant_count
    assert seller.active_tool_grant_count >= 1
    assert seller.active_trigger_count >= 1
    assert "brain.search" not in seller.missing_capability_scopes
    assert "telegram.send_message" not in seller.missing_tool_scopes
    assert all(scope.startswith("telegram.") for scope in seller.missing_tool_scopes)
    assert any(issue.code == "sources_missing" for issue in projection.readiness.issues)


async def test_workspace_os_state_endpoint_and_retry_provision(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    before = await client.get("/api/workspace-os/state", headers=auth_headers)
    assert before.status_code == 200
    assert before.json()["readiness"]["status"] == "not_provisioned"

    provisioned = await client.post("/api/workspace-os/provision", headers=auth_headers)
    assert provisioned.status_code == 200
    data = provisioned.json()
    assert data["schema_version"] == "workspace_os_projection.v1"
    assert data["readiness"]["status"] == "ready"
    assert {agent["package_key"] for agent in data["agents"]} == {
        "seller",
        "support",
        "catalog_update",
        "follow_up",
        "bi",
    }
    assert all(agent["present"] for agent in data["agents"])
    assert data["documents"]["sections_preview"][0]["title"]
    seller = next(agent for agent in data["agents"] if agent["package_key"] == "seller")
    assert seller["document_preview"]
    assert seller["skill_names"]

    after = await client.get("/api/workspace-os/state", headers=auth_headers)
    assert after.status_code == 200
    assert after.json()["readiness"]["percent"] == 100
