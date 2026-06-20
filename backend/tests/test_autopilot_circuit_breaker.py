from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.hermes_runtime_policy import HermesAutopilotCircuitBreaker
from app.models.workspace import Workspace
from app.modules.agent_runtime_v2.reply_runtime import SendAction
from app.modules.hermes_runtime.circuit_breaker import AutopilotCircuitBreakerService


async def test_global_circuit_breaker_forces_propose(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    db_session.add(
        HermesAutopilotCircuitBreaker(
            scope_type="global",
            scope_id=None,
            active=True,
            reason="maintenance",
        )
    )
    await db_session.flush()

    decision = await AutopilotCircuitBreakerService(db_session).check(
        workspace_id=workspace.id,
        agent_id=agent.id,
    )

    assert decision.allowed is False
    assert decision.forced_action == SendAction.PROPOSE.value
    assert decision.reason_code == "autopilot_circuit_breaker:global:maintenance"


async def test_workspace_circuit_breaker_is_scoped(
    db_session: AsyncSession,
    workspace: Workspace,
    workspace_b: Workspace,
    agent: Agent,
) -> None:
    db_session.add(
        HermesAutopilotCircuitBreaker(
            scope_type="workspace",
            scope_id=workspace.id,
            active=True,
            reason="owner_pause",
        )
    )
    await db_session.flush()
    service = AutopilotCircuitBreakerService(db_session)

    blocked = await service.check(workspace_id=workspace.id, agent_id=agent.id)
    allowed = await service.check(workspace_id=workspace_b.id, agent_id=None)

    assert blocked.allowed is False
    assert blocked.reason_code == "autopilot_circuit_breaker:workspace:owner_pause"
    assert allowed.allowed is True


async def test_agent_circuit_breaker_takes_precedence(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    db_session.add_all(
        [
            HermesAutopilotCircuitBreaker(
                scope_type="workspace",
                scope_id=workspace.id,
                active=True,
                reason="workspace_pause",
            ),
            HermesAutopilotCircuitBreaker(
                scope_type="agent",
                scope_id=agent.id,
                active=True,
                reason="agent_pause",
            ),
        ]
    )
    await db_session.flush()

    decision = await AutopilotCircuitBreakerService(db_session).check(
        workspace_id=workspace.id,
        agent_id=agent.id,
    )

    assert decision.allowed is False
    assert decision.reason_code == "autopilot_circuit_breaker:agent:agent_pause"
