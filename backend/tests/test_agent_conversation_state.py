from __future__ import annotations

from importlib import import_module

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_session import AgentSession
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.workspace import Workspace
from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfileCompiler
from app.modules.agent_runtime_v2.runtime_service import AgentRuntimeService
from app.modules.agent_sessions.service import AgentSessionService

pytestmark = pytest.mark.asyncio


def _state_service_cls():
    try:
        module = import_module("app.modules.agent_conversation_state.service")
    except ModuleNotFoundError as exc:
        pytest.fail(f"agent conversation state service is not implemented: {exc}")
    return module.AgentConversationStateService


async def _agent_session(
    db_session: AsyncSession,
    *,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
) -> AgentSession:
    return await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )


async def test_conversation_set_state_commits_idempotent_agent_session_snapshot(
    db_session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
) -> None:
    session = await _agent_session(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
    )
    service = _state_service_cls()(db_session)

    first = await service.set_state(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:test-state",
        summary="Customer wants to buy a catalog item and chose Click.",
        stage="checkout",
        active_intent="buy",
        selected_items=[
            {
                "item_ref": "catalog_item:test-store:starter",
                "title": "Starter pack",
                "quantity": 1,
            }
        ],
        shown_prices=[
            {
                "item_ref": "catalog_item:test-store:starter",
                "amount": 40000,
                "currency": "UZS",
                "authority_ref": "catalog_offer:test-store:starter",
            }
        ],
        payment={"method": "click", "status": "details_missing"},
        missing_authority=["click_payment_details"],
        next_best_action="collect_contact_and_notify_owner",
        risk_flags=["missing_payment_authority"],
        source_refs=["message:123", "hermes_run:test-state"],
        idempotency_key="state:test-store:checkout",
    )
    second = await service.set_state(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:test-state",
        summary="duplicate replay should not create another snapshot",
        stage="checkout",
        active_intent="buy",
        source_refs=["message:123"],
        idempotency_key="state:test-store:checkout",
    )

    assert first.snapshot_id == second.snapshot_id
    assert first.stage == "checkout"
    assert first.active_intent == "buy"
    assert first.state["selected_items"][0]["title"] == "Starter pack"
    assert first.state["payment"]["status"] == "details_missing"
    assert first.state["missing_authority"] == ["click_payment_details"]

    rows = await db_session.execute(
        select(service.snapshot_model).where(
            service.snapshot_model.agent_session_id == session.id
        )
    )
    assert len(rows.scalars().all()) == 1


async def test_gather_context_includes_latest_compact_conversation_state(
    db_session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
) -> None:
    session = await _agent_session(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
    )
    await _state_service_cls()(db_session).set_state(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:previous",
        summary="Customer is in checkout for a generic item.",
        stage="checkout",
        active_intent="buy",
        selected_items=[{"item_ref": "catalog_item:any-store:any-item"}],
        missing_authority=["payment_details"],
        next_best_action="create_owner_handoff",
        source_refs=["message:321"],
        idempotency_key="state:any-store:checkout",
    )

    ctx = await AgentRuntimeService(db_session).gather_turn_context(
        workspace_id=workspace.id,
        agent_id=agent.id,
        customer_message="ok click",
        conversation_id=conversation.id,
        agent_session_id=session.id,
        hermes_session_id=session.hermes_session_id,
    )

    state = ctx.conversation_state
    assert state["stage"] == "checkout"
    assert state["active_intent"] == "buy"
    assert state["missing_authority"] == ["payment_details"]
    # the compact state is still reflected in the telemetry payload
    assert ctx.runtime_context_packet["dynamic_context"]["conversation_state_chars"] > 0


async def test_apply_turn_facts_prev_facts_survive_interleaved_set_state(
    db_session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
) -> None:
    """S4 (#423): a newer ``conversation.set_state`` snapshot carries no ``facts``
    key. The next ``apply_turn_facts`` must read prior facts from the latest
    *facts* snapshot, not the newest row — so accrued facts (e.g. a recorded
    handoff) carry forward instead of silently resetting to ``{}``.
    """
    from app.modules.agent_conversation_state.reducer import TurnSignals

    session = await _agent_session(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
    )
    service = _state_service_cls()(db_session)

    # turn 1 — a handoff happened: facts gain handoff_recorded="lead"
    first = await service.apply_turn_facts(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="run-1",
        signals=TurnSignals(reply_delivered=True, handoff_kinds=["lead"]),
    )
    assert first is not None
    assert first.state["facts"]["handoff_recorded"] == "lead"

    # interleave — a newer set_state packet (real shape: no "facts" key)
    await service.set_state(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="run-1b",
        summary="checkout details collected",
        stage="checkout",
        active_intent="buy",
        idempotency_key="setstate-interleave",
    )

    # turn 2 — a buying signal: must ADD buying_signal_seen while KEEPING the handoff
    second = await service.apply_turn_facts(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="run-2",
        signals=TurnSignals(intelligence=[{"buying_signals": ["wants to buy"]}]),
    )
    assert second is not None
    assert second.state["facts"]["buying_signal_seen"] is True
    # carried forward from turn 1 — NOT reset by the interleaved set_state row
    assert second.state["facts"].get("handoff_recorded") == "lead"


async def test_action_agent_mode_exposes_conversation_state_tool(agent: Agent) -> None:
    config = AgentConfig(
        workspace_id=agent.workspace_id,
        agent_id=agent.id,
        name=agent.name,
        agent_md="# Seller\nYou sell the workspace catalog.",
        trust_mode="autopilot",
        auto_send_threshold=0.85,
    )

    profile = RuntimeProfileCompiler().compile_agent(
        config=config,
        agent_kind="seller_agent",
        execution_mode="action",
    )

    assert "conversation.set_state" in profile.allowed_tool_names
