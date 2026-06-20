from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.hermes_run import HermesRun
from app.models.workspace import Workspace
from app.modules.hermes_runtime.contracts import (
    HermesRunEventInput,
    HermesRunEventKind,
    HermesRunInput,
    HermesRunPatch,
    HermesRunState,
)
from app.modules.hermes_runtime.service import HermesRunService


async def _running_run(
    service: HermesRunService,
    *,
    run_id: str,
    workspace_id: int,
    agent_id: int,
) -> HermesRun:
    await service.start_or_dedupe(
        HermesRunInput(
            run_id=run_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            trigger_type="conversation_turn",
            trigger_id=f"trigger:{run_id}",
        )
    )
    await service.mark_running(run_id)
    run = await service._db.scalar(select(HermesRun).where(HermesRun.run_id == run_id))
    assert run is not None
    return run


async def test_reclaim_stale_running_runs_marks_only_aged_running_rows_failed(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    """A run left 'running' past the TTL (e.g. a turn aborted before
    finalization, #418) must be reclaimed as failed so the central run record
    can never silently lie. Fresh running rows and finished rows are untouched."""
    service = HermesRunService(db_session)

    stale = await _running_run(
        service, run_id="hermes_run:stale", workspace_id=workspace.id, agent_id=agent.id
    )
    stale.updated_at = datetime.now(UTC) - timedelta(seconds=900)

    fresh = await _running_run(
        service, run_id="hermes_run:fresh", workspace_id=workspace.id, agent_id=agent.id
    )

    done = await _running_run(
        service, run_id="hermes_run:done", workspace_id=workspace.id, agent_id=agent.id
    )
    await service.complete(done.run_id)
    done.updated_at = datetime.now(UTC) - timedelta(seconds=900)
    await db_session.flush()

    reclaimed = await service.reclaim_stale_running_runs(ttl_seconds=300, limit=50)

    assert reclaimed == 1
    await db_session.refresh(stale)
    await db_session.refresh(fresh)
    await db_session.refresh(done)
    assert stale.state == HermesRunState.FAILED
    assert stale.error_code == "stale_running_reclaimed"
    assert stale.completed_at is not None
    assert fresh.state == HermesRunState.RUNNING
    assert done.state == HermesRunState.COMPLETED

    events = await service.events_for_run(stale.run_id)
    assert events[-1].kind == HermesRunEventKind.FAILED


async def test_start_or_dedupe_creates_only_one_run(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    service = HermesRunService(db_session)
    payload = HermesRunInput(
        run_id="hermes_run:test:dedupe",
        workspace_id=workspace.id,
        agent_id=agent.id,
        run_mode="reply",
        lane="fast_interactive",
        trigger_type="telegram_message",
        trigger_id="message:1",
        correlation_id="corr:dedupe",
    )

    first = await service.start_or_dedupe(payload)
    second = await service.start_or_dedupe(payload)
    latest = await service.latest_for_workspace_agent(workspace_id=workspace.id, agent_id=agent.id)

    assert first.run_id == payload.run_id
    assert second.run_id == first.run_id
    assert second.deduped is True
    assert len(latest) == 1


async def test_completion_records_runtime_metrics(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    service = HermesRunService(db_session)
    run = await service.start_or_dedupe(
        HermesRunInput(
            run_id="hermes_run:test:complete",
            workspace_id=workspace.id,
            agent_id=agent.id,
            trigger_type="manual",
            trigger_id="trigger:complete",
        )
    )

    completed = await service.complete(
        run.run_id,
        HermesRunPatch(
            total_latency_ms=1234,
            llm_latency_ms=900,
            llm_calls=1,
            tokens_in=2000,
            tokens_out=140,
            total_tokens=2140,
            confidence=0.88,
            warnings_count=1,
            tool_errors_count=0,
            output_action="send_reply",
            output_ref="reply:1",
        ),
    )

    assert completed.state == HermesRunState.COMPLETED
    assert completed.completed_at is not None
    assert completed.total_latency_ms == 1234
    assert completed.total_tokens == 2140
    assert completed.confidence == 0.88
    assert completed.output_action == "send_reply"


async def test_failure_records_error_code_and_event(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    service = HermesRunService(db_session)
    run = await service.start_or_dedupe(
        HermesRunInput(
            run_id="hermes_run:test:fail",
            workspace_id=workspace.id,
            agent_id=agent.id,
            trigger_type="manual",
            trigger_id="trigger:fail",
        )
    )

    failed = await service.fail(
        run.run_id,
        error_code="llm_timeout",
        error_message="Hermes timed out",
        details={"timeout_seconds": 20},
    )
    events = await service.events_for_run(run.run_id)

    assert failed.state == HermesRunState.FAILED
    assert failed.error_code == "llm_timeout"
    assert failed.details["timeout_seconds"] == 20
    assert events[-1].kind == HermesRunEventKind.FAILED


async def test_events_keep_order_and_dedupe_by_event_id(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    service = HermesRunService(db_session)
    run = await service.start_or_dedupe(
        HermesRunInput(
            run_id="hermes_run:test:events",
            workspace_id=workspace.id,
            agent_id=agent.id,
            trigger_type="manual",
            trigger_id="trigger:events",
        )
    )

    first = await service.record_event(
        HermesRunEventInput(
            event_id="event:context",
            run_id=run.run_id,
            workspace_id=workspace.id,
            kind=HermesRunEventKind.CONTEXT_GATHERED,
        )
    )
    duplicate = await service.record_event(
        HermesRunEventInput(
            event_id="event:context",
            run_id=run.run_id,
            workspace_id=workspace.id,
            kind=HermesRunEventKind.CONTEXT_GATHERED,
        )
    )
    second = await service.record_event(
        HermesRunEventInput(
            event_id="event:policy",
            run_id=run.run_id,
            workspace_id=workspace.id,
            kind=HermesRunEventKind.POLICY_CHECKED,
        )
    )
    events = await service.events_for_run(run.run_id)

    assert first.sequence == 2  # created event is sequence 1
    assert duplicate.sequence == first.sequence
    assert second.sequence == 3
    assert [event.kind for event in events] == [
        HermesRunEventKind.CREATED,
        HermesRunEventKind.CONTEXT_GATHERED,
        HermesRunEventKind.POLICY_CHECKED,
    ]
