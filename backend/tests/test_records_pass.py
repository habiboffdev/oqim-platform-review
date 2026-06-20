"""Records pass (deal_value written from the stated record), POST-COMMIT.

The post-reply step that forces the seller to RECORD its own commercial state by
re-invoking the engine in a single forced ``record`` pass (mode=ANY pinned to
``conversation.record``), THEN reads ``result.record_payload["deal_value"]`` and
writes ``conversation.deal_value`` DIRECTLY (Decimal), re-arming the CRM lead link
so the worker pushes it.

It runs POST-commit (off the dispatcher's open turn transaction) — the deal_value
write opens its OWN fresh session and would deadlock on the dispatcher transaction's
row locks if run inside it. So it is a plain async function, NOT a pre-commit
TURN_CONSUMERS entry.

These tests mock the engine entrypoint (HermesEngineAdapter.run) so no LLM is
called; the mock returns a ``ReplyResult`` carrying ``record_payload``. They pin:
the structured gate (active_lead_link + reply_delivered + a commercial signal —
never text detection), the record profile (execution_mode == "record"), idempotency
on hermes_run_id, and the direct deal_value write effect.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.db.base import utc_now
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.reply_runtime import ReplyResult
from app.modules.agent_runtime_v2.turn_consumers import run_records_pass

pytestmark = pytest.mark.asyncio

_ENGINE_RUN = "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run"


def _cfg(workspace_id: int, **overrides) -> AgentConfig:
    base = dict(
        agent_id=4,
        workspace_id=workspace_id,
        name="Sotuvchi",
        trust_mode="disabled",
        auto_send_threshold=0.85,
        agent_md="# Sotuvchi",
        agent_kind="seller",
    )
    base.update(overrides)
    return AgentConfig(**base)


async def _active_crm_link(db_session, workspace, conversation, customer) -> CrmLeadLink:
    conn = CrmConnection(
        workspace_id=workspace.id,
        provider="amocrm",
        status="active",
        provider_account_ref="biz",
        webhook_token="tok",
        pipeline_config={},
    )
    db_session.add(conn)
    await db_session.flush()
    link = CrmLeadLink(
        workspace_id=workspace.id,
        connection_id=conn.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        desired_stage_role="qualified",
        stage_authority="oqim",
        sync_state="pending",
        attempts=0,
        next_attempt_at=utc_now(),
        pending_notes=[],
    )
    db_session.add(link)
    await db_session.flush()
    return link


def _args(workspace, conversation, customer, **overrides) -> dict:
    base = dict(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=4,
        agent_session_id=5,
        # unique per call so the process-local idempotency guard never bleeds across
        # tests (the idempotency test reuses ONE id twice on purpose).
        hermes_run_id=f"run-record-{uuid.uuid4().hex}",
        reply_delivered=True,
        agent_config=_cfg(workspace.id),
        agent_kind="seller",
        hermes_session_id="oqim:agent-session:5",
        session_db=object(),
        grounding=["Mahsulot X narxi: 250000 so'm"],
        conversation_state={"stage": "qualified"},
    )
    base.update(overrides)
    return base


# --- (Task 1) pure helpers: synthesize reducer facts + intelligence from a record


def test_facts_from_record_maps_signals():
    from app.modules.agent_runtime_v2.turn_consumers import _facts_from_record

    f = _facts_from_record(
        {
            "stage": "quoted",
            "buying_signals": ["asked price"],
            "customer": {"phone": "+998901635207"},
            "handoff": {"needed": True, "kind": "lead", "reason": "shared phone"},
            "opted_out": False,
        }
    )
    assert f["handoff_recorded"] == "lead"
    assert f["contact_captured"] is True
    assert f["buying_signal_seen"] is True
    assert f["engaged"] is True
    assert "opted_out" not in f  # only present when True


def test_facts_from_record_unsupported_handoff_kind_dropped():
    from app.modules.agent_runtime_v2.turn_consumers import _facts_from_record

    f = _facts_from_record(
        {"stage": "new", "handoff": {"needed": True, "kind": "bogus"}}
    )
    assert "handoff_recorded" not in f


def test_facts_from_record_refund_is_a_recognized_handoff_kind():
    """refund is in the conversation.record handoff.kind enum, so a refund handoff
    must be honored by the HANDOFF_KINDS gate (it was missing -> a refund handoff
    silently recorded nothing and created no work-item)."""
    from app.modules.agent_runtime_v2.turn_consumers import _facts_from_record

    f = _facts_from_record(
        {"stage": "negotiating", "handoff": {"needed": True, "kind": "refund"}}
    )
    assert f["handoff_recorded"] == "refund"


def test_handoff_kinds_covers_every_record_schema_kind():
    """Guard the schema<->gate drift that hid the refund bug: every handoff.kind the
    conversation.record tool accepts must be honored by HANDOFF_KINDS."""
    from app.modules.agent_business_actions.service import HANDOFF_KINDS

    record_schema_kinds = {"lead", "support", "complaint", "refund", "human_requested"}
    assert record_schema_kinds <= HANDOFF_KINDS


def test_intel_from_record_builds_note_payload():
    from app.modules.agent_runtime_v2.turn_consumers import _intel_from_record

    i = _intel_from_record(
        {
            "stage": "quoted",
            "buying_signals": ["price"],
            "objections": ["too pricey"],
            "summary": "dev moving to HR",
            "next_best_action": "call back",
            "opted_out": True,
        }
    )
    assert i["objections"] == ["too pricey"]
    assert i["owner_notes"] == ["dev moving to HR"]
    assert i["next_best_action"] == "call back"
    assert i["opted_out"] is True


# --- the load-bearing effect: deal_value written DIRECTLY from record_payload ----


async def test_records_pass_writes_deal_value_from_record_payload(
    db_session, workspace, conversation, customer, agent
):
    """The forced record pass returns a ReplyResult whose record_payload carries a
    stated deal_value; the driver writes it to ``conversation.deal_value`` directly
    (no seeded snapshot, no list parse) in a FRESH session and re-arms the link."""
    from app.models.conversation import Conversation
    from app.modules.agent_sessions.service import AgentSessionService

    session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )
    await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()

    run_mock = AsyncMock(
        return_value=ReplyResult(
            reply_text="",
            confidence=0.0,
            grounding_hits=0,
            record_payload={"stage": "quoted", "deal_value": 9790000, "currency": "UZS"},
        )
    )

    # The post-commit deal_value write opens a FRESH ``async_session()`` (a new
    # connection in prod). The savepoint-rollback test DB never commits to a second
    # connection, so we bind that fresh session to the SAME test connection — this
    # still exercises the real direct-write path, only its connection is the test's.
    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(
            db=db_session,
            **_args(workspace, conversation, customer, agent_session_id=session.id),
        )

    run_mock.assert_awaited_once()
    profile = run_mock.await_args.kwargs["profile"]
    assert profile.execution_mode == "record"
    refreshed = await db_session.get(Conversation, conversation.id)
    await db_session.refresh(refreshed)
    assert refreshed.deal_value == Decimal("9790000")


async def test_records_pass_owns_its_session_when_db_absent(
    db_session, workspace, conversation, customer, agent
):
    """The records pass must run with NO caller-supplied db (a background consumer
    runs it after the turn's session is closed): it opens its own session for the
    active_lead_link gate-read. Passing db=None must reach the engine and write."""
    from app.models.conversation import Conversation
    from app.modules.agent_sessions.service import AgentSessionService

    session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )
    await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()

    run_mock = AsyncMock(
        return_value=ReplyResult(
            reply_text="",
            confidence=0.0,
            grounding_hits=0,
            record_payload={"stage": "quoted", "deal_value": 9790000, "currency": "UZS"},
        )
    )

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    # db is NOT passed; the gate-read must open its own (patched) session.
    args = _args(workspace, conversation, customer, agent_session_id=session.id)
    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(**args)

    run_mock.assert_awaited_once()
    refreshed = await db_session.get(Conversation, conversation.id)
    await db_session.refresh(refreshed)
    assert refreshed.deal_value == Decimal("9790000")


async def test_records_pass_feeds_transcript_to_engine(
    db_session, workspace, conversation, customer
):
    """The pass is fed the turn transcript (customer text + the reply just sent) so
    the model records the price it actually quoted."""
    await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()

    run_mock = AsyncMock(
        return_value=ReplyResult(reply_text="", confidence=0.0, grounding_hits=0)
    )
    with patch(_ENGINE_RUN, run_mock):
        await run_records_pass(
            db=db_session,
            **_args(
                workspace,
                conversation,
                customer,
                customer_text="Narxi qancha?",
                reply_text="HR kursi narxi 9 790 000 so'm.",
            ),
        )

    run_mock.assert_awaited_once()
    history = run_mock.await_args.kwargs["history"]
    assert any("Narxi qancha?" in line for line in history)
    assert any("9 790 000" in line for line in history)


# --- guard: model recorded no price -> deal_value unchanged, no crash ---------


async def test_records_pass_none_record_payload_leaves_deal_value(
    db_session, workspace, conversation, customer
):
    """``record_payload=None`` (the model recorded nothing) leaves deal_value
    unchanged and never crashes."""
    from app.models.conversation import Conversation

    await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()

    run_mock = AsyncMock(
        return_value=ReplyResult(reply_text="", confidence=0.0, grounding_hits=0)
    )

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(
            db=db_session, **_args(workspace, conversation, customer)
        )

    run_mock.assert_awaited_once()
    refreshed = await db_session.get(Conversation, conversation.id)
    await db_session.refresh(refreshed)
    assert refreshed.deal_value is None


async def test_records_pass_zero_deal_value_leaves_unchanged(
    db_session, workspace, conversation, customer
):
    """A non-positive stated deal_value is not written (guards against a 0/blank)."""
    from app.models.conversation import Conversation

    await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()

    run_mock = AsyncMock(
        return_value=ReplyResult(
            reply_text="",
            confidence=0.0,
            grounding_hits=0,
            record_payload={"stage": "new", "deal_value": 0},
        )
    )

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(
            db=db_session, **_args(workspace, conversation, customer)
        )

    run_mock.assert_awaited_once()
    refreshed = await db_session.get(Conversation, conversation.id)
    await db_session.refresh(refreshed)
    assert refreshed.deal_value is None


# --- (Task 2) fan-out: handoff work-item + CRM stage/note + opt-out -----------


async def test_records_pass_creates_handoff_workitem(
    db_session, workspace, conversation, customer, agent
):
    """A record with ``handoff.needed`` fans out a handoff work-item (owner task +
    notification) via AgentBusinessActionService.handoff, synthesized from the
    record_payload — NOT from a Phase-1 work.handoff tool call."""
    from sqlalchemy import select

    from app.models.commercial_action import CommercialActionProposalRecord
    from app.modules.agent_runtime_v2.reply_runtime import ReplyResult
    from app.modules.agent_runtime_v2.turn_consumers import run_records_pass
    from app.modules.agent_sessions.service import AgentSessionService

    session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )
    link = await _active_crm_link(db_session, workspace, conversation, customer)
    link.provider_lead_id = "555"  # so on_turn_facts can advance/note
    await db_session.commit()

    run_mock = AsyncMock(
        return_value=ReplyResult(
            reply_text="",
            confidence=0.0,
            grounding_hits=0,
            record_payload={
                "stage": "qualified",
                "deal_value": 9790000,
                "currency": "UZS",
                "customer": {"name": "Mirzosharif", "phone": "+998901635207"},
                "handoff": {"needed": True, "kind": "lead", "reason": "shared phone"},
            },
        )
    )

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(
            db=db_session,
            **_args(
                workspace,
                conversation,
                customer,
                agent_id=agent.id,
                agent_session_id=session.id,
            ),
        )

    proposals = (
        await db_session.execute(
            select(CommercialActionProposalRecord).where(
                CommercialActionProposalRecord.conversation_id == conversation.id
            )
        )
    ).scalars().all()
    # handoff() writes a create_task + a notify_owner proposal
    assert any(p.action_type == "create_business_task" for p in proposals)


async def test_records_pass_advances_crm_stage_and_optout(
    db_session, workspace, conversation, customer, agent
):
    """A record carrying a buying signal advances the CRM stage (qualified buying-
    signal -> negotiation role) and an ``opted_out`` record latches the promoter
    opt-out on Customer.opted_out — both synthesized from the record_payload."""
    from app.modules.agent_runtime_v2.reply_runtime import ReplyResult
    from app.modules.agent_runtime_v2.turn_consumers import run_records_pass
    from app.modules.agent_sessions.service import AgentSessionService

    session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )
    link = await _active_crm_link(db_session, workspace, conversation, customer)
    link.desired_stage_role = "new"  # start below negotiation so the advance shows
    link.provider_lead_id = "555"
    await db_session.commit()

    run_mock = AsyncMock(
        return_value=ReplyResult(
            reply_text="",
            confidence=0.0,
            grounding_hits=0,
            record_payload={
                "stage": "qualified",
                "buying_signals": ["asked price"],
                "objections": ["pricey"],
                "summary": "dev -> HR",
                "handoff": {"needed": False, "kind": "", "reason": ""},
                "opted_out": True,
            },
        )
    )

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(
            db=db_session,
            **_args(
                workspace,
                conversation,
                customer,
                agent_id=agent.id,
                agent_session_id=session.id,
            ),
        )

    await db_session.refresh(link)
    assert link.desired_stage_role == "negotiation"  # qualified buying-signal -> negotiation role
    await db_session.refresh(customer)
    assert customer.opted_out is True


# --- (slice 5) note re-route: records pass feeds items/price to the card note --


async def test_records_pass_note_has_items_and_price(
    db_session, workspace, conversation, customer, agent
):
    """The records pass adapts its record_payload into the set_state-shaped packet
    the CRM note composers expect (items -> selected_items, deal_value+currency ->
    shown_prices), so the amoCRM card note shows Mahsulot + Narx lines for
    records-path turns (the slice-5 bugfix; previously blank)."""
    from app.modules.agent_runtime_v2.reply_runtime import ReplyResult
    from app.modules.agent_runtime_v2.turn_consumers import run_records_pass
    from app.modules.agent_sessions.service import AgentSessionService

    session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )
    link = await _active_crm_link(db_session, workspace, conversation, customer)
    link.provider_lead_id = "555"
    await db_session.commit()

    run_mock = AsyncMock(
        return_value=ReplyResult(
            reply_text="",
            confidence=0.0,
            grounding_hits=0,
            record_payload={
                "stage": "qualified",
                "deal_value": 9790000,
                "currency": "UZS",
                "items": [{"name": "HR kursi", "quantity": 1}],
                "handoff": {"needed": True, "kind": "lead", "reason": "shared phone"},
            },
        )
    )

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(
            db=db_session,
            **_args(
                workspace,
                conversation,
                customer,
                agent_id=agent.id,
                agent_session_id=session.id,
            ),
        )

    await db_session.refresh(link)
    note_blob = " ".join(str(n.get("text", "")) for n in (link.pending_notes or []))
    assert "HR kursi" in note_blob  # Mahsulot line from record.items
    assert "9 790 000" in note_blob  # Narx line from record.deal_value


# --- (slice 3 re-gate) runs on every reply-delivering CRM-linked turn ---------


async def test_records_pass_runs_with_empty_signals_when_link_and_reply(
    db_session, workspace, conversation, customer, agent
):
    """Post-slice-3 the interactive seller no longer emits work.handoff /
    record_intelligence, so ``intelligence_payloads`` / ``handoff_kinds`` are
    always empty and an ungrounded handoff turn carries no signals. The pass must
    still run (gate is reply_delivered + active_lead_link only) so a pure handoff
    turn is recorded — the records pass is the sole commercial-state recorder."""
    from app.modules.agent_runtime_v2.reply_runtime import ReplyResult
    from app.modules.agent_runtime_v2.turn_consumers import run_records_pass
    from app.modules.agent_sessions.service import AgentSessionService

    session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )
    await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()

    run_mock = AsyncMock(
        return_value=ReplyResult(
            reply_text="",
            confidence=0.0,
            grounding_hits=0,
            record_payload={"stage": "qualified", "deal_value": 250000, "currency": "UZS"},
        )
    )

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        # grounding=[], intelligence_payloads=[], handoff_kinds=[] (post-slice-3 reality)
        await run_records_pass(
            db=db_session,
            **_args(
                workspace,
                conversation,
                customer,
                agent_id=agent.id,
                agent_session_id=session.id,
                grounding=[],
                intelligence_payloads=[],
                handoff_kinds=[],
            ),
        )

    run_mock.assert_awaited_once()  # ran despite empty signals
    refreshed = await db_session.get(type(conversation), conversation.id)
    await db_session.refresh(refreshed)
    assert refreshed.deal_value == Decimal("250000")


# --- (b) no active lead link -> early return, no engine call -----------------


async def test_records_pass_no_op_without_active_lead_link(
    db_session, workspace, conversation, customer
):
    # No CRM connection / link created at all.
    await db_session.commit()
    run_mock = AsyncMock(return_value=None)
    with patch(_ENGINE_RUN, run_mock):
        await run_records_pass(db=db_session, **_args(workspace, conversation, customer))
    run_mock.assert_not_awaited()


# --- (c) reply not delivered -> early return ---------------------------------


async def test_records_pass_no_op_when_reply_not_delivered(
    db_session, workspace, conversation, customer
):
    await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()
    run_mock = AsyncMock(return_value=None)
    with patch(_ENGINE_RUN, run_mock):
        await run_records_pass(
            db=db_session,
            **_args(workspace, conversation, customer, reply_delivered=False),
        )
    run_mock.assert_not_awaited()


# NOTE (slice 3): the old "no commercial signal -> no record pass" gate was
# removed. The interactive seller no longer emits work.handoff /
# record_intelligence, so signals are always empty for interactive turns; gating
# on them would skip a pure ungrounded handoff turn. The pass now runs on every
# reply-delivering CRM-linked turn (covered by
# test_records_pass_runs_with_empty_signals_when_link_and_reply above). The gate
# is STILL never text — it is the active CRM link + the delivered reply.


# --- (d) idempotent on hermes_run_id -----------------------------------------


async def test_records_pass_is_idempotent_per_hermes_run_id(
    db_session, workspace, conversation, customer
):
    await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()
    args = _args(workspace, conversation, customer, hermes_run_id="run-record-idem-xyz")
    run_mock = AsyncMock(
        return_value=ReplyResult(reply_text="", confidence=0.0, grounding_hits=0)
    )
    with patch(_ENGINE_RUN, run_mock):
        await run_records_pass(db=db_session, **args)
        await run_records_pass(db=db_session, **args)
    run_mock.assert_awaited_once()


# --- kill switch: per-agent channel_config flag (default ON) ------------------


async def test_records_pass_respects_disabled_kill_switch(
    db_session, workspace, conversation, customer
):
    """A per-agent disable flag early-returns even with the gate satisfied."""
    await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()
    run_mock = AsyncMock(return_value=None)
    with patch(_ENGINE_RUN, run_mock):
        await run_records_pass(
            db=db_session,
            **_args(
                workspace,
                conversation,
                customer,
                agent_config=_cfg(workspace.id, commercial_finalization_enabled=False),
            ),
        )
    run_mock.assert_not_awaited()


# --- it is NOT a pre-commit consumer (would deadlock) -------------------------


async def test_records_pass_is_not_a_pre_commit_consumer():
    """The forced record pass must NOT run inside the open dispatcher transaction
    (its own-session deal_value write deadlocks on this turn's row locks). Pin that
    it is not in the pre-commit registry."""
    from app.modules.agent_runtime_v2.turn_consumers import TURN_CONSUMERS

    assert "records_pass" not in [name for name, _ in TURN_CONSUMERS]
    assert "record_commercial_state" not in [name for name, _ in TURN_CONSUMERS]


# --- (S3) pipeline routing: records pass re-homes the lead from pipeline_key -------


async def _routing_conn_link(db_session, workspace, conversation, customer, *, default="111"):
    """An active connection with a 2-pipeline nested snapshot + a lead link pinned to
    the default pipeline (111) — the starting point for a route-once move."""
    conn = CrmConnection(
        workspace_id=workspace.id, provider="amocrm", status="active",
        provider_account_ref="biz", webhook_token="tok",
        pipeline_config={
            "schema_version": 2,
            "snapshot": {"pipelines": [{"id": "111"}, {"id": "222"}]},
            "mapping": {"default_pipeline_id": default, "pipelines": {}},
        })
    db_session.add(conn)
    await db_session.flush()
    link = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id, conversation_id=conversation.id,
        customer_id=customer.id, pipeline_id="111", desired_stage_role="new",
        stage_authority="oqim", sync_state="synced", attempts=0,
        next_attempt_at=utc_now(), pending_notes=[])
    db_session.add(link)
    await db_session.flush()
    return link


async def test_records_pass_routes_lead_from_pipeline_key(
    db_session, workspace, conversation, customer, agent
):
    """A record carrying pipeline_key='consulting' re-homes the lead from default 111
    to the mapped target 222."""
    link = await _routing_conn_link(db_session, workspace, conversation, customer)
    await db_session.commit()

    run_mock = AsyncMock(return_value=ReplyResult(
        reply_text="", confidence=0.0, grounding_hits=0,
        record_payload={"stage": "qualified", "pipeline_key": "consulting"}))

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    config = _cfg(workspace.id, crm_routing={
        "pipelines": {"sales": "111", "consulting": "222"},
        "default": "sales", "instructions": "consulting -> consulting"})
    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(db=db_session, **_args(
            workspace, conversation, customer, agent_config=config))

    await db_session.refresh(link)
    assert link.pipeline_id == "222"
    # the routing keys reach the records agent's grounding
    grounding_arg = run_mock.await_args.kwargs["grounding"]
    assert any("pipeline_key" in g and "consulting" in g for g in grounding_arg)


async def test_records_pass_no_routing_config_leaves_pipeline(
    db_session, workspace, conversation, customer, agent
):
    """No crm_routing on the config -> pipeline_key is ignored, lead stays on default."""
    link = await _routing_conn_link(db_session, workspace, conversation, customer)
    await db_session.commit()

    run_mock = AsyncMock(return_value=ReplyResult(
        reply_text="", confidence=0.0, grounding_hits=0,
        record_payload={"stage": "qualified", "pipeline_key": "consulting"}))

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    config = _cfg(workspace.id)  # crm_routing defaults to None
    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(db=db_session, **_args(
            workspace, conversation, customer, agent_config=config))

    await db_session.refresh(link)
    assert link.pipeline_id == "111"


async def test_records_pass_routes_via_default_when_no_pipeline_key(
    db_session, workspace, conversation, customer, agent
):
    """#13: with NO pipeline_key recorded, the owner routing.default key is used."""
    link = await _routing_conn_link(db_session, workspace, conversation, customer)
    await db_session.commit()
    run_mock = AsyncMock(return_value=ReplyResult(
        reply_text="", confidence=0.0, grounding_hits=0,
        record_payload={"stage": "qualified"}))  # no pipeline_key
    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session
    config = _cfg(workspace.id, crm_routing={
        "pipelines": {"sales": "111", "consulting": "222"},
        "default": "consulting", "instructions": "x"})
    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(db=db_session, **_args(
            workspace, conversation, customer, agent_config=config))
    await db_session.refresh(link)
    assert link.pipeline_id == "222"  # routed to the default key's pipeline


# --- (S4 Task 5) custom-field + tag writes: resolve logical keys -> queued ops ----


async def test_records_pass_queues_field_and_tag_ops(
    db_session, workspace, conversation, customer, agent
):
    """A record carrying ``custom_fields``/``tags`` is resolved (logical key ->
    field_id + coerced value; tag key -> namespaced name) and the ops are queued on
    ``link.pending_field_ops`` for the worker to drain."""
    link = await _active_crm_link(db_session, workspace, conversation, customer)
    link.provider_lead_id = "555"
    await db_session.commit()

    run_mock = AsyncMock(return_value=ReplyResult(
        reply_text="", confidence=0.0, grounding_hits=0,
        record_payload={
            "stage": "qualified",
            "custom_fields": [{"key": "budget", "value": "5000000"}],
            "tags": ["vip"],
            "opted_out": False,
        }))

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    config = _cfg(
        workspace.id,
        crm_fields={"budget": {"field_id": 600123, "type": "numeric", "write": True}},
        crm_tags={"vocabulary": ["vip"], "namespace": "oqim:"},
    )
    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(db=db_session, **_args(
            workspace, conversation, customer, agent_config=config))

    await db_session.refresh(link)
    assert {"kind": "custom_field", "entity": "lead", "field_id": "600123", "value": 5000000, "type": "numeric"} in link.pending_field_ops
    assert {"kind": "tag", "name": "oqim:vip"} in link.pending_field_ops


async def test_records_pass_field_ops_drops_unblessed_and_unwritable(
    db_session, workspace, conversation, customer, agent
):
    """``write:false`` and unconfigured keys (and tags outside the vocabulary) are
    NEVER queued (the opt-in gate) — and a record stating no fields queues nothing."""
    link = await _active_crm_link(db_session, workspace, conversation, customer)
    link.provider_lead_id = "555"
    await db_session.commit()

    run_mock = AsyncMock(return_value=ReplyResult(
        reply_text="", confidence=0.0, grounding_hits=0,
        record_payload={
            "stage": "qualified",
            "custom_fields": [
                {"key": "secret", "value": "1"},   # write:false -> dropped
                {"key": "unknown", "value": "x"},  # unconfigured -> dropped
            ],
            "tags": ["spam"],                       # not in vocabulary -> dropped
        }))

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    config = _cfg(
        workspace.id,
        crm_fields={"secret": {"field_id": 600999, "type": "numeric", "write": False}},
        crm_tags={"vocabulary": ["vip"], "namespace": "oqim:"},
    )
    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(db=db_session, **_args(
            workspace, conversation, customer, agent_config=config))

    await db_session.refresh(link)
    assert link.pending_field_ops == []


async def test_records_pass_no_custom_fields_queues_nothing(
    db_session, workspace, conversation, customer, agent
):
    """Eval (spec §4.7): a transcript that states no field info records no
    ``custom_fields`` -> nothing queued (the fields are filled only from stated
    values, never fabricated)."""
    link = await _active_crm_link(db_session, workspace, conversation, customer)
    link.provider_lead_id = "555"
    await db_session.commit()

    run_mock = AsyncMock(return_value=ReplyResult(
        reply_text="", confidence=0.0, grounding_hits=0,
        record_payload={"stage": "qualified"}))  # no custom_fields, no tags

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    config = _cfg(
        workspace.id,
        crm_fields={"budget": {"field_id": 600123, "type": "numeric", "write": True}},
        crm_tags={"vocabulary": ["vip"], "namespace": "oqim:"},
    )
    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(db=db_session, **_args(
            workspace, conversation, customer, agent_config=config))

    await db_session.refresh(link)
    assert link.pending_field_ops == []


async def test_records_pass_grounds_writable_field_menu(
    db_session, workspace, conversation, customer, agent
):
    """The records-agent grounding carries the writable field-menu guidance line so
    the model knows which logical keys it MAY fill."""
    await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()

    run_mock = AsyncMock(return_value=ReplyResult(
        reply_text="", confidence=0.0, grounding_hits=0,
        record_payload={"stage": "qualified"}))

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    config = _cfg(
        workspace.id,
        crm_fields={
            "budget": {"field_id": 600123, "type": "numeric", "write": True},
            "secret": {"field_id": 600999, "type": "numeric", "write": False},
        },
    )
    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(db=db_session, **_args(
            workspace, conversation, customer, agent_config=config))

    grounding_arg = run_mock.await_args.kwargs["grounding"]
    menu_lines = [g for g in grounding_arg if "custom_fields" in g]
    assert menu_lines, "expected a custom-field menu grounding line"
    assert "budget" in menu_lines[0]
    assert "secret" not in menu_lines[0]  # write:false excluded from the menu


# --- (S4 Task 10) DNC outbound: opt-out queues the mapped do-not-contact write ---


async def test_records_pass_optout_queues_dnc_field_op(
    db_session, workspace, conversation, customer, agent
):
    """When the record opts the customer out AND a ``crm_dnc`` field is mapped, the
    pass queues the mapped do-not-contact field write on ``link.pending_field_ops``
    (drained by the worker), bidirectional with the inbound DNC read."""
    link = await _active_crm_link(db_session, workspace, conversation, customer)
    link.provider_lead_id = "555"
    await db_session.commit()

    run_mock = AsyncMock(return_value=ReplyResult(
        reply_text="", confidence=0.0, grounding_hits=0,
        record_payload={"stage": "qualified", "opted_out": True}))

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    config = _cfg(
        workspace.id,
        crm_dnc={"field_id": "600126", "on_value": True, "label": "Bog'lanmaslik"},
    )
    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(db=db_session, **_args(
            workspace, conversation, customer, agent_config=config))

    await db_session.refresh(link)
    assert {"kind": "dnc", "entity": "contact", "field_id": "600126", "value": True} in link.pending_field_ops


async def test_records_pass_no_dnc_config_queues_no_field_op_on_optout(
    db_session, workspace, conversation, customer, agent
):
    """An opt-out with NO ``crm_dnc`` mapping queues no field op (opt-in gate)."""
    link = await _active_crm_link(db_session, workspace, conversation, customer)
    link.provider_lead_id = "555"
    await db_session.commit()

    run_mock = AsyncMock(return_value=ReplyResult(
        reply_text="", confidence=0.0, grounding_hits=0,
        record_payload={"stage": "qualified", "opted_out": True}))

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    config = _cfg(workspace.id)  # crm_dnc defaults to None
    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        await run_records_pass(db=db_session, **_args(
            workspace, conversation, customer, agent_config=config))

    await db_session.refresh(link)
    assert link.pending_field_ops == []


# --- (S4c Task 2) the enumerated field directive reaches the engine grounding -


async def test_records_pass_injects_field_directive_into_grounding(
    db_session, workspace, conversation, customer
):
    """When the agent has writable CRM fields, the records pass hands the engine
    an enumerated custom_fields directive (label/type/enum options inline)."""
    await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()

    cfg = _cfg(
        workspace.id,
        crm_fields={
            "budjet": {"field_id": "740937", "type": "text", "write": True,
                       "label": "Budjet", "entity": "contact"},
            "manba": {"field_id": "740941", "type": "select", "write": True,
                      "label": "Manba", "entity": "contact",
                      "enum_map": {"Instagram": 1308885, "Telegram": 1308887}},
        },
    )
    # record_payload=None -> the engine is still called WITH the grounding, then
    # run_records_pass returns early (no fresh-session block needed here).
    run_mock = AsyncMock(
        return_value=ReplyResult(reply_text="", confidence=0.0, grounding_hits=0)
    )
    with patch(_ENGINE_RUN, run_mock):
        await run_records_pass(
            db=db_session, **_args(workspace, conversation, customer, agent_config=cfg)
        )

    grounding = "\n".join(run_mock.await_args.kwargs["grounding"])
    assert "custom_fields" in grounding
    assert "budjet" in grounding and "manba" in grounding
    assert "Instagram" in grounding and "Telegram" in grounding


# --- (S4c Task 3) emit/resolve observability + the queue path ----------------


async def test_records_pass_queues_field_ops_and_logs_emit(
    db_session, workspace, conversation, customer, caplog
):
    """A record carrying custom_fields resolves to queued pending_field_ops AND
    logs what the agent emitted + how many ops resolved (self-diagnosing)."""
    link = await _active_crm_link(db_session, workspace, conversation, customer)
    await db_session.commit()

    cfg = _cfg(
        workspace.id,
        crm_fields={
            "budjet": {"field_id": "740937", "type": "text", "write": True,
                       "label": "Budjet", "entity": "contact"},
        },
    )
    record = {"stage": "qualified",
              "custom_fields": [{"key": "Budjet", "value": "5 mln"}]}  # capitalized key
    run_mock = AsyncMock(
        return_value=ReplyResult(reply_text="", confidence=0.0, grounding_hits=0,
                                 record_payload=record)
    )

    @contextlib.asynccontextmanager
    async def _fresh():
        yield db_session

    with patch(_ENGINE_RUN, run_mock), patch("app.db.session.async_session", _fresh):
        with caplog.at_level(logging.INFO):
            await run_records_pass(
                db=db_session, **_args(workspace, conversation, customer, agent_config=cfg)
            )

    await db_session.refresh(link)
    assert link.pending_field_ops
    assert link.pending_field_ops[0]["field_id"] == "740937"
    assert "resolved_field_ops=1" in caplog.text
    assert "Budjet" in caplog.text  # the emitted key is logged
