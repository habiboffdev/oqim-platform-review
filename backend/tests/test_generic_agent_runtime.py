from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_session import AgentSession, AgentSessionEvent
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.hermes_run import HermesRun
from app.models.message import Message, SenderType
from app.models.workspace import Workspace
from app.modules.agent_conversation_state.service import AgentConversationStateService
from app.modules.agent_runtime_v2.dispatcher import dispatch_agent_turn
from app.modules.agent_runtime_v2.reply_runtime import ReplyResult
from app.modules.agent_runtime_v2.trace import emit_trace_event
from app.modules.agent_talking.contracts import (
    TalkAction,
    TalkActionKind,
    TalkBundle,
    TalkingPolicy,
)
from app.modules.conversation_turns.service import ConversationTurnSessionService
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService
from app.services.delivery import DeliveryResult

pytestmark = pytest.mark.asyncio


@dataclass
class FakeDelivery:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def deliver_message(
        self,
        conversation_id: int,
        text: str,
        **kwargs: Any,
    ) -> DeliveryResult:
        self.calls.append({"conversation_id": conversation_id, "text": text, **kwargs})
        return DeliveryResult(
            success=True,
            external_message_id=f"tg:{len(self.calls)}",
            state="confirmed",
        )


async def test_satstation_customer_turn_runs_through_generic_agent_runtime(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
) -> None:
    agent.name = "SATStation"
    agent.agent_type = "seller"
    agent.trust_mode = "autopilot"
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope="telegram.send_message"),
    )
    customer_message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="Assalomu alaykum, sat bormi?",
        telegram_message_id=8844,
    )
    db_session.add(customer_message)
    await db_session.flush()
    turn = await ConversationTurnSessionService(db_session).append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=customer_message,
        agent_id=agent.id,
    )

    async def fake_hermes_run(self, **kwargs: Any) -> ReplyResult:
        hermes_run_id = str(kwargs["hermes_run_id"])
        await emit_trace_event(
            "llm",
            "success",
            operation="hermes_reply",
            provider="gemini",
            model="gemini-test",
            latency_ms=123,
            usage={
                "input_tokens": 321,
                "output_tokens": 24,
                "cached_content_tokens": 111,
                "thought_tokens": 7,
            },
            output_text_preview="Va alaykum assalom",
            tool_calls=[{"name": "talk.send_msg"}],
            thought_summaries=["Greet and answer the SAT availability question."],
        )
        bundle = TalkBundle(
            workspace_id=workspace.id,
            agent_id=agent.id,
            hermes_run_id=hermes_run_id,
            trigger_ref=kwargs["reply_to_message_ref"],
            conversation_id=conversation.id,
            actions=[
                TalkAction(
                    kind=TalkActionKind.SEND_MSG,
                    text="Va alaykum assalom",
                    idempotency_key=f"{hermes_run_id}:bubble:0",
                ),
                TalkAction(
                    kind=TalkActionKind.SEND_MSG,
                    text="SAT yo'q, lekin SATStation platformasida tayyorlansangiz bo'ladi.",
                    idempotency_key=f"{hermes_run_id}:bubble:1",
                ),
            ],
            talking_policy_snapshot=TalkingPolicy.seller_default(),
        )
        return ReplyResult(
            reply_text=bundle.text_preview(),
            confidence=0.0,
            grounding_hits=1,
            talk_bundle=bundle,
            turn_details={"observed_revision": turn.turn_revision, "pending_steer_count": 0},
        )

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run",
        fake_hermes_run,
    )
    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.runtime_service.judge_faithfulness",
        _always_faithful,
    )
    delivery = FakeDelivery()

    dispatched = await dispatch_agent_turn(
        db=db_session,
        workspace_id=workspace.id,
        telegram_chat_id=conversation.telegram_chat_id,
        customer=customer,
        conversation=conversation,
        message=customer_message,
        turn_session=turn,
        trigger_telemetry={"telegram_update_to_backend_ms": 12},
        delivery=delivery,
    )

    assert dispatched is True
    assert [call["text"] for call in delivery.calls] == [
        "Va alaykum assalom",
        "SAT yo'q, lekin SATStation platformasida tayyorlansangiz bo'ladi.",
    ]

    run = await db_session.scalar(
        select(HermesRun).where(
            HermesRun.workspace_id == workspace.id,
            HermesRun.conversation_id == conversation.id,
        )
    )
    assert run is not None
    assert run.state == "completed"
    assert run.output_action == "auto_send"
    assert run.tokens_in == 321
    assert run.tokens_out == 24
    assert run.details["generic_agent_runtime"]["entrypoint"] == "dispatch_agent_turn"
    assert run.details["generic_agent_runtime"]["profile_kind"] == "agent"
    assert run.details["generic_agent_runtime"]["execution_mode"] == "interactive"
    assert run.details["agent_session"]["agent_session_id"]
    assert run.details["agent_session"]["hermes_session_id"].startswith("oqim:agent-session:")
    assert run.details["runtime_context_packet"]["agent_session_id"] == (
        run.details["agent_session"]["agent_session_id"]
    )
    assert run.details["runtime_context_packet"]["customer_turn_chars"] > 0
    assert run.details["trace_metrics"]["calls"][0]["tool_calls"] == [
        {"name": "talk.send_msg"}
    ]
    assert run.details["agent_action"]["action_kind"] == "reply.send"
    assert run.details["agent_action"]["action_id"].startswith("agent_control:")
    assert run.details["delivery"]["state"] == "confirmed"
    assert run.details["delivery"]["message_ids"]

    action = await db_session.scalar(
        select(CommercialActionProposalRecord).where(
            CommercialActionProposalRecord.workspace_id == workspace.id,
            CommercialActionProposalRecord.trace_id == run.run_id,
        )
    )
    assert action is not None
    assert action.action_type == "send_reply"
    assert action.lifecycle_state == "executed"
    assert action.payload["agent_control"]["action_kind"] == "reply.send"

    agent_session = await db_session.scalar(
        select(AgentSession).where(
            AgentSession.workspace_id == workspace.id,
            AgentSession.conversation_id == conversation.id,
            AgentSession.agent_id == agent.id,
        )
    )
    assert agent_session is not None
    assert agent_session.hermes_session_id == run.details["agent_session"]["hermes_session_id"]
    agent_event = await db_session.scalar(
        select(AgentSessionEvent).where(
            AgentSessionEvent.agent_session_id == agent_session.id,
            AgentSessionEvent.event_type == "agent_action",
        )
    )
    assert agent_event is not None
    assert agent_event.hermes_run_id == run.run_id
    assert "talk_bundle_execution" in agent_event.payload


async def test_dispatch_suppresses_reply_for_opted_out_customer(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
) -> None:
    """DNC at the dispatch chokepoint (2026-06-18): an opted_out (Bog'lanmaslik)
    customer's turn must produce NO reply, whatever inbound path enqueued it. The
    inbound gate (_handle_customer_message) only covers the real-time persist path;
    catch-up recovery + channel sync bypass it, so on prod the seller replied to a
    do-not-contact lead (conv 3, run 185). dispatch_agent_turn is the single point
    every enqueued turn funnels through: it must return False (the runner then
    completes the turn via dispatch_skipped), create no reply run, and never reach
    the engine."""
    agent.agent_type = "seller"
    agent.trust_mode = "autopilot"
    customer.opted_out = True
    db_session.add(customer)
    await db_session.flush()

    async def _engine_must_not_run(self, **kwargs: Any) -> Any:
        raise AssertionError("the reply engine must not run for an opted_out customer")

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run",
        _engine_must_not_run,
    )

    customer_message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="hey",
        telegram_message_id=9911,
    )
    db_session.add(customer_message)
    await db_session.flush()
    turn = await ConversationTurnSessionService(db_session).append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=customer_message,
        agent_id=agent.id,
    )
    delivery = FakeDelivery()

    dispatched = await dispatch_agent_turn(
        db=db_session,
        workspace_id=workspace.id,
        telegram_chat_id=conversation.telegram_chat_id,
        customer=customer,
        conversation=conversation,
        message=customer_message,
        turn_session=turn,
        delivery=delivery,
    )

    assert dispatched is False  # runner completes the turn (dispatch_skipped)
    assert delivery.calls == []  # no customer-visible reply
    run = await db_session.scalar(
        select(HermesRun).where(
            HermesRun.workspace_id == workspace.id,
            HermesRun.conversation_id == conversation.id,
        )
    )
    assert run is None  # no reply run is created for a do-not-contact customer


async def test_dispatch_suppresses_reply_for_disabled_seller(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
) -> None:
    """Two trust states only (2026-06-18): a customer-facing agent is either
    'autopilot' (run + send) or 'disabled' (fully off). A 'disabled' seller must
    NOT run the LLM, create a reply run, or send/draft anything — dispatch returns
    False and the runner completes the turn via dispatch_skipped. This is the new
    default for a fresh account, so an owner who never enabled autopilot never has
    the agent process customer messages."""
    agent.agent_type = "seller"
    agent.trust_mode = "disabled"
    db_session.add(agent)
    await db_session.flush()

    async def _engine_must_not_run(self, **kwargs: Any) -> Any:
        raise AssertionError("the reply engine must not run for a disabled seller")

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run",
        _engine_must_not_run,
    )

    customer_message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="narx qancha?",
        telegram_message_id=9971,
    )
    db_session.add(customer_message)
    await db_session.flush()
    turn = await ConversationTurnSessionService(db_session).append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=customer_message,
        agent_id=agent.id,
    )
    delivery = FakeDelivery()

    dispatched = await dispatch_agent_turn(
        db=db_session,
        workspace_id=workspace.id,
        telegram_chat_id=conversation.telegram_chat_id,
        customer=customer,
        conversation=conversation,
        message=customer_message,
        turn_session=turn,
        delivery=delivery,
    )

    assert dispatched is False  # runner completes the turn (dispatch_skipped)
    assert delivery.calls == []  # no reply, no draft proposal
    run = await db_session.scalar(
        select(HermesRun).where(
            HermesRun.workspace_id == workspace.id,
            HermesRun.conversation_id == conversation.id,
        )
    )
    assert run is None  # no reply run is created for a disabled agent


async def test_generic_runtime_reuses_customer_hermes_session_and_reports_context_efficiency(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
) -> None:
    agent.name = "SATStation"
    agent.agent_type = "seller"
    agent.trust_mode = "autopilot"
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope="telegram.send_message"),
    )
    customer_b = Customer(
        workspace_id=workspace.id,
        display_name="Second Customer",
        language="uz",
    )
    db_session.add(customer_b)
    await db_session.flush()
    conversation_b = Conversation(
        workspace_id=workspace.id,
        customer_id=customer_b.id,
        telegram_chat_id=987654321,
        pipeline_stage="new",
    )
    db_session.add(conversation_b)
    await db_session.flush()

    hermes_calls: list[dict[str, Any]] = []

    async def fake_hermes_run(self, **kwargs: Any) -> ReplyResult:
        hermes_calls.append(dict(kwargs))
        hermes_run_id = str(kwargs["hermes_run_id"])
        await emit_trace_event(
            "llm",
            "success",
            operation="hermes_reply",
            provider="gemini",
            model="gemini-test",
            latency_ms=42,
            usage={
                "input_tokens": 200 + len(hermes_calls),
                "output_tokens": 12,
                "cached_content_tokens": 80,
                "thought_tokens": 3,
            },
            output_text_preview="Qisqa javob",
            tool_calls=[{"name": "talk.send_msg"}],
            thought_summaries=["Use session continuity and current turn context."],
        )
        bundle = TalkBundle(
            workspace_id=workspace.id,
            agent_id=agent.id,
            hermes_run_id=hermes_run_id,
            trigger_ref=kwargs["reply_to_message_ref"],
            conversation_id=kwargs["conversation_id"],
            actions=[
                TalkAction(
                    kind=TalkActionKind.SEND_MSG,
                    text=f"Javob {len(hermes_calls)}",
                    idempotency_key=f"{hermes_run_id}:bubble:0",
                )
            ],
            talking_policy_snapshot=TalkingPolicy.seller_default(),
        )
        return ReplyResult(
            reply_text=bundle.text_preview(),
            confidence=0.0,
            grounding_hits=1,
            talk_bundle=bundle,
            turn_details={
                "observed_revision": kwargs["turn_revision_start"],
                "pending_steer_count": 0,
            },
        )

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run",
        fake_hermes_run,
    )
    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.runtime_service.judge_faithfulness",
        _always_faithful,
    )
    delivery = FakeDelivery()

    first_run = await _dispatch_customer_text(
        db_session=db_session,
        workspace=workspace,
        agent=agent,
        customer=customer,
        conversation=conversation,
        text="Assalomu alaykum",
        telegram_message_id=9101,
        delivery=delivery,
    )
    await AgentConversationStateService(db_session).set_state(
        workspace_id=workspace.id,
        agent_session_id=first_run.details["agent_session"]["agent_session_id"],
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id=first_run.run_id,
        summary="Customer asked about preparation. Keep the next reply concise.",
        stage="lead",
        active_intent="learn_platform",
        next_best_action="answer current question with approved authority",
        idempotency_key="test:compact-state:customer-a",
    )
    second_run = await _dispatch_customer_text(
        db_session=db_session,
        workspace=workspace,
        agent=agent,
        customer=customer,
        conversation=conversation,
        text="narxi qancha?",
        telegram_message_id=9102,
        delivery=delivery,
    )
    other_customer_run = await _dispatch_customer_text(
        db_session=db_session,
        workspace=workspace,
        agent=agent,
        customer=customer_b,
        conversation=conversation_b,
        text="hello",
        telegram_message_id=9201,
        delivery=delivery,
    )

    assert len(hermes_calls) == 3
    assert hermes_calls[0]["hermes_session_id"] == hermes_calls[1]["hermes_session_id"]
    assert hermes_calls[2]["hermes_session_id"] != hermes_calls[0]["hermes_session_id"]
    assert first_run.details["agent_session"]["agent_session_id"] == (
        second_run.details["agent_session"]["agent_session_id"]
    )
    assert other_customer_run.details["agent_session"]["agent_session_id"] != (
        first_run.details["agent_session"]["agent_session_id"]
    )

    first_packet = first_run.details["runtime_context_packet"]
    second_packet = second_run.details["runtime_context_packet"]
    other_packet = other_customer_run.details["runtime_context_packet"]
    assert first_packet["static_context"]["cache_key"] == (
        second_packet["static_context"]["cache_key"]
    )
    assert first_packet["static_context"]["material_hash"] == (
        second_packet["static_context"]["material_hash"]
    )
    assert "profile:" in " ".join(first_packet["static_context"]["cache_keys"])
    assert second_packet["dynamic_context"]["customer_turn_chars"] == len("narxi qancha?")
    assert second_packet["dynamic_context"]["transcript_hit_count"] == 0
    assert second_packet["dynamic_context"]["conversation_state_chars"] > 2
    assert second_packet["dynamic_context"]["estimated_bytes"] < 16_000
    assert second_packet["dynamic_context"]["estimated_tokens"] < 4_000
    assert second_packet["dynamic_context"]["full_history_rebuild"] is False
    assert hermes_calls[1]["conversation_state"]["active_intent"] == "learn_platform"
    assert other_packet["dynamic_context"]["transcript_hit_count"] == 1
    assert second_run.details["runtime_telemetry"]["context_efficiency"]["static_cache_key"] == (
        second_packet["static_context"]["cache_key"]
    )
    assert second_run.details["runtime_telemetry"]["context_efficiency"][
        "dynamic_estimated_tokens"
    ] == second_packet["dynamic_context"]["estimated_tokens"]
    assert second_run.details["trace_metrics"]["cache_effective_input_tokens"] < (
        second_run.details["trace_metrics"]["input_tokens"]
    )


async def test_generic_runtime_blocks_stale_autopilot_output_with_debuggable_trace(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
) -> None:
    agent.name = "SATStation"
    agent.agent_type = "seller"
    agent.trust_mode = "autopilot"
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope="telegram.send_message"),
    )
    first_message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="Assalomu alaykum",
        telegram_message_id=9301,
    )
    db_session.add(first_message)
    await db_session.flush()
    turn = await ConversationTurnSessionService(db_session).append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=first_message,
        agent_id=agent.id,
    )

    async def fake_slow_hermes_run(self, **kwargs: Any) -> ReplyResult:
        second_message = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type=SenderType.CUSTOMER.value,
            content="narxi qancha?",
            telegram_message_id=9302,
        )
        db_session.add(second_message)
        await db_session.flush()
        await ConversationTurnSessionService(db_session).append_customer_message(
            workspace_id=workspace.id,
            conversation=conversation,
            customer=customer,
            message=second_message,
            agent_id=agent.id,
        )
        hermes_run_id = str(kwargs["hermes_run_id"])
        await emit_trace_event(
            "llm",
            "success",
            operation="hermes_reply",
            usage={"input_tokens": 199, "output_tokens": 10},
            tool_calls=[{"name": "talk.send_msg"}],
            thought_summaries=["This answer only observed the first bubble."],
        )
        bundle = TalkBundle(
            workspace_id=workspace.id,
            agent_id=agent.id,
            hermes_run_id=hermes_run_id,
            trigger_ref=kwargs["reply_to_message_ref"],
            conversation_id=conversation.id,
            actions=[
                TalkAction(
                    kind=TalkActionKind.SEND_MSG,
                    text="Stale hello reply",
                    idempotency_key=f"{hermes_run_id}:bubble:0",
                )
            ],
            talking_policy_snapshot=TalkingPolicy.seller_default(),
        )
        return ReplyResult(
            reply_text=bundle.text_preview(),
            confidence=0.0,
            grounding_hits=1,
            talk_bundle=bundle,
            turn_details={
                "observed_revision": kwargs["turn_revision_start"],
                "pending_steer_count": 0,
            },
        )

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run",
        fake_slow_hermes_run,
    )
    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.runtime_service.judge_faithfulness",
        _always_faithful,
    )
    delivery = FakeDelivery()

    dispatched = await dispatch_agent_turn(
        db=db_session,
        workspace_id=workspace.id,
        telegram_chat_id=conversation.telegram_chat_id,
        customer=customer,
        conversation=conversation,
        message=first_message,
        turn_session=turn,
        trigger_telemetry={"telegram_update_to_backend_ms": 5},
        delivery=delivery,
    )

    assert dispatched is True
    assert delivery.calls == []
    run = await db_session.scalar(
        select(HermesRun).where(
            HermesRun.workspace_id == workspace.id,
            HermesRun.trigger_id == f"turn:{turn.id}:rev:1:gen:1",
        )
    )
    assert run is not None
    assert run.state == "skipped"
    assert run.details["turn_finalization"]["can_deliver"] is False
    assert run.details["turn_finalization"]["reason"] == "turn_revision_not_observed"
    assert run.details["generic_agent_runtime"]["entrypoint"] == "dispatch_agent_turn"
    assert run.details["agent_session"]["hermes_session_id"].startswith(
        "oqim:agent-session:"
    )
    assert run.details["runtime_context_packet"]["dynamic_context"][
        "customer_turn_chars"
    ] == len("Assalomu alaykum")
    assert run.details["delivery"]["state"] == "not_executed"
    await db_session.refresh(turn)
    assert turn.state == "continued"
    assert turn.turn_revision == 2
    assert turn.latest_customer_message_id != first_message.id


async def test_setup_agent_turn_creates_generic_action_without_talk_tools(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
) -> None:
    agent.name = "OQIM Setup"
    agent.agent_type = "setup"
    # A setup agent is excluded from the disabled gate: it runs regardless of
    # trust_mode to emit owner approval proposals (it never auto-sends to customers).
    agent.trust_mode = "disabled"
    message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="I want to setup OQIM. Here are my SAT files.",
        telegram_message_id=9401,
    )
    db_session.add(message)
    await db_session.flush()
    turn = await ConversationTurnSessionService(db_session).append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=message,
        agent_id=agent.id,
    )
    seen_profile: dict[str, Any] = {}

    async def fake_setup_hermes_run(self, **kwargs: Any) -> ReplyResult:
        profile = kwargs["profile"]
        seen_profile["profile_kind"] = profile.profile_kind
        seen_profile["execution_mode"] = profile.execution_mode
        seen_profile["allowed_tool_names"] = list(profile.allowed_tool_names)
        await emit_trace_event(
            "llm",
            "success",
            operation="hermes_setup",
            usage={"input_tokens": 250, "output_tokens": 20},
            tool_calls=[{"name": "knowledge_create_source_doc"}],
            thought_summaries=["Create a source intake proposal for owner files."],
        )
        return ReplyResult(
            reply_text="",
            confidence=1.0,
            grounding_hits=1,
            agent_actions=[
                {
                    "user_id": f"workspace:{workspace.id}:owner",
                    "action_kind": "knowledge.write",
                    "target_ref": "source_intake:onboarding:sat_files",
                    "proposed_payload": {
                        "proposal_kind": "source_intake",
                        "source_refs": ["owner_upload:sat_files"],
                        "document_updates": ["BUSINESS.md", "AGENT.md"],
                    },
                    "risk_level": "medium",
                    "evidence_refs": ["message:setup-files"],
                    "approval_required": True,
                }
            ],
            turn_details={
                "observed_revision": kwargs["turn_revision_start"],
                "pending_steer_count": 0,
            },
        )

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run",
        fake_setup_hermes_run,
    )
    delivery = FakeDelivery()

    dispatched = await dispatch_agent_turn(
        db=db_session,
        workspace_id=workspace.id,
        telegram_chat_id=conversation.telegram_chat_id,
        customer=customer,
        conversation=conversation,
        message=message,
        turn_session=turn,
        trigger_telemetry={"telegram_update_to_backend_ms": 6},
        delivery=delivery,
    )

    assert dispatched is True
    assert delivery.calls == []
    assert seen_profile["profile_kind"] == "agent"
    assert seen_profile["execution_mode"] == "setup"
    assert not any(
        tool.startswith("talk.") for tool in seen_profile["allowed_tool_names"]
    )
    action = await db_session.scalar(
        select(CommercialActionProposalRecord).where(
            CommercialActionProposalRecord.workspace_id == workspace.id,
            CommercialActionProposalRecord.trace_id.like("hermes_run:%"),
            CommercialActionProposalRecord.action_type == "knowledge.write",
        )
    )
    assert action is not None
    assert action.lifecycle_state == "waiting_approval"
    assert action.payload["agent_control"]["target_ref"] == (
        "source_intake:onboarding:sat_files"
    )
    run = await db_session.scalar(
        select(HermesRun).where(
            HermesRun.workspace_id == workspace.id,
            HermesRun.trigger_id == f"turn:{turn.id}:rev:1:gen:1",
        )
    )
    assert run is not None
    assert run.state == "completed"
    assert run.output_action == "agent_actions"
    assert run.details["generic_agent_runtime"]["profile_kind"] == "agent"
    assert run.details["generic_agent_runtime"]["execution_mode"] == "setup"
    assert run.details["agent_actions"][0]["action_kind"] == "knowledge.write"
    assert run.details["delivery"]["state"] == "not_executed"


async def _dispatch_customer_text(
    *,
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
    text: str,
    telegram_message_id: int,
    delivery: FakeDelivery,
) -> HermesRun:
    message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content=text,
        telegram_message_id=telegram_message_id,
    )
    db_session.add(message)
    await db_session.flush()
    turn = await ConversationTurnSessionService(db_session).append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=message,
        agent_id=agent.id,
    )
    dispatched = await dispatch_agent_turn(
        db=db_session,
        workspace_id=workspace.id,
        telegram_chat_id=conversation.telegram_chat_id,
        customer=customer,
        conversation=conversation,
        message=message,
        turn_session=turn,
        trigger_telemetry={"telegram_update_to_backend_ms": 7},
        delivery=delivery,
    )
    assert dispatched is True
    run = await db_session.scalar(
        select(HermesRun).where(
            HermesRun.workspace_id == workspace.id,
            HermesRun.trigger_id
            == f"turn:{turn.id}:rev:{turn.turn_revision}:gen:{turn.generation}",
        )
    )
    assert run is not None
    return run


class _FaithfulnessVerdict:
    unsupported_authority_claims = 0


async def _always_faithful(**_: Any) -> _FaithfulnessVerdict:
    return _FaithfulnessVerdict()
