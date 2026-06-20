from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.workspace import Workspace
from app.modules.hermes_runtime.contracts import (
    HermesRunEventInput,
    HermesRunEventKind,
    HermesRunInput,
    HermesRunPatch,
)
from app.modules.hermes_runtime.service import HermesRunService
from app.modules.hermes_runtime.trace_formatter import (
    build_hermes_run_trace_payload,
    format_hermes_run_trace_lines,
)


async def test_hermes_run_trace_queries_by_output_conversation_and_event(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    service = HermesRunService(db_session)
    run = await service.start_or_dedupe(
        HermesRunInput(
            run_id="hermes_run:test:trace-query",
            workspace_id=workspace.id,
            agent_id=agent.id,
            lane="fast_interactive",
            run_mode="reply",
            trigger_type="telegram_message",
            trigger_id="message:77",
            event_id="event:77",
            conversation_id=123,
            customer_id=456,
        )
    )
    await service.complete(
        run.run_id,
        HermesRunPatch(
            total_latency_ms=789,
            llm_latency_ms=450,
            llm_calls=1,
            tokens_in=1800,
            tokens_out=120,
            total_tokens=1920,
            confidence=0.91,
            warnings_count=0,
            tool_errors_count=0,
            output_action="send_reply",
            output_ref="agent_action:77",
            details={"policy_decision": "auto_send"},
        ),
    )

    by_output = await service.get_by_output_ref("agent_action:77")
    by_conversation = await service.latest_for_conversation(
        workspace_id=workspace.id,
        conversation_id=123,
    )
    by_event = await service.latest_for_event(workspace_id=workspace.id, event_id="event:77")

    assert by_output is not None
    assert by_output.run_id == run.run_id
    assert [item.run_id for item in by_conversation] == [run.run_id]
    assert [item.run_id for item in by_event] == [run.run_id]


async def test_hermes_run_trace_payload_and_lines_include_operator_fields(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    service = HermesRunService(db_session)
    run = await service.start_or_dedupe(
        HermesRunInput(
            run_id="hermes_run:test:trace-format",
            workspace_id=workspace.id,
            agent_id=agent.id,
            lane="fast_interactive",
            run_mode="reply",
            trigger_type="telegram_message",
            trigger_id="message:88",
            event_id="event:88",
            conversation_id=321,
        )
    )
    await service.record_event(
        HermesRunEventInput(
            event_id="event:trace-format:policy",
            run_id=run.run_id,
            workspace_id=workspace.id,
            kind=HermesRunEventKind.POLICY_CHECKED,
            payload={"decision": "auto_send"},
        )
    )
    await service.record_event(
        HermesRunEventInput(
            event_id="event:trace-format:talk",
            run_id=run.run_id,
            workspace_id=workspace.id,
            kind=HermesRunEventKind.TOOL_CALLED,
            tool_name="talk.bundle",
            tool_state="queued",
            payload={"action_count": 2},
        )
    )
    completed = await service.complete(
        run.run_id,
        HermesRunPatch(
            total_latency_ms=1200,
            llm_latency_ms=800,
            llm_calls=1,
            tokens_in=2100,
            tokens_out=160,
            total_tokens=2260,
            confidence=0.86,
            warnings_count=1,
            tool_errors_count=0,
            output_action="send_reply",
            output_ref="agent_action:88",
            details={
                "policy_decision": "auto_send",
                "trigger_telemetry": {
                    "telegram_update_received_at": 1_700_000_001.1,
                    "backend_webhook_received_at": 1_700_000_001.4,
                    "trigger_matched_at": 1_700_000_001.5,
                    "hermes_run_started_at": 1_700_000_001.842,
                    "telegram_update_to_hermes_run_started_ms": 742.0,
                    "backend_webhook_to_hermes_run_started_ms": 442.0,
                    "trigger_matched_to_hermes_run_started_ms": 342.0,
                },
            },
        ),
    )
    events = await service.events_for_run(run.run_id)

    payload = build_hermes_run_trace_payload(completed, events)
    lines = "\n".join(format_hermes_run_trace_lines(payload))

    assert payload["lane"] == "fast_interactive"
    assert payload["mode"] == "reply"
    assert payload["state"] == "completed"
    assert payload["latency"]["total_ms"] == 1200
    assert payload["tokens"]["total"] == 2260
    assert payload["confidence"] == 0.86
    assert payload["policy_decision"] == "auto_send"
    assert payload["trigger_telemetry"]["telegram_update_to_hermes_run_started_ms"] == 742.0
    assert payload["output"]["ref"] == "agent_action:88"
    assert [event["kind"] for event in payload["events"]] == [
        "created",
        "policy_checked",
        "tool_called",
        "completed",
    ]
    assert payload["events"][2]["talk"]["tool_name"] == "talk.bundle"
    assert "fast_interactive" in lines
    assert "2260" in lines
    assert "auto_send" in lines
    assert "telegram_update->hermes_started=742.0ms" in lines
    assert "backend_webhook->hermes_started=442.0ms" in lines
    assert "trigger_matched->hermes_started=342.0ms" in lines
    assert "talk.bundle queued actions=2" in lines
