"""Phase 9 brutal — 1000-tenant isolation stress test.

Seeds 1000 workspaces, each with 1 agent + 1 skill + 1 trigger + 1 tool
grant, then proves three things:

 1. Reading workspace N's primitives returns N's data only — no leakage.
 2. The trigger matcher fans events out only inside the originating
    workspace; cross-workspace fan-out is structurally impossible.
 3. List queries stay bounded (each list scoped by workspace_id index).

This test runs in ~30s on a laptop Postgres and bounds the worst-case
runtime so we catch a "list returned everyone's rows" regression early.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import AgentSkillInput
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService
from app.modules.triggers.contracts import TriggerInput
from app.modules.triggers.matcher import TriggerEvent, TriggerMatcher
from app.modules.triggers.service import TriggerService


pytestmark = pytest.mark.asyncio


TENANT_COUNT = 1000


@dataclass
class SeededTenant:
    workspace_id: int
    agent_id: int


async def _seed_tenants(
    db_session: AsyncSession, count: int
) -> list[SeededTenant]:
    """Create `count` workspaces in a single flush per workspace.

    The seed is intentionally narrow — one agent per workspace, one skill,
    one trigger, one grant — to focus on the isolation properties rather
    than entity richness.
    """

    workspaces = [
        Workspace(
            name=f"tenant-{idx:04d}",
            phone_number=f"+99800{idx:07d}",
            onboarding_completed=True,
        )
        for idx in range(count)
    ]
    db_session.add_all(workspaces)
    await db_session.flush()

    agents = [
        Agent(workspace_id=ws.id, name=f"seller-{idx:04d}", agent_type="seller")
        for idx, ws in enumerate(workspaces)
    ]
    db_session.add_all(agents)
    await db_session.flush()

    doc_service = AgentDocumentService(db_session)
    grant_service = ToolGrantService(db_session)
    trigger_service = TriggerService(db_session)

    seeded: list[SeededTenant] = []
    for idx, (ws, agent) in enumerate(zip(workspaces, agents)):
        await doc_service.upsert_skill(
            workspace_id=ws.id,
            payload=AgentSkillInput(
                slug="catalog-lookup", name="Catalog lookup", agent_id=agent.id
            ),
        )
        await grant_service.grant(
            workspace_id=ws.id,
            payload=ToolGrantInput(
                agent_id=agent.id, scope="telegram.send_message"
            ),
        )
        await trigger_service.create(
            workspace_id=ws.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="schedule",
                action_proposal_type="task.daily_review",
                matching_scope={"day": "monday"},
            ),
        )
        seeded.append(SeededTenant(workspace_id=ws.id, agent_id=agent.id))
    return seeded


class TestThousandTenants:
    async def test_isolation_for_skills_grants_triggers(
        self, db_session: AsyncSession
    ) -> None:
        """1000 workspaces each see only their own primitives."""

        seeded = await _seed_tenants(db_session, TENANT_COUNT)
        # Sanity: we actually seeded 1000.
        assert len(seeded) == TENANT_COUNT

        doc_service = AgentDocumentService(db_session)
        grant_service = ToolGrantService(db_session)
        trigger_service = TriggerService(db_session)

        # Sample 5 tenants from across the range; each must see exactly 1 of
        # its own resources and zero of anyone else's.
        sample_indices = [0, 250, 500, 750, 999]
        for idx in sample_indices:
            tenant = seeded[idx]
            skills = await doc_service.list_skills(
                workspace_id=tenant.workspace_id
            )
            grants = await grant_service.list_for_workspace(
                workspace_id=tenant.workspace_id
            )
            triggers = await trigger_service.list_for_workspace(
                workspace_id=tenant.workspace_id
            )
            assert len(skills) == 1
            assert skills[0].agent_id == tenant.agent_id
            assert len(grants) == 1
            assert grants[0].agent_id == tenant.agent_id
            assert len(triggers) == 1
            assert triggers[0].owner_agent_id == tenant.agent_id

    async def test_trigger_matcher_does_not_cross_workspaces(
        self, db_session: AsyncSession
    ) -> None:
        """Firing a schedule event on workspace N creates one proposal in N
        and zero proposals in any of the other 999 workspaces.
        """

        seeded = await _seed_tenants(db_session, TENANT_COUNT)
        matcher = TriggerMatcher(db_session)

        target = seeded[500]
        await matcher.fan_out(
            TriggerEvent(
                workspace_id=target.workspace_id,
                event_source="schedule",
                payload={"day": "monday"},
            )
        )

        in_target = (
            await db_session.scalars(
                select(CommercialActionProposalRecord).where(
                    CommercialActionProposalRecord.workspace_id == target.workspace_id
                )
            )
        ).all()
        assert len(in_target) == 1

        elsewhere = (
            await db_session.scalars(
                select(CommercialActionProposalRecord).where(
                    CommercialActionProposalRecord.workspace_id != target.workspace_id
                )
            )
        ).all()
        assert elsewhere == []

    async def test_list_for_workspace_stays_bounded(
        self, db_session: AsyncSession
    ) -> None:
        """A workspace-scoped list query should return in <1s for 1000
        tenants. Catches future regressions where someone removes the
        workspace_id filter from a join.
        """

        seeded = await _seed_tenants(db_session, TENANT_COUNT)
        trigger_service = TriggerService(db_session)

        target = seeded[42]
        start = time.perf_counter()
        result = await trigger_service.list_for_workspace(
            workspace_id=target.workspace_id
        )
        elapsed = time.perf_counter() - start
        assert len(result) == 1
        # 1s is generous — locally this runs in ~20ms. The bound exists to
        # catch sequential-scan regressions.
        assert elapsed < 1.0, (
            f"list_for_workspace took {elapsed:.3f}s for one tenant out of "
            f"{TENANT_COUNT}; the workspace_id index probably regressed"
        )
