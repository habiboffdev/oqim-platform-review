from __future__ import annotations

from typing import Any

from app.modules.hermes_runtime.contracts import HermesRunEventSnapshot, HermesRunSnapshot


def build_hermes_run_trace_payload(
    run: HermesRunSnapshot,
    events: list[HermesRunEventSnapshot],
) -> dict[str, Any]:
    """Owner-safe runtime trace summary for CLI/operator gates."""

    details = run.details if isinstance(run.details, dict) else {}
    policy_decision = details.get("policy_decision")
    circuit_breaker = details.get("autopilot_circuit_breaker")
    trigger_telemetry = details.get("trigger_telemetry")
    return {
        "run_id": run.run_id,
        "workspace_id": run.workspace_id,
        "agent_id": run.agent_id,
        "agent_kind": run.agent_kind,
        "mode": run.run_mode.value if hasattr(run.run_mode, "value") else str(run.run_mode),
        "lane": run.lane.value if hasattr(run.lane, "value") else str(run.lane),
        "state": run.state.value if hasattr(run.state, "value") else str(run.state),
        "trigger": {
            "type": run.trigger_type,
            "id": run.trigger_id,
            "event_id": run.event_id,
            "conversation_id": run.conversation_id,
            "customer_id": run.customer_id,
        },
        "runtime_profile": {
            "snapshot_id": run.runtime_profile_snapshot_id,
            "cache_key": run.runtime_profile_cache_key,
        },
        "latency": {
            "total_ms": run.total_latency_ms,
            "llm_ms": run.llm_latency_ms,
            "llm_calls": run.llm_calls,
        },
        "tokens": {
            "input": run.tokens_in,
            "output": run.tokens_out,
            "total": run.total_tokens,
        },
        "confidence": run.confidence,
        "warnings_count": run.warnings_count,
        "tool_errors_count": run.tool_errors_count,
        "trigger_telemetry": trigger_telemetry if isinstance(trigger_telemetry, dict) else {},
        "policy_decision": policy_decision,
        "autopilot_circuit_breaker": circuit_breaker,
        "output": {
            "action": run.output_action,
            "ref": run.output_ref,
        },
        "error": {
            "code": run.error_code,
            "message": run.error_message,
        },
        "events": [
            _format_event(event)
            for event in events
        ],
    }


def _format_event(event: HermesRunEventSnapshot) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    formatted = {
        "sequence": event.sequence,
        "kind": event.kind.value if hasattr(event.kind, "value") else str(event.kind),
        "visibility": event.visibility,
        "tool_name": event.tool_name,
        "tool_state": event.tool_state,
        "action_proposal_id": event.action_proposal_id,
        "payload": event.payload,
        "created_at": event.created_at,
    }
    if event.tool_name and (
        event.tool_name.startswith("talk.") or event.tool_name == "talk.bundle"
    ):
        formatted["talk"] = {
            "tool_name": event.tool_name,
            "tool_state": event.tool_state,
            "action_count": payload.get("action_count"),
        }
    return formatted


def format_hermes_run_trace_lines(payload: dict[str, Any]) -> list[str]:
    trigger = payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {}
    latency = payload.get("latency") if isinstance(payload.get("latency"), dict) else {}
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    trigger_telemetry = (
        payload.get("trigger_telemetry")
        if isinstance(payload.get("trigger_telemetry"), dict)
        else {}
    )
    lines = [
        f"run:          {payload.get('run_id')}",
        f"workspace:    {payload.get('workspace_id')}  agent: {payload.get('agent_id')}",
        f"mode/lane:    {payload.get('mode')} / {payload.get('lane')}",
        f"state:        {payload.get('state')}",
        (
            "trigger:      "
            f"{trigger.get('type')}:{trigger.get('id')}  "
            f"event={trigger.get('event_id')}  conversation={trigger.get('conversation_id')}"
        ),
        (
            "latency:      "
            f"total={latency.get('total_ms')}ms  "
            f"llm={latency.get('llm_ms')}ms  calls={latency.get('llm_calls')}"
        ),
        (
            "tokens:       "
            f"in={tokens.get('input')}  out={tokens.get('output')}  total={tokens.get('total')}"
        ),
        f"confidence:   {payload.get('confidence')}",
        f"warnings:     {payload.get('warnings_count')}  tool_errors={payload.get('tool_errors_count')}",
        f"policy:       {payload.get('policy_decision') or '(none recorded)'}",
        f"output:       {output.get('action')} -> {output.get('ref')}",
    ]
    if trigger_telemetry:
        lines.append(
            "gateway:      "
            f"telegram_update->hermes_started="
            f"{trigger_telemetry.get('telegram_update_to_hermes_run_started_ms')}ms  "
            f"backend_webhook->hermes_started="
            f"{trigger_telemetry.get('backend_webhook_to_hermes_run_started_ms')}ms  "
            f"trigger_matched->hermes_started="
            f"{trigger_telemetry.get('trigger_matched_to_hermes_run_started_ms')}ms"
        )
    breaker = payload.get("autopilot_circuit_breaker")
    if breaker:
        lines.append(f"breaker:      {breaker}")
    error = payload.get("error")
    if isinstance(error, dict) and (error.get("code") or error.get("message")):
        lines.append(f"error:        {error.get('code')} {error.get('message')}")
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        talk = event.get("talk")
        if not isinstance(talk, dict):
            continue
        action_count = talk.get("action_count")
        suffix = f" actions={action_count}" if action_count is not None else ""
        lines.append(
            "talk:         "
            f"{talk.get('tool_name')} {talk.get('tool_state')}{suffix}"
        )
    return lines
