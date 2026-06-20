from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.workspace import Workspace
from app.modules.agent_runtime_events.contracts import (
    AgentRunEventInput,
    AgentRunInput,
)
from app.modules.agent_runtime_events.service import (
    AgentRuntimeEventService,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository


def _service(db_session: AsyncSession) -> AgentRuntimeEventService:
    return AgentRuntimeEventService(CommercialSpineRepository(db_session))


async def test_agent_run_start_is_idempotent_and_projection_backed(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    payload = AgentRunInput(
        run_id="run:progress:1",
        workspace_id=workspace.id,
        agent_id=agent.id,
        agent_kind="seller",
        trigger_ref="message:100",
        conversation_id=10,
        customer_id=20,
        state="running",
        permission_mode="auto_approve",
        cache_key="agent-cache:abc",
        correlation_id="corr:run:1",
        idempotency_key="idem:run:1",
    )
    service = _service(db_session)

    first = await service.start_run(payload)
    second = await service.start_run(payload)
    timeline = await service.timeline(workspace_id=workspace.id, run_id=payload.run_id)

    assert first.run_id == payload.run_id
    assert second.run_id == first.run_id
    assert second.state == "running"
    assert timeline.run is not None
    assert timeline.run.agent_id == agent.id
    assert timeline.events == []


async def test_agent_run_events_keep_visibility_and_tool_lifecycle_order(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    service = _service(db_session)
    run = await service.start_run(
        AgentRunInput(
            run_id="run:progress:2",
            workspace_id=workspace.id,
            agent_id=agent.id,
            agent_kind="seller",
            trigger_ref="message:200",
            conversation_id=11,
            customer_id=21,
            state="running",
            permission_mode="ask_always",
            correlation_id="corr:run:2",
            idempotency_key="idem:run:2",
        )
    )

    owner_event = await service.record_event(
        AgentRunEventInput(
            event_id="run-progress-2-owner-1",
            run_id=run.run_id,
            workspace_id=workspace.id,
            event_type="owner_progress.created",
            visibility="owner",
            owner_label="Katalogdan model qidirilyapti",
            owner_detail="Agent katalogdan mos mahsulotni tekshirmoqda.",
            correlation_id="corr:run:2",
            idempotency_key="idem:run:2:owner:1",
        )
    )
    tool_event = await service.record_event(
        AgentRunEventInput(
            event_id="run-progress-2-tool-1",
            run_id=run.run_id,
            workspace_id=workspace.id,
            event_type="tool.call.started",
            visibility="internal",
            tool_name="catalog.search",
            tool_state="called",
            correlation_id="corr:run:2",
            idempotency_key="idem:run:2:tool:1",
        )
    )
    action_event = await service.record_event(
        AgentRunEventInput(
            event_id="run-progress-2-action-1",
            run_id=run.run_id,
            workspace_id=workspace.id,
            event_type="customer_status.proposed",
            visibility="customer_action",
            owner_label="Mijozga holat xabari taklif qilindi",
            action_proposal_id="proposal-status-message",
            correlation_id="corr:run:2",
            idempotency_key="idem:run:2:action:1",
        )
    )
    duplicate = await service.record_event(
        AgentRunEventInput(
            event_id="run-progress-2-tool-1",
            run_id=run.run_id,
            workspace_id=workspace.id,
            event_type="tool.call.started",
            visibility="internal",
            tool_name="catalog.search",
            tool_state="called",
            correlation_id="corr:run:2",
            idempotency_key="idem:run:2:tool:1",
        )
    )
    timeline = await service.timeline(workspace_id=workspace.id, run_id=run.run_id)

    assert owner_event.sequence == 1
    assert tool_event.sequence == 2
    assert action_event.sequence == 3
    assert duplicate.sequence == tool_event.sequence
    assert [event.event_type for event in timeline.events] == [
        "owner_progress.created",
        "tool.call.started",
        "customer_status.proposed",
    ]
    assert timeline.events[0].visibility == "owner"
    assert timeline.events[1].tool_name == "catalog.search"
    assert timeline.events[2].action_proposal_id == "proposal-status-message"


async def test_agent_run_transition_records_completion_state(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    service = _service(db_session)
    run = await service.start_run(
        AgentRunInput(
            run_id="run:progress:3",
            workspace_id=workspace.id,
            agent_id=agent.id,
            agent_kind="seller",
            trigger_ref="message:300",
            state="running",
            correlation_id="corr:run:3",
            idempotency_key="idem:run:3",
        )
    )

    completed = await service.transition_run(
        workspace_id=workspace.id,
        run_id=run.run_id,
        state="completed",
        correlation_id="corr:run:3:done",
    )
    timeline = await service.timeline(workspace_id=workspace.id, run_id=run.run_id)

    assert completed.state == "completed"
    assert completed.completed_at is not None
    assert timeline.run is not None
    assert timeline.run.state == "completed"


async def test_recent_agent_run_feed_returns_latest_runs_with_owner_events(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    service = _service(db_session)
    older = await service.start_run(
        AgentRunInput(
            run_id="run:progress:older",
            workspace_id=workspace.id,
            agent_id=agent.id,
            agent_kind="seller",
            trigger_ref="message:older",
            state="completed",
            started_at=datetime(2026, 5, 18, 9, 0, tzinfo=UTC),
            correlation_id="corr:older",
            idempotency_key="idem:older",
        )
    )
    newer = await service.start_run(
        AgentRunInput(
            run_id="run:progress:newer",
            workspace_id=workspace.id,
            agent_id=agent.id,
            agent_kind="seller",
            trigger_ref="message:newer",
            state="running",
            started_at=datetime(2026, 5, 18, 9, 5, tzinfo=UTC),
            correlation_id="corr:newer",
            idempotency_key="idem:newer",
        )
    )
    await service.record_event(
        AgentRunEventInput(
            event_id="run-progress-newer-owner-1",
            run_id=newer.run_id,
            workspace_id=workspace.id,
            event_type="owner_progress.created",
            visibility="owner",
            owner_label="Katalog tekshirilmoqda",
            owner_detail="2 ta mos mahsulot topildi.",
            correlation_id="corr:newer",
            idempotency_key="idem:newer:owner:1",
        )
    )
    await service.record_event(
        AgentRunEventInput(
            event_id="run-progress-older-owner-1",
            run_id=older.run_id,
            workspace_id=workspace.id,
            event_type="owner_progress.created",
            visibility="owner",
            owner_label="Javob tayyorlandi",
            owner_detail="Mijozga javob taklif qilindi.",
            correlation_id="corr:older",
            idempotency_key="idem:older:owner:1",
        )
    )

    feed = await service.recent_timelines(workspace_id=workspace.id, limit=1)

    assert feed.schema_version == "agent_run_feed.v1"
    assert len(feed.timelines) == 1
    assert feed.timelines[0].run is not None
    assert feed.timelines[0].run.run_id == newer.run_id
    assert feed.timelines[0].events[0].owner_label == "Katalog tekshirilmoqda"
