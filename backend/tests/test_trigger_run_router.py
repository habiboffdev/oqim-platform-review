from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.hermes_run import HermesRun, HermesRunEvent
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.workspace import Workspace
from app.modules.triggers.contracts import (
    Phase3TriggerDefinition,
    TriggerKind,
    TriggerRunMode,
)
from app.modules.triggers.matcher import TriggerEvent, TriggerMatcher
from app.modules.triggers.run_router import TriggerRunRouter
from app.modules.triggers.service import TriggerService

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    (
        "kind",
        "run_mode",
        "event_filters",
        "event_payload",
        "lane",
        "agent_type",
        "expected_hermes_mode",
        "expected_lane",
    ),
    [
        (
            TriggerKind.MESSAGE,
            TriggerRunMode.REPLY,
            {"chat_id": "123"},
            {"chat_id": "123", "event_id": "msg:reply:1"},
            "fast_interactive",
            "seller",
            "reply",
            "fast_interactive",
        ),
        (
            TriggerKind.OWNER_COMMAND,
            TriggerRunMode.OWNER_ONLY,
            {"command": "summarize_today"},
            {"command": "summarize_today", "event_id": "owner:personal:1"},
            "background",
            "personal",
            "personal",
            "background",
        ),
        (
            TriggerKind.SCHEDULE,
            TriggerRunMode.BROADCAST,
            {"schedule_key": "daily_broadcast"},
            {"schedule_key": "daily_broadcast", "event_id": "schedule:broadcast:1"},
            "background",
            "broadcast",
            "broadcast",
            "background",
        ),
    ],
)
async def test_phase3_trigger_profiles_route_to_matching_hermes_modes(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    kind: TriggerKind,
    run_mode: TriggerRunMode,
    event_filters: dict[str, str],
    event_payload: dict[str, str],
    lane: str,
    agent_type: str,
    expected_hermes_mode: str,
    expected_lane: str,
) -> None:
    agent.agent_type = agent_type
    trigger = await TriggerService(db_session).create(
        workspace_id=workspace.id,
        payload=Phase3TriggerDefinition(
            owner_agent_id=agent.id,
            kind=kind,
            run_mode=run_mode,
            event_filters=event_filters,
            lane=lane,
            permission_mode="auto_approve",
        ).to_trigger_input(),
    )

    matched = await TriggerMatcher(db_session).fan_out(
        TriggerEvent(
            workspace_id=workspace.id,
            event_source=trigger.event_source,
            payload=event_payload,
            correlation_id=f"corr:{event_payload['event_id']}",
        )
    )

    assert [item.action_proposal_type for item in matched] == [f"hermes.{run_mode.value}"]

    routed = await TriggerRunRouter(db_session).route_pending(limit=10)

    assert len(routed) == 1
    assert routed[0].agent_kind == agent_type
    assert routed[0].run_mode == expected_hermes_mode
    assert routed[0].lane == expected_lane
    assert routed[0].details["trigger"]["phase3"]["run_mode"] == run_mode.value
    assert routed[0].details["event_payload"]["event_id"] == event_payload["event_id"]


async def test_approved_phase3_trigger_routes_to_generic_hermes_run(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    agent.agent_type = "scanner"
    trigger_input = Phase3TriggerDefinition(
        owner_agent_id=agent.id,
        kind=TriggerKind.SCAN,
        run_mode=TriggerRunMode.SCANNER,
        event_filters={"command": "scan_debts"},
        idempotency_scope={"owner_command": "scan_debts"},
        lane="background",
        priority=25,
        permission_mode="auto_approve",
    ).to_trigger_input()
    trigger = await TriggerService(db_session).create(
        workspace_id=workspace.id,
        payload=trigger_input,
    )

    matched = await TriggerMatcher(db_session).fan_out(
        TriggerEvent(
            workspace_id=workspace.id,
            event_source="owner_bi_command",
            payload={"command": "scan_debts", "event_id": "owner-command:scan-debts"},
            correlation_id="corr-scan-debts",
        )
    )

    assert len(matched) == 1
    assert matched[0].action_proposal_type == "hermes.scanner"

    routed = await TriggerRunRouter(db_session).route_pending(limit=10)

    assert len(routed) == 1
    run = await db_session.scalar(
        select(HermesRun).where(HermesRun.run_id == routed[0].run_id)
    )
    assert run is not None
    assert run.workspace_id == workspace.id
    assert run.agent_id == agent.id
    assert run.agent_kind == "scanner"
    assert run.lane == "background"
    assert run.run_mode == "scan"
    assert run.trigger_type == "generic_trigger"
    assert run.trigger_id == matched[0].proposal_id
    assert run.event_id == "owner-command:scan-debts"
    assert run.correlation_id == "corr-scan-debts"
    assert f"trigger:{trigger.id}" in run.source_refs
    assert f"action_proposal:{matched[0].proposal_id}" in run.source_refs
    assert run.details["trigger"]["phase3"]["run_mode"] == "scanner"
    assert run.details["trigger"]["event_source"] == "owner_bi_command"
    assert run.details["event_payload"]["command"] == "scan_debts"

    proposal = await db_session.scalar(
        select(CommercialActionProposalRecord).where(
            CommercialActionProposalRecord.proposal_id == matched[0].proposal_id
        )
    )
    assert proposal is not None
    assert proposal.lifecycle_state == "executed"
    assert proposal.payload["hermes_run_id"] == run.run_id
    assert proposal.payload["hermes_run_deduped"] is False

    events = (
        await db_session.scalars(
            select(HermesRunEvent).where(HermesRunEvent.run_id == run.run_id)
        )
    ).all()
    assert [event.kind for event in events] == ["created"]

    assert await TriggerRunRouter(db_session).route_pending(limit=10) == []
