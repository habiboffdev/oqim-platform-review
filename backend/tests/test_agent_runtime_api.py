from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.workspace import Workspace
from app.modules.agent_sessions.service import AgentSessionService
from app.modules.commercial_spine.contracts import CommercialActionProposal
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.hermes_runtime.contracts import (
    HermesRunEventInput,
    HermesRunEventKind,
    HermesRunInput,
    HermesRunPatch,
)
from app.modules.hermes_runtime.service import HermesRunService


async def test_agent_runtime_api_inspects_actions_runs_and_sessions(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    run_service = HermesRunService(db_session)
    run = await run_service.start_or_dedupe(
        HermesRunInput(
            run_id="hermes_run:api:trace",
            workspace_id=workspace.id,
            agent_id=agent.id,
            agent_kind="seller",
            run_mode="reply",
            lane="fast_interactive",
            trigger_type="conversation_turn",
            trigger_id="turn:api:1",
            conversation_id=conversation.id,
            customer_id=customer.id,
            correlation_id="corr:api:trace",
            idempotency_key="idem:api:trace",
            source_refs=["message:api:1"],
            input_summary="Assalomu alaykum",
        )
    )
    await run_service.record_event(
        HermesRunEventInput(
            event_id="event:api:context",
            run_id=run.run_id,
            workspace_id=workspace.id,
            kind=HermesRunEventKind.CONTEXT_GATHERED,
            payload={"runtime_context_packet": {"agent_session_id": 1}},
            correlation_id="corr:api:trace",
            idempotency_key="idem:api:trace:context",
        )
    )
    await run_service.complete(
        run.run_id,
        HermesRunPatch(
            tokens_in=444,
            tokens_out=33,
            output_action="auto_send",
            output_ref="talk_bundle:api",
            details={
                "generic_agent_runtime": {"entrypoint": "dispatch_agent_turn"},
                "runtime_context_packet": {
                    "static_context": {"cache_key": "agent-cache:api"},
                    "dynamic_context": {
                        "transcript_hit_count": 2,
                        "conversation_state_chars": 88,
                        "full_history_rebuild": False,
                    },
                },
                "runtime_telemetry": {
                    "context_efficiency": {
                        "static_cache_key": "agent-cache:api",
                        "dynamic_transcript_hit_count": 2,
                        "dynamic_conversation_state_chars": 88,
                        "full_history_rebuild": False,
                    },
                    "finalization": {
                        "blocked": False,
                        "reason_code": "supported_or_no_authority_claim",
                    },
                },
                "trace_metrics": {
                    "input_tokens": 444,
                    "output_tokens": 33,
                    "thought_tokens": 9,
                    "calls": [
                        {
                            "tool_calls": [{"name": "talk.send_msg"}],
                            "thought_summaries": ["Answer from approved context."],
                        }
                    ]
                },
                "agent_action": {"action_id": "agent_control:api"},
                "delivery": {"state": "confirmed"},
            },
        ),
    )
    proposal = CommercialActionProposal(
        proposal_id="proposal-api-trace",
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        action_type="send_reply",
        lifecycle_state="executed",
        execution_mode="auto_execute_if_policy_allows",
        risk_level="low",
        requires_approval=False,
        priority="medium",
        confidence=0.91,
        reason_code="agent_runtime_api_test",
        source_refs=[f"agent_run:{run.run_id}"],
        payload={
            "agent_control": {
                "action_id": "agent_control:api",
                "action_kind": "reply.send",
            },
            "draft_text": "Va alaykum assalom",
        },
        idempotency_key="idem:proposal-api-trace",
        correlation_id="corr:api:trace",
        trace_id=run.run_id,
    )
    await CommercialSpineRepository(db_session).persist_action_proposal(proposal)
    agent_session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel="telegram_dm",
    )
    await AgentSessionService(db_session).append_event(
        agent_session_id=agent_session.id,
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        agent_id=agent.id,
        event_type="customer_message",
        direction="inbound",
        hermes_run_id=run.run_id,
        text="Assalomu alaykum",
        idempotency_key="agent-session-api:inbound",
    )
    await db_session.commit()

    actions_response = await client.get(
        "/api/agent-runtime/actions",
        headers=auth_headers,
    )
    assert actions_response.status_code == 200, actions_response.text
    actions = actions_response.json()
    assert actions["schema_version"] == "agent_runtime_action_feed.v1"
    assert actions["actions"][0]["proposal_id"] == proposal.proposal_id
    assert actions["actions"][0]["trace_id"] == run.run_id
    assert actions["actions"][0]["agent_action"]["action_kind"] == "reply.send"

    run_response = await client.get(
        f"/api/agent-runtime/runs/{run.run_id}",
        headers=auth_headers,
    )
    assert run_response.status_code == 200, run_response.text
    run_trace = run_response.json()
    assert run_trace["schema_version"] == "agent_runtime_run_trace.v1"
    assert run_trace["run"]["run_id"] == run.run_id
    assert run_trace["run"]["tokens_out"] == 33
    assert run_trace["run"]["details"]["generic_agent_runtime"]["entrypoint"] == (
        "dispatch_agent_turn"
    )
    details = run_trace["run"]["details"]
    assert details["runtime_telemetry"]["context_efficiency"]["static_cache_key"] == (
        "agent-cache:api"
    )
    assert details["runtime_telemetry"]["finalization"]["blocked"] is False
    assert details["trace_metrics"]["thought_tokens"] == 9
    assert run_trace["events"][0]["kind"] == "created"
    assert run_trace["actions"][0]["proposal_id"] == proposal.proposal_id

    session_response = await client.get(
        f"/api/agent-runtime/sessions/{agent_session.id}",
        headers=auth_headers,
    )
    assert session_response.status_code == 200, session_response.text
    session_trace = session_response.json()
    assert session_trace["schema_version"] == "agent_runtime_session_trace.v1"
    assert session_trace["session"]["hermes_session_id"] == (
        agent_session.hermes_session_id
    )
    assert [event["event_type"] for event in session_trace["events"]] == [
        "customer_message"
    ]
