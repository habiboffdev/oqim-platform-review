"""Owner-turn entrypoint (spike #439).

dispatch_owner_turn is the customer-free sibling of dispatch_agent_turn: the
owner talks to the setup agent over the owner channel (no Customer, no
Conversation). Owner sessions are keyed by owner_chat_id with conversation_id
NULL (Option B migration), so owner memory stays stable across turns.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select

from app.models.agent_session import AgentSession
from app.models.hermes_run import HermesRun
from app.modules.agent_sessions.service import AgentSessionService

pytestmark = pytest.mark.asyncio


@dataclass
class FakeDelivery:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def deliver_message(self, conversation_id, text, **kwargs):
        from app.services.delivery import DeliveryResult

        self.calls.append({"conversation_id": conversation_id, "text": text, **kwargs})
        return DeliveryResult(
            success=True, external_message_id=f"tg:{len(self.calls)}", state="confirmed"
        )


async def test_owner_session_get_or_create_dedupes_by_owner_chat(
    db_session, workspace, agent
):
    svc = AgentSessionService(db_session)
    s1 = await svc.get_or_create(
        workspace_id=workspace.id,
        conversation_id=None,
        customer_id=None,
        agent_id=agent.id,
        channel="owner",
        owner_chat_id=555,
    )
    s2 = await svc.get_or_create(
        workspace_id=workspace.id,
        conversation_id=None,
        customer_id=None,
        agent_id=agent.id,
        channel="owner",
        owner_chat_id=555,
    )
    assert s1.id == s2.id  # same owner chat -> one stable session
    assert s1.conversation_id is None
    assert s1.owner_chat_id == 555
    assert s1.channel == "owner"

    s3 = await svc.get_or_create(
        workspace_id=workspace.id,
        conversation_id=None,
        customer_id=None,
        agent_id=agent.id,
        channel="owner",
        owner_chat_id=999,
    )
    assert s3.id != s1.id  # different owner chat -> different session

    rows = (
        await db_session.scalars(
            select(AgentSession).where(
                AgentSession.workspace_id == workspace.id,
                AgentSession.owner_chat_id == 555,
            )
        )
    ).all()
    assert len(rows) == 1  # partial unique index held


async def test_owner_turn_runs_workspace_scoped_setup_run(
    db_session, workspace, agent, monkeypatch
):
    from app.modules.agent_runtime_v2.owner_turn import dispatch_owner_turn
    from app.modules.agent_runtime_v2.reply_runtime import ReplyResult

    agent.agent_type = "setup"
    await db_session.flush()

    async def fake_run(self, **kwargs):
        # owner turns have no customer/conversation
        assert kwargs.get("conversation_id") is None
        return ReplyResult(
            reply_text="Tayyor.", confidence=0.0, grounding_hits=0, turn_details={}
        )

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run", fake_run
    )

    delivery = FakeDelivery()
    ok = await dispatch_owner_turn(
        db=db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        owner_chat_id=555,
        message_text="AGENT.md ni yangila",
        delivery=delivery,
    )

    assert ok is True
    assert delivery.calls, "owner reply was not delivered"
    assert delivery.calls[0]["text"] == "Tayyor."
    assert delivery.calls[0]["conversation_id"] == 555  # delivered to the owner chat

    run = await db_session.scalar(
        select(HermesRun).where(HermesRun.workspace_id == workspace.id)
    )
    assert run is not None
    assert run.state == "completed"
    assert run.conversation_id is None
    assert run.customer_id is None
    assert run.details["generic_agent_runtime"]["execution_mode"] == "setup"


async def test_owner_turn_handles_multiple_messages_in_one_session(
    db_session, workspace, agent, monkeypatch
):
    """Regression: every owner message must produce its own run + reply. A
    constant run-dedupe key silently swallowed all messages after the first."""
    from app.modules.agent_runtime_v2.owner_turn import dispatch_owner_turn
    from app.modules.agent_runtime_v2.reply_runtime import ReplyResult

    agent.agent_type = "setup"
    await db_session.flush()

    seen: list[str] = []

    async def fake_run(self, **kwargs):
        seen.append(kwargs.get("customer_message") or "")
        return ReplyResult(
            reply_text=f"javob {len(seen)}",
            confidence=0.0,
            grounding_hits=0,
            turn_details={},
        )

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run", fake_run
    )

    delivery = FakeDelivery()
    for text in ("birinchi xabar", "ikkinchi boshqa xabar", "uchinchi xabar"):
        ok = await dispatch_owner_turn(
            db=db_session,
            workspace_id=workspace.id,
            agent_id=agent.id,
            owner_chat_id=555,
            message_text=text,
            delivery=delivery,
        )
        assert ok is True

    # all three messages reached the engine and produced a delivered reply
    assert len(seen) == 3
    assert len(delivery.calls) == 3
    runs = (
        await db_session.scalars(
            select(HermesRun).where(HermesRun.workspace_id == workspace.id)
        )
    ).all()
    assert len(runs) == 3
