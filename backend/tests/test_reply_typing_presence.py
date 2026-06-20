from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.modules.agent_runtime_v2.dispatcher as dispatcher_mod
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message, SenderType
from app.models.workspace import Workspace
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


class FakePresence:
    def __init__(self, **_kwargs: Any) -> None:
        self.typing_pulses = 0
        self.calls: list[dict[str, Any]] = []

    async def pulse(self, *, workspace_id: int, chat_id: str, online: bool = True,
                    read: bool = True, typing: bool | None = True, **_kw: Any) -> Any:
        self.calls.append({"workspace_id": workspace_id, "chat_id": chat_id,
                           "online": online, "read": read, "typing": typing})
        if typing:
            self.typing_pulses += 1
        return None


class _FaithfulnessVerdict:
    unsupported_authority_claims = 0


async def _always_faithful(**_: Any) -> _FaithfulnessVerdict:
    return _FaithfulnessVerdict()


async def test_typing_pulses_while_thinking(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_presence = FakePresence()
    monkeypatch.setattr(dispatcher_mod, "TalkPresenceService", lambda **kw: fake_presence)
    monkeypatch.setattr(dispatcher_mod, "_REPLY_TYPING_FIRST_DELAY_S", 0.0)
    monkeypatch.setattr(dispatcher_mod, "_REPLY_TYPING_INTERVAL_S", 0.01)

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
        content="Hello, are you there?",
        telegram_message_id=7001,
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

    async def slow_hermes_run(self, **kwargs: Any) -> ReplyResult:
        await asyncio.sleep(0.1)
        hermes_run_id = str(kwargs["hermes_run_id"])
        await emit_trace_event(
            "llm",
            "success",
            operation="hermes_reply",
            provider="gemini",
            model="gemini-test",
            latency_ms=100,
            usage={
                "input_tokens": 100,
                "output_tokens": 10,
                "cached_content_tokens": 0,
                "thought_tokens": 2,
            },
            output_text_preview="Yes, I'm here!",
            tool_calls=[{"name": "talk.send_msg"}],
            thought_summaries=["Respond to greeting."],
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
                    text="Yes, I'm here!",
                    idempotency_key=f"{hermes_run_id}:bubble:0",
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
        slow_hermes_run,
    )
    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.runtime_service.judge_faithfulness",
        _always_faithful,
    )

    fake_delivery = FakeDelivery()

    dispatched = await dispatch_agent_turn(
        db=db_session,
        workspace_id=workspace.id,
        telegram_chat_id=12345,
        customer=customer,
        conversation=conversation,
        message=customer_message,
        turn_session=turn,
        delivery=fake_delivery,
    )

    assert dispatched is True
    assert fake_presence.typing_pulses >= 1
    assert all(c["typing"] for c in fake_presence.calls)


# ---------------------------------------------------------------------------
# Task 2: Resilience tests
# ---------------------------------------------------------------------------


async def _build_message_and_turn(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
) -> tuple[Message, Any]:
    """Shared harness: add tool grant, create message + turn session."""
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
        content="Hello, are you there?",
        telegram_message_id=7002,
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
    return customer_message, turn


def _make_fast_hermes_run(workspace: Workspace, agent: Agent, conversation: Conversation):
    """Return a fast (no sleep) fake HermesEngineAdapter.run that produces a valid ReplyResult."""
    from app.modules.agent_runtime_v2.reply_runtime import ReplyResult
    from app.modules.agent_runtime_v2.trace import emit_trace_event
    from app.modules.agent_talking.contracts import (
        TalkAction,
        TalkActionKind,
        TalkBundle,
        TalkingPolicy,
    )

    async def fast_run(self, **kwargs: Any) -> ReplyResult:
        hermes_run_id = str(kwargs["hermes_run_id"])
        await emit_trace_event(
            "llm",
            "success",
            operation="hermes_reply",
            provider="gemini",
            model="gemini-test",
            latency_ms=10,
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_content_tokens": 0,
                "thought_tokens": 0,
            },
            output_text_preview="Hi!",
            tool_calls=[{"name": "talk.send_msg"}],
            thought_summaries=[],
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
                    text="Hi!",
                    idempotency_key=f"{hermes_run_id}:bubble:0",
                ),
            ],
            talking_policy_snapshot=TalkingPolicy.seller_default(),
        )
        return ReplyResult(
            reply_text=bundle.text_preview(),
            confidence=0.0,
            grounding_hits=1,
            talk_bundle=bundle,
            turn_details={"observed_revision": 1, "pending_steer_count": 0},
        )

    return fast_run


async def test_presence_failure_does_not_block_reply(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crashing presence sidecar must never prevent the reply from being delivered."""

    class BoomPresence(FakePresence):
        async def pulse(self, **kw: Any) -> Any:
            raise RuntimeError("sidecar down")

    boom = BoomPresence()
    monkeypatch.setattr(dispatcher_mod, "TalkPresenceService", lambda **kw: boom)
    monkeypatch.setattr(dispatcher_mod, "_REPLY_TYPING_FIRST_DELAY_S", 0.0)
    monkeypatch.setattr(dispatcher_mod, "_REPLY_TYPING_INTERVAL_S", 0.01)
    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.runtime_service.judge_faithfulness",
        _always_faithful,
    )

    customer_message, turn = await _build_message_and_turn(
        db_session, workspace, agent, customer, conversation
    )
    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run",
        _make_fast_hermes_run(workspace, agent, conversation),
    )
    fake_delivery = FakeDelivery()

    dispatched = await dispatcher_mod.dispatch_agent_turn(
        db=db_session,
        workspace_id=workspace.id,
        telegram_chat_id=12345,
        customer=customer,
        conversation=conversation,
        message=customer_message,
        turn_session=turn,
        delivery=fake_delivery,
    )
    assert dispatched is True
    assert len(fake_delivery.calls) >= 1  # reply still delivered despite presence errors


async def test_empty_chat_id_skips_heartbeat(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no chat id is resolvable the heartbeat must not start (no pulses, no crash)."""
    fake_presence = FakePresence()
    monkeypatch.setattr(dispatcher_mod, "TalkPresenceService", lambda **kw: fake_presence)
    monkeypatch.setattr(dispatcher_mod, "_REPLY_TYPING_FIRST_DELAY_S", 0.0)
    monkeypatch.setattr(dispatcher_mod, "_REPLY_TYPING_INTERVAL_S", 0.01)
    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.runtime_service.judge_faithfulness",
        _always_faithful,
    )

    # Strip every chat-id path so the dispatcher sees no resolvable id
    conversation.external_chat_id = None
    conversation.telegram_chat_id = None

    customer_message, turn = await _build_message_and_turn(
        db_session, workspace, agent, customer, conversation
    )
    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run",
        _make_fast_hermes_run(workspace, agent, conversation),
    )
    fake_delivery = FakeDelivery()

    dispatched = await dispatcher_mod.dispatch_agent_turn(
        db=db_session,
        workspace_id=workspace.id,
        telegram_chat_id=None,
        customer=customer,
        conversation=conversation,
        message=customer_message,
        turn_session=turn,
        delivery=fake_delivery,
    )
    assert dispatched is True
    assert fake_presence.typing_pulses == 0  # no chat id → no heartbeat, no crash


async def test_heartbeat_cancelled_when_run_raises(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    customer: Customer,
    conversation: Conversation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When hermes.run raises, the finally block must cancel the heartbeat cleanly."""
    fake_presence = FakePresence()
    monkeypatch.setattr(dispatcher_mod, "TalkPresenceService", lambda **kw: fake_presence)
    monkeypatch.setattr(dispatcher_mod, "_REPLY_TYPING_FIRST_DELAY_S", 0.0)
    monkeypatch.setattr(dispatcher_mod, "_REPLY_TYPING_INTERVAL_S", 0.01)
    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.runtime_service.judge_faithfulness",
        _always_faithful,
    )

    async def boom_run(self, **kwargs: Any) -> Any:
        await asyncio.sleep(0.02)
        raise RuntimeError("hermes blew up")

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run",
        boom_run,
    )

    customer_message, turn = await _build_message_and_turn(
        db_session, workspace, agent, customer, conversation
    )
    fake_delivery = FakeDelivery()

    with pytest.raises(RuntimeError, match="hermes blew up"):
        await dispatcher_mod.dispatch_agent_turn(
            db=db_session,
            workspace_id=workspace.id,
            telegram_chat_id=12345,
            customer=customer,
            conversation=conversation,
            message=customer_message,
            turn_session=turn,
            delivery=fake_delivery,
        )

    # Give the event loop a tick so any improperly-uncancelled tasks would surface.
    await asyncio.sleep(0.05)
    assert fake_presence.typing_pulses >= 1  # heartbeat was running before the raise


async def test_hot_inbound_presence_skipped_for_dnc_customer(
    db_session: AsyncSession,
    conversation: Conversation,
    customer: Customer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DNC true silence (2026-06-18): a do-not-contact (opted_out) customer's
    inbound message must NOT trigger online presence OR a read receipt. These fire
    on RECEIPT in the persist consumer, before the reply dispatch gate -- so without
    this guard the userbot goes online and marks the message 'seen' even though it
    never replies (owner-reported leak: 'why did it go online and read if it
    doesn't reply')."""
    from types import SimpleNamespace

    from app.services import event_spine_persist_consumer as consumer_mod

    customer.opted_out = True

    def _must_not_pulse(**_kw: Any) -> Any:
        raise AssertionError(
            "presence/read must not pulse for an opted_out (DNC) customer"
        )

    monkeypatch.setattr(consumer_mod, "TalkPresenceService", _must_not_pulse)

    class _Redis:
        async def incr(self, *_a: Any, **_k: Any) -> int:
            return 1

    event = SimpleNamespace(
        channel="telegram_dm",
        telegram_chat_id=conversation.telegram_chat_id,
        telegram_message_id=4242,
    )
    message = SimpleNamespace(telegram_message_id=4242, id=1)

    # With the DNC guard this returns immediately (TalkPresenceService never built);
    # without it, _must_not_pulse raises.
    await consumer_mod._pulse_hot_inbound_presence(
        session=db_session,
        redis=_Redis(),
        event=event,
        conversation=conversation,
        message=message,
        customer=customer,
    )
