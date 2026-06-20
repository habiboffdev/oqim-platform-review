"""Turn-consumer registry (#424 S5).

One place to react to a committed turn. The dispatcher used to hand-wire the
reducer → CRM stage sync → promoter opt-out chain inline, with bespoke args per
consumer and one shared try/except. Here that fan-out is a registered list of
consumers walked by ``finalize_turn(ctx)``:

- ``TurnContext`` carries everything a consumer needs, gathered once at the seam.
- After slice 4/5 the only pre-commit consumer is the facts reducer; it persists
  the turn's ``{facts: {...}}`` snapshot row (no shared-context hand-off remains).
- Each consumer is independently non-fatal — a crash is logged and never aborts
  the turn or the other consumers (a registry guarantee, not copied try/except).

New turn reactions register here instead of editing the dispatcher hot path; the
CRM stage/note + promoter opt-out + deal_value now run post-commit in
``run_records_pass`` (the sole commercial recorder).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger("turn.finalize")


async def _walk_consumers(ctx: Any, consumers: list[tuple[str, Any]], *, phase: str) -> None:
    """Run a registry of ``(name, consumer)`` entries over ``ctx``. Each consumer is
    independently non-fatal — a failure is logged and never aborts the rest (the one
    isolation guarantee shared by both registry phases)."""
    for name, consume in consumers:
        try:
            await consume(ctx)
        except Exception:
            logger.exception("%s consumer %r failed (non-fatal)", phase, name)


@dataclass
class TurnContext:
    """Everything a committed-turn consumer needs, gathered once at the seam."""

    db: AsyncSession
    workspace_id: int
    conversation_id: int
    customer_id: int
    agent_id: int
    agent_session_id: int
    hermes_run_id: str
    committed_action_refs: list[str] = field(default_factory=list)
    handoff_kinds: list[str] = field(default_factory=list)
    intelligence_payloads: list[dict[str, Any]] = field(default_factory=list)
    reply_delivered: bool = False
    customer_texts: list[str] = field(default_factory=list)
    # NOTE: the forced records pass no longer rides on this context. It re-invokes
    # the engine and writes ``conversation.deal_value`` in its own session, which
    # would deadlock on the open dispatcher transaction's row locks; it now runs
    # POST-commit via ``run_records_pass`` (called from the dispatcher after
    # ``db.commit()``), sourcing its args directly from the dispatcher scope.


TurnConsumer = Callable[[TurnContext], Awaitable[None]]


async def _reduce_facts_consumer(ctx: TurnContext) -> None:
    """The sole pre-commit consumer: derive + persist this turn's ``{facts: {...}}``
    snapshot row (the seed-role / UI stage_label projection reads it). The return is
    no longer shared on ctx — downstream CRM/promoter reactions moved post-commit to
    ``run_records_pass``."""
    from app.modules.agent_conversation_state.reducer import TurnSignals
    from app.modules.agent_conversation_state.service import (
        AgentConversationStateService,
    )

    await AgentConversationStateService(ctx.db).apply_turn_facts(
        workspace_id=ctx.workspace_id,
        agent_session_id=ctx.agent_session_id,
        agent_id=ctx.agent_id,
        conversation_id=ctx.conversation_id,
        customer_id=ctx.customer_id,
        hermes_run_id=ctx.hermes_run_id,
        signals=TurnSignals(
            reply_delivered=ctx.reply_delivered,
            handoff_kinds=ctx.handoff_kinds,
            intelligence=ctx.intelligence_payloads,
            customer_texts=ctx.customer_texts,
        ),
    )


# A bounded process-local guard: a re-walked turn (retry / double-record) must not
# re-invoke the record engine pass for the same hermes_run_id. The deal_value WRITE
# is already idempotent (own session, only-if-changed), so this is purely a cheap
# LLM-call dedupe, not a correctness gate. Bounded so a long-lived worker process
# never grows it without limit.
#
# CONCURRENCY (PRD #433): the off-lease RecordsConsumer pool can drain several jobs
# at once, so ``_mark_recorded_once`` is now touched by multiple concurrent records
# passes. It is safe WITHOUT a lock ONLY because: (a) it contains no ``await`` — the
# asyncio scheduler cannot preempt it mid-statement, so the check-then-set and the
# bounded eviction never tear or interleave; and (b) the only effect a missed dedupe
# could have is a redundant records-pass LLM call, whose underlying writes are all
# idempotent (only-if-changed deal_value, monotonic stage, set-True opt-out). If any
# ``await`` is ever introduced into this function it MUST become a guarded section.
_RECORDED_RUN_IDS: dict[str, None] = {}
_RECORDED_RUN_IDS_MAX = 4096


def _mark_recorded_once(hermes_run_id: str) -> bool:
    """Record ``hermes_run_id`` as recorded; return True the FIRST time only.

    Must remain ``await``-free — see the concurrency note above; the off-lease
    consumer pool relies on this being a single un-yielding critical section."""
    if hermes_run_id in _RECORDED_RUN_IDS:
        return False
    if len(_RECORDED_RUN_IDS) >= _RECORDED_RUN_IDS_MAX:
        # Drop the oldest insertion (dict preserves insertion order) to stay bounded.
        _RECORDED_RUN_IDS.pop(next(iter(_RECORDED_RUN_IDS)), None)
    _RECORDED_RUN_IDS[hermes_run_id] = None
    return True


# The short inline instruction the forced record pass runs as its turn input.
# mode=ANY + the single conversation.record grant already force the call; this just
# tells the model WHAT to record from the transcript it is fed (so it records the
# price it actually quoted, not invents).
_RECORD_INSTRUCTION = (
    "Record this conversation's commercial state by calling conversation.record: "
    "the stage, the price you quoted the customer (deal_value), the items, who "
    "they are, whether a human must step in, and any owner-configured CRM "
    "custom_fields whose value the conversation states (see the CRM custom "
    "fields note). Record the price you actually told them, even if it is not "
    "an approved catalog price."
)


def _records_transcript(customer_text: str, reply_text: str) -> list[str]:
    """The turn transcript fed to the record pass: the customer's message and the
    reply just sent, so the model records the price it actually quoted."""
    lines: list[str] = []
    if customer_text.strip():
        lines.append(f"Customer said: {customer_text.strip()}")
    if reply_text.strip():
        lines.append(f"You (the seller) replied: {reply_text.strip()}")
    return lines


def _facts_from_record(record: dict) -> dict:
    """Synthesize the reducer's boolean facts from a conversation.record payload,
    so the existing target_role_for_facts / on_turn_facts path advances the CRM
    stage exactly as the old record_intelligence/work.handoff signals did."""
    from app.modules.agent_business_actions.service import HANDOFF_KINDS

    facts: dict = {"engaged": True}  # the records pass only runs after a delivered reply
    handoff = record.get("handoff") or {}
    kind = (handoff.get("kind") or "").strip()
    if handoff.get("needed") is True and kind in HANDOFF_KINDS:
        facts["handoff_recorded"] = kind
    customer = record.get("customer") or {}
    if str(customer.get("phone") or "").strip():
        facts["contact_captured"] = True
    stage = (record.get("stage") or "").strip()
    if record.get("buying_signals") or stage in {
        "qualified",
        "quoted",
        "negotiating",
        "won",
    }:
        facts["buying_signal_seen"] = True
    if record.get("opted_out") is True:
        facts["opted_out"] = True
    return facts


def _intel_from_record(record: dict) -> dict:
    """Synthesize the record_intelligence payload shape on_turn_facts expects for
    compose_lead_context_note (objections / owner_notes / next_best_action)."""
    summary = (record.get("summary") or "").strip()
    return {
        "lead_stage": (record.get("stage") or "").strip(),
        "buying_signals": list(record.get("buying_signals") or []),
        "objections": list(record.get("objections") or []),
        "owner_notes": [summary] if summary else [],
        "next_best_action": (record.get("next_best_action") or "").strip(),
        "opted_out": record.get("opted_out") is True,
    }


def _record_to_note_packet(record: dict) -> dict:
    """Adapt a conversation.record payload into the set_state-shaped packet the
    CRM note composers expect (selected_items / shown_prices), so the amoCRM
    card note shows Mahsulot + Narx lines for records-path turns."""
    items = list(record.get("items") or [])
    deal_value = record.get("deal_value")
    currency = (record.get("currency") or "UZS").strip() or "UZS"
    shown_prices = (
        [{"amount": deal_value, "currency": currency}]
        if deal_value not in (None, 0)
        else []
    )
    return {
        "selected_items": items,
        "shown_prices": shown_prices,
        "next_best_action": (record.get("next_best_action") or "").strip() or None,
    }


async def run_records_pass(  # noqa: C901 (one ordered post-commit fan-out — deal_value + handoff + stage/note + opt-out must synthesize from one record and commit together in one fresh session)
    *,
    db: AsyncSession | None = None,
    workspace_id: int,
    conversation_id: int,
    customer_id: int,
    agent_id: int,
    agent_session_id: int,
    hermes_run_id: str,
    agent_config: Any,
    agent_kind: str,
    hermes_session_id: str | None,
    session_db: Any,
    grounding: list[str],
    conversation_state: dict[str, Any],
    reply_delivered: bool,
    customer_text: str = "",
    reply_text: str = "",
    intelligence_payloads: list[dict[str, Any]] | None = None,
    handoff_kinds: list[str] | None = None,
) -> None:
    """Force the seller to RECORD its own commercial state (the price it quoted,
    items, stage, customer, handoff) by re-invoking the engine in a single forced
    ``record`` pass (mode=ANY pinned to conversation.record, fed the turn
    transcript), THEN read ``result.record_payload["deal_value"]`` and write
    ``conversation.deal_value`` DIRECTLY (Decimal) so the CRM worker pushes it.

    This MUST run POST-COMMIT, off the dispatcher's open turn transaction. The
    deal_value write opens its OWN ``async_session``; if the dispatcher transaction
    were still open it would hold the conversation row lock and this write would
    block on it, deadlocking until a timeout (deal_value stays null = "0 so'm" on
    the amoCRM card). Running after ``db.commit()`` releases that lock first.

    Runs OFF the customer-facing path (the reply is already sent). Gated purely on
    structured turn facts — an active CRM lead link and a delivered reply — never
    on the reply text or a commercial signal. Idempotent per ``hermes_run_id``.
    Non-fatal: every failure is logged, never raised, so a record-pass hiccup never
    disturbs the already-committed turn."""
    config = agent_config
    if config is None or not getattr(config, "commercial_finalization_enabled", True):
        return
    if not reply_delivered:
        return
    if hermes_session_id is None or session_db is None:
        return
    # (slice 3) NO has_signal gate. The interactive seller no longer emits
    # work.handoff / record_intelligence (those tools were removed), so
    # ``intelligence_payloads`` / ``handoff_kinds`` are always empty for an
    # interactive turn and an ungrounded handoff turn ("I'll connect you", no
    # catalog search) would be silently skipped. The pass now runs on every
    # reply-delivering CRM-linked turn — gated on the active lead link + the
    # delivered reply below, never on the signals or the reply text.
    # ``grounding`` / ``intelligence_payloads`` / ``handoff_kinds`` are still
    # accepted and used to build the transcript / synthetic facts — only the gate
    # on them is dropped.
    from app.modules.crm_connector.lead_links import active_lead_link, rearm_lead_link

    # The gate-read owns its session when the caller did not supply one — a
    # background records consumer (off-lease, PRD #433) runs this AFTER the
    # dispatcher's turn session is closed, so it can never borrow the dispatcher's
    # db. (Inline callers may still pass db; tests do.)
    if db is not None:
        link = await active_lead_link(
            db, workspace_id=workspace_id, conversation_id=conversation_id
        )
    else:
        from app.db.session import async_session

        async with async_session() as gate:
            link = await active_lead_link(
                gate, workspace_id=workspace_id, conversation_id=conversation_id
            )
    if link is None:
        return
    if not _mark_recorded_once(hermes_run_id):
        return

    routing = getattr(config, "crm_routing", None)
    record: dict[str, Any] | None = None
    try:
        from app.modules.agent_runtime_v2.hermes.engine import HermesEngineAdapter
        from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfileCompiler

        profile = RuntimeProfileCompiler().compile_profile(
            config=config,
            agent_kind=agent_kind,
            execution_mode="record",
        )
        records_grounding = list(grounding)
        if routing and routing.get("pipelines"):
            _keys = ", ".join(sorted(routing["pipelines"].keys()))
            records_grounding.append(
                "CRM pipeline routing: record `pipeline_key` as EXACTLY one of "
                f"[{_keys}] (omit if unsure). Owner guidance: "
                f"{routing.get('instructions') or '(none)'}"
            )
        fields_cfg = getattr(config, "crm_fields", None)
        if fields_cfg:
            from app.modules.crm_connector.field_ops import describe_writable_fields

            directive = describe_writable_fields(fields_cfg)
            if directive:
                records_grounding.append(directive)
        result = await HermesEngineAdapter().run(
            config=config,
            profile=profile,
            customer_message=_RECORD_INSTRUCTION,
            grounding=records_grounding,
            history=_records_transcript(customer_text, reply_text),
            conversation_id=conversation_id,
            hermes_run_id=hermes_run_id,
            agent_kind=agent_kind,
            hermes_session_id=hermes_session_id,
            session_db=session_db,
            agent_session_id=agent_session_id,
            conversation_state=dict(conversation_state or {}),
        )
        record = getattr(result, "record_payload", None)
        logger.info(
            "records pass ran conversation_id=%s hermes_run_id=%s has_record=%s",
            conversation_id,
            hermes_run_id,
            record is not None,
        )
    except Exception:
        logger.exception("records pass failed (non-fatal)")

    if not record:
        return

    # The record is the COMPLETE source of this turn's commercial state (slice 2): a
    # FRESH session (a new connection in prod, off the dispatcher's released locks)
    # writes deal_value AND fans out the handoff work-item, CRM stage/note, and
    # promoter opt-out — all synthesized from the record, all idempotent (monotonic
    # advance, dedup-by-kind handoff, set-True opt-out, only-if-changed value).
    # Post-slice-4 this is the SOLE commercial recorder (the CRM/promoter pre-commit
    # consumers were retired); the pre-commit registry only writes the facts
    # snapshot row now, so there is no double-write. Non-fatal.
    raw_value = record.get("deal_value")
    value: Decimal | None = None
    if raw_value not in (None, ""):
        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, ValueError, TypeError):
            value = None
        if value is not None and value <= 0:
            value = None

    try:
        from types import SimpleNamespace

        from app.db.session import async_session
        from app.models.conversation import Conversation
        from app.modules.agent_business_actions.service import (
            HANDOFF_KINDS,
            AgentBusinessActionService,
        )
        from app.modules.bi_promoter.fact_hooks import apply_promoter_turn_facts
        from app.modules.crm_connector.sync_service import CrmSyncService

        synth_facts = _facts_from_record(record)
        synth_intel = _intel_from_record(record)

        async with async_session() as fresh:
            # deal_value DIRECT write + re-arm so CrmSyncWorker pushes the quoted value.
            if value is not None:
                conv = await fresh.get(Conversation, conversation_id)
                if conv is not None and conv.deal_value != value:
                    conv.deal_value = value
                    fresh_link = await active_lead_link(
                        fresh, workspace_id=workspace_id, conversation_id=conversation_id
                    )
                    if fresh_link is not None:
                        rearm_lead_link(fresh_link)

            # (a) handoff work-item (owner task + notification), deduped by kind.
            handoff = record.get("handoff") or {}
            h_kind = (handoff.get("kind") or "").strip()
            if handoff.get("needed") is True and h_kind in HANDOFF_KINDS:
                cust = record.get("customer") or {}
                cust_name = str(cust.get("name") or "").strip() or None
                try:
                    await AgentBusinessActionService(fresh).handoff(
                        workspace_id=workspace_id,
                        agent_session_id=agent_session_id,
                        agent_id=agent_id,
                        conversation_id=conversation_id,
                        customer_id=customer_id,
                        hermes_run_id=hermes_run_id,
                        kind=h_kind,
                        title=f"{h_kind} lead: {cust_name}" if cust_name else f"{h_kind} lead",
                        detail=(handoff.get("reason") or "").strip()
                        or (record.get("summary") or "").strip()
                        or "Records pass handoff",
                        customer_name=cust_name,
                        customer_phone=str(cust.get("phone") or "").strip() or None,
                        idempotency_key=f"records-handoff:{hermes_run_id}:{h_kind}",
                    )
                except Exception:
                    logger.exception("records pass handoff work-item failed (non-fatal)")

            # (a.5) pipeline routing (route-once): resolve the recorded LOGICAL key
            # -> target pipeline id via the owner's map, then re-home the lead once.
            pkey = (record.get("pipeline_key") or "").strip()
            if routing and not pkey:
                pkey = (routing.get("default") or "").strip()  # owner default fallback
            if routing and pkey:
                target_id = (routing.get("pipelines") or {}).get(pkey)
                if target_id:
                    try:
                        status = await CrmSyncService(fresh).route_lead(
                            workspace_id=workspace_id,
                            conversation_id=conversation_id,
                            target_pipeline_id=str(target_id),
                        )
                        if status == "vanished":
                            from app.modules.crm_connector.owner_cards import (
                                queue_crm_owner_notification,
                            )
                            await queue_crm_owner_notification(
                                fresh,
                                workspace_id=workspace_id,
                                title="amoCRM voronka topilmadi",
                                summary="Sotuvchi tanlagan voronka amoCRM sxemasida yo'q.",
                                recommended_action="'oqim crm route' sozlamasini va amoCRM voronkalarini tekshiring.",
                                idempotency_key=f"crm_route_vanished:{workspace_id}:{pkey}",
                            )
                    except Exception:
                        logger.exception("records pass routing failed (non-fatal)")

            # (a.6) S4 custom-field + tag writes (worker-drained, opt-in). Resolve
            # logical keys -> provider ids + coerce; queue ops the worker drains.
            # Log what the agent emitted + what resolved so a live test is
            # self-diagnosing (emitted-nothing vs emitted-but-dropped).
            from app.modules.crm_connector.field_ops import (
                resolve_field_ops,
                resolve_tag_ops,
            )

            emitted = record.get("custom_fields") or []
            field_ops = resolve_field_ops(getattr(config, "crm_fields", None) or {}, emitted)
            tag_ops = resolve_tag_ops(
                getattr(config, "crm_tags", None) or {}, record.get("tags") or []
            )
            logger.info(
                "records pass fields conversation_id=%s resolved_field_ops=%d "
                "resolved_tag_ops=%d emitted=%s",
                conversation_id,
                len(field_ops),
                len(tag_ops),
                repr(emitted)[:500],
            )
            ops = field_ops + tag_ops
            if ops:
                try:
                    await CrmSyncService(fresh).queue_field_ops(
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        ops=ops,
                    )
                except Exception:
                    logger.exception("records pass field-op queue failed (non-fatal)")

            # (b) CRM stage advance + pending-task + context note (synthetic facts + intel).
            try:
                await CrmSyncService(fresh).on_turn_facts(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    facts=synth_facts,
                    intelligence=[synth_intel],
                    state_packet=_record_to_note_packet(record),
                )
            except Exception:
                logger.exception("records pass crm stage/note failed (non-fatal)")

            # (c) promoter opt-out latch.
            if record.get("opted_out") is True:
                try:
                    await apply_promoter_turn_facts(
                        fresh,
                        SimpleNamespace(state={"facts": {"opted_out": True}}),
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        customer_id=customer_id,
                    )
                except Exception:
                    logger.exception("records pass opt-out latch failed (non-fatal)")

                # (c.1) S4 DNC outbound: when a do-not-contact field is mapped,
                # queue the write so the worker pushes it to the CRM card (the
                # bidirectional partner of the inbound DNC read).
                dnc = getattr(config, "crm_dnc", None)
                if dnc and dnc.get("field_id"):
                    try:
                        await CrmSyncService(fresh).queue_field_ops(
                            workspace_id=workspace_id,
                            conversation_id=conversation_id,
                            ops=[{
                                "kind": "dnc",
                                "entity": dnc.get("entity") or "contact",
                                "field_id": str(dnc["field_id"]),
                                "value": dnc.get("on_value", True),
                            }],
                        )
                    except Exception:
                        logger.exception("records pass DNC outbound queue failed (non-fatal)")

            # ONE commit for deal_value + handoff + stage + opt-out together.
            await fresh.commit()
    except Exception:
        logger.exception("records pass fan-out write failed (non-fatal)")


# The ordered consumer registry. It runs INSIDE the open dispatcher transaction
# (before its commit), so it must contain ONLY same-session DB writes. After
# slice 4 that is just the facts reducer: it writes the ``{facts: {...}}`` state
# snapshot that CRM role-seeding (``_seed_role_for_conversation``) and the UI
# stage_label projection read. The CRM stage sync + promoter opt-out pre-commit
# consumers were retired — the post-commit records pass (``run_records_pass``) is
# now the sole CRM/promoter writer (it advances the stage, creates the handoff
# work-item + note, and latches opt-out from the recorded payload), so the
# duplicate pre-commit writes are gone (closing the double-write drain-window).
# The forced records pass is NOT here: it re-invokes the engine and writes
# deal_value in its OWN session, which would deadlock on this transaction's row
# locks — it runs POST-commit via run_records_pass.
TURN_CONSUMERS: list[tuple[str, TurnConsumer]] = [
    ("reduce_facts", _reduce_facts_consumer),
]


async def finalize_turn(
    ctx: TurnContext,
    *,
    consumers: list[tuple[str, TurnConsumer]] | None = None,
) -> None:
    """Walk the post-commit turn-consumer registry. Each consumer is independently
    non-fatal — a failure is logged and never aborts the turn or later consumers."""
    await _walk_consumers(
        ctx, consumers if consumers is not None else TURN_CONSUMERS, phase="turn"
    )


# --- inbound phase: hooks that run on every customer message, BEFORE the reply
# gate (lead capture + outreach reply-retire must never depend on the agent
# replying). Same registry machinery, an inbound-shaped context. (#425 S6) -------


@dataclass
class InboundContext:
    """What an inbound-message consumer needs, gathered once. These consumers run on
    every customer message before the reply-lifecycle gate, so they carry the live
    ORM objects (not just ids) the hooks already expect."""

    db: AsyncSession
    workspace: Any
    conversation: Any
    customer: Any


InboundConsumer = Callable[[InboundContext], Awaitable[None]]


async def _crm_lead_capture_consumer(ctx: InboundContext) -> None:
    """First-contact CRM lead capture (idempotent; no-op without an active CRM)."""
    from app.modules.crm_connector.sync_service import CrmSyncService

    await CrmSyncService(ctx.db).ensure_lead_link(
        workspace=ctx.workspace, conversation=ctx.conversation, customer=ctx.customer
    )


async def _promoter_reply_retire_consumer(ctx: InboundContext) -> None:
    """A customer reply retires matching outreach targets (promoter reply loop)."""
    from app.modules.bi_promoter.reply_hook import mark_outreach_replied

    await mark_outreach_replied(
        ctx.db,
        workspace_id=ctx.workspace.id,
        conversation_id=ctx.conversation.id,
        phone=ctx.customer.phone_number,
    )


INBOUND_CONSUMERS: list[tuple[str, InboundConsumer]] = [
    ("crm_lead_capture", _crm_lead_capture_consumer),
    ("promoter_reply_retire", _promoter_reply_retire_consumer),
]


async def on_inbound_message(
    ctx: InboundContext,
    *,
    consumers: list[tuple[str, InboundConsumer]] | None = None,
) -> None:
    """Walk the inbound-message consumer registry (runs before the reply gate).
    Each consumer is independently non-fatal."""
    await _walk_consumers(
        ctx, consumers if consumers is not None else INBOUND_CONSUMERS, phase="inbound"
    )
