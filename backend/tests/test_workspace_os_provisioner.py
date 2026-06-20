from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_document import AgentDocumentSection
from app.models.agent_skill import AgentSkill
from app.models.tool_grant import ToolGrant
from app.models.trigger import Trigger
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import AgentDocumentSectionInput
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.telegram_tools.contracts import TELEGRAM_TOOL_SCOPES
from app.modules.workspace_os.provisioner import WorkspaceOSProvisioner

pytestmark = pytest.mark.asyncio


async def _count(session: AsyncSession, model, workspace_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
        )
        or 0
    )


async def test_provisioner_creates_default_workspace_os(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    result = await WorkspaceOSProvisioner(db_session).provision(
        workspace=workspace,
        profile={
            "business_profile": {
                "offer_summary": "SAT kurslari va mentorlik",
                "preferred_language": "uzbek_latin",
                "tone": "short_warm",
            },
            "preferences": {"reply_mode": "draft"},
            "sources": {"notes": "Telegram kanal va PDF bor"},
            "owner_rules": {"notes": "To'lovdan keyin chek so'ra."},
        },
        preferences={
            "default_agents": ["seller", "support", "catalog_update", "follow_up", "bi"],
            "permission_mode": "ask_always",
        },
    )

    assert result.selected_agent_keys == (
        "seller",
        "support",
        "catalog_update",
        "follow_up",
        "bi",
    )
    assert result.agent_count == 5
    assert result.skill_count >= 6
    assert result.document_section_count >= 30
    assert result.tool_grant_count == 13
    assert result.trigger_count >= 7

    agents = (
        await db_session.scalars(
            select(Agent).where(Agent.workspace_id == workspace.id).order_by(Agent.agent_type)
        )
    ).all()
    assert {agent.agent_type for agent in agents} >= {
        "seller",
        "support",
        "catalog_update",
        "follow_up",
        "bi",
    }
    assert all(agent.persona["schema_version"] == "agent_package.v1" for agent in agents)
    catalog_agent = next(agent for agent in agents if agent.agent_type == "catalog_update")
    assert "source.ingest" in catalog_agent.tools_config["tool_scopes"]

    business_sections = (
        await db_session.scalars(
            select(AgentDocumentSection).where(
                AgentDocumentSection.workspace_id == workspace.id,
                AgentDocumentSection.document_kind == "business",
                AgentDocumentSection.subject_type == "workspace",
            )
        )
    ).all()
    assert {section.section_key for section in business_sections} >= {
        "business_overview",
        "permission_policy",
        "missing_data_behavior",
    }
    assert any("SAT kurslari" in section.body for section in business_sections)

    grants = (
        await db_session.scalars(
            select(ToolGrant).where(
                ToolGrant.workspace_id == workspace.id,
                ToolGrant.scope == "telegram.send_message",
            )
        )
    ).all()
    assert grants
    assert all(grant.active for grant in grants)

    all_grants = (
        await db_session.scalars(
            select(ToolGrant).where(ToolGrant.workspace_id == workspace.id)
        )
    ).all()
    assert len(all_grants) == result.tool_grant_count
    assert all(grant.scope in TELEGRAM_TOOL_SCOPES for grant in all_grants)
    assert {grant.scope for grant in all_grants}.isdisjoint(
        {"brain.search", "source.ingest", "action.create_proposal"}
    )


async def test_provisioner_is_idempotent(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = WorkspaceOSProvisioner(db_session)
    kwargs = {
        "workspace": workspace,
        "profile": {"business_profile": {"offer_summary": "Xizmatlar"}},
        "preferences": {
            "default_agents": ["seller", "support", "catalog_update", "follow_up", "bi"],
            "permission_mode": "ask_always",
        },
    }

    await service.provision(**kwargs)
    first_counts = {
        "agents": await _count(db_session, Agent, workspace.id),
        "skills": await _count(db_session, AgentSkill, workspace.id),
        "sections": await _count(db_session, AgentDocumentSection, workspace.id),
        "grants": await _count(db_session, ToolGrant, workspace.id),
        "triggers": await _count(db_session, Trigger, workspace.id),
    }

    await service.provision(**kwargs)
    second_counts = {
        "agents": await _count(db_session, Agent, workspace.id),
        "skills": await _count(db_session, AgentSkill, workspace.id),
        "sections": await _count(db_session, AgentDocumentSection, workspace.id),
        "grants": await _count(db_session, ToolGrant, workspace.id),
        "triggers": await _count(db_session, Trigger, workspace.id),
    }

    assert second_counts == first_counts


async def test_provisioner_skips_documents_when_documents_false(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    result = await WorkspaceOSProvisioner(db_session).provision(
        workspace=workspace,
        profile={"business_profile": {"offer_summary": "Xizmatlar"}},
        preferences={
            "default_agents": ["seller", "support", "catalog_update", "follow_up", "bi"],
            "permission_mode": "ask_always",
        },
        documents=False,
    )

    # Agents/grants/triggers/skills are still created.
    assert result.agent_count == 5
    assert await _count(db_session, Agent, workspace.id) == 5
    assert await _count(db_session, ToolGrant, workspace.id) > 0
    assert await _count(db_session, Trigger, workspace.id) > 0

    # But ZERO document sections were written.
    assert result.document_section_count == 0
    assert await _count(db_session, AgentDocumentSection, workspace.id) == 0


async def test_provisioner_writes_documents_by_default(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    result = await WorkspaceOSProvisioner(db_session).provision(
        workspace=workspace,
        profile={"business_profile": {"offer_summary": "Xizmatlar"}},
        preferences={
            "default_agents": ["seller", "support", "catalog_update", "follow_up", "bi"],
            "permission_mode": "ask_always",
        },
    )

    assert result.document_section_count > 0
    assert await _count(db_session, AgentDocumentSection, workspace.id) > 0


async def test_provisioner_keeps_bi_even_when_old_client_omits_it(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    result = await WorkspaceOSProvisioner(db_session).provision(
        workspace=workspace,
        profile={},
        preferences={"default_agents": ["seller"], "permission_mode": "ask_always"},
    )

    assert result.selected_agent_keys == ("seller", "bi")
    agents = (
        await db_session.scalars(
            select(Agent).where(Agent.workspace_id == workspace.id).order_by(Agent.agent_type)
        )
    ).all()
    assert {agent.agent_type for agent in agents} == {"seller", "bi"}


async def test_provisioner_preserves_owner_edited_document_sections(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    await AgentDocumentService(db_session).upsert_section(
        workspace_id=workspace.id,
        payload=AgentDocumentSectionInput(
            document_kind="business",
            subject_type="workspace",
            section_key="business_overview",
            title="Business overview",
            body="Owner approved context",
            generated_by="owner",
        ),
    )

    await WorkspaceOSProvisioner(db_session).provision(
        workspace=workspace,
        profile={"business_profile": {"offer_summary": "Generated context"}},
        preferences={},
    )

    section = await db_session.scalar(
        select(AgentDocumentSection).where(
            AgentDocumentSection.workspace_id == workspace.id,
            AgentDocumentSection.document_kind == "business",
            AgentDocumentSection.subject_type == "workspace",
            AgentDocumentSection.section_key == "business_overview",
        )
    )
    assert section is not None
    assert section.body == "Owner approved context"
    assert section.generated_by == "owner"
