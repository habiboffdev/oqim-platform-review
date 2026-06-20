from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_turn_session import ConversationTurnSession
from app.models.customer import Customer
from app.models.message import Message
from app.modules.agent_control.contracts import AgentControlActionInput
from app.modules.agent_control.service import AgentControlService
from app.modules.agent_runtime_v2.hermes.session_store import OqimHermesSessionDB
from app.modules.agent_runtime_v2.prompt_text import (
    message_prompt_text as _message_prompt_text,
)
from app.modules.agent_runtime_v2.reply_runtime import SendAction
from app.modules.agent_runtime_v2.runtime_service import AgentRuntimeService
from app.modules.agent_runtime_v2.trace import (
    agent_runtime_trace_session,
    get_trace_events,
    summarize_trace_metrics,
)
from app.modules.agent_sessions.service import AgentSessionService
from app.modules.agent_talking.contracts import (
    TalkAction,
    TalkActionKind,
    TalkBundle,
    TalkingPolicy,
)
from app.modules.agent_talking.presence import TalkPresenceService
from app.modules.agent_talking.service import TalkBundleService
from app.modules.commercial_spine.contracts import utc_now as spine_utc_now
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.conversation_turns.lifecycle import TurnLifecycle
from app.modules.conversation_turns.service import ConversationTurnSessionService
from app.modules.hermes_runtime.contracts import (
    HermesRunEventInput,
    HermesRunEventKind,
    HermesRunInput,
    HermesRunLane,
    HermesRunMode,
    HermesRunPatch,
    HermesRunState,
)
from app.modules.hermes_runtime.service import HermesRunService

logger = logging.getLogger(__name__)

_REPLY_TYPING_FIRST_DELAY_S = 2.5  # humans read before they type
_REPLY_TYPING_INTERVAL_S = 4.0


async def _reply_typing_heartbeat(
    presence: TalkPresenceService, *, workspace_id: int, chat_id: str
) -> None:
    """Pulse Telegram 'typing' while the agent thinks, until cancelled.

    Runs in the async dispatcher (NOT the threaded Hermes loop). Uses the
    DB-free TalkPresenceService; self-heals over transient sidecar errors.
    """
    try:
        await asyncio.sleep(_REPLY_TYPING_FIRST_DELAY_S)
        while True:
            try:
                await presence.pulse(
                    workspace_id=workspace_id,
                    chat_id=chat_id,
                    online=True,
                    read=False,
                    typing=True,
                )
            except Exception as exc:  # best-effort presence; never kill the heartbeat
                logger.debug("reply typing pulse failed: %s", exc)
            await asyncio.sleep(_REPLY_TYPING_INTERVAL_S)
    except asyncio.CancelledError:
        return


async def _dnc_silent_owner_card(
    db: AsyncSession, *, workspace_id: int, conversation_id: int
) -> None:
    """DNC chokepoint: card the owner once per conversation per day when a
    do-not-contact (Bog'lanmaslik) lead writes and the seller stays silent. Uses
    the SAME idempotency key as the inbound gate (_handle_customer_message) so the
    two paths dedupe to a single card. Non-fatal — the silence is the guarantee;
    a card failure must never surface to the customer or crash the turn."""
    from app.modules.crm_connector.owner_cards import queue_crm_owner_notification

    day = spine_utc_now().strftime("%Y%m%d")
    try:
        await queue_crm_owner_notification(
            db,
            workspace_id=workspace_id,
            title="Bog'lanmaslik mijoz yozdi",
            summary=(
                "Bu mijozda 'Bog'lanmaslik' (do-not-contact) belgisi bor, shuning "
                "uchun agent javob bermadi."
            ),
            recommended_action="Suhbatni o'zingiz davom ettiring yoki belgini oling.",
            idempotency_key=f"dnc-silent:{workspace_id}:{conversation_id}:{day}",
            conversation_id=conversation_id,
        )
    except Exception:
        logger.warning(
            "dnc-silent owner card (dispatch gate) failed (non-fatal)", exc_info=True
        )


async def dispatch_agent_turn(
    *,
    db: AsyncSession,
    workspace_id: int,
    telegram_chat_id: int | None,
    customer: Customer,
    conversation: Conversation,
    message: Message,
    turn_session: ConversationTurnSession,
    burst_messages: list[Message] | None = None,
    media_type: str | None = None,
    trigger_telemetry: dict | None = None,
    delivery: Any | None = None,
) -> bool:
    # telegram_chat_id is used below as the chat_id fallback
    _ = media_type
    agent_id = int(turn_session.agent_id or 0)
    if agent_id <= 0:
        return False

    agent = await db.get(Agent, agent_id)
    if agent is None or int(agent.workspace_id) != int(workspace_id):
        return False

    if getattr(customer, "opted_out", False):
        # DNC / Bog'lanmaslik: do not generate a reply, regardless of which inbound
        # path enqueued this turn. The inbound gate (_handle_customer_message) only
        # covers the real-time persist path; catch-up recovery + channel sync bypass
        # it (prod conv 3 / run 185: the seller replied to a do-not-contact lead).
        # This is the single chokepoint every enqueued turn funnels through, so the
        # gate belongs here. Card the owner once/day and return False -> the runner
        # completes the turn (dispatch_skipped) with no reply and no run created.
        await _dnc_silent_owner_card(
            db, workspace_id=workspace_id, conversation_id=conversation.id
        )
        return False

    if _is_customer_reply_agent(agent) and not _autopilot_enabled(agent):
        # Two trust states only: 'autopilot' (run + send) or 'disabled' (fully off,
        # the default for a fresh account). A disabled customer-facing agent does
        # NOT run the LLM, create a reply run, or send/draft anything. We gate here
        # at the single chokepoint every enqueued turn funnels through (the inbound
        # gate misses catch-up recovery + channel sync) and BEFORE run creation so a
        # disabled agent costs zero LLM. Return False -> the runner completes the
        # turn via dispatch_skipped with no run and no customer-visible reply.
        return False

    agent_session = await AgentSessionService(db).get_or_create(
        workspace_id=workspace_id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent_id,
        channel=conversation.channel or message.channel or "telegram_dm",
    )
    hermes_session_db = await OqimHermesSessionDB.load(
        db,
        workspace_id=workspace_id,
        agent_session_id=agent_session.id,
    )
    run_service = HermesRunService(db)
    turn_service = ConversationTurnSessionService(db)
    turn_revision = int(turn_session.turn_revision or 1)
    generation = int(turn_session.generation or 1)
    run = await run_service.start_or_dedupe(
        HermesRunInput(
            workspace_id=workspace_id,
            agent_id=agent_id,
            agent_kind=str(agent.agent_type or "custom_agent"),
            lane=HermesRunLane.FAST_INTERACTIVE,
            run_mode=HermesRunMode.REPLY,
            trigger_type="conversation_turn",
            trigger_id=f"turn:{turn_session.id}:rev:{turn_revision}:gen:{generation}",
            event_id=f"turn:{turn_session.id}:rev:{turn_revision}",
            conversation_id=conversation.id,
            customer_id=customer.id,
            correlation_id=(
                f"agent-turn:{workspace_id}:{conversation.id}:"
                f"{turn_session.id}:{turn_revision}:{generation}"
            ),
            source_refs=[
                f"message:{message.id}",
                f"turn:{turn_session.id}",
                f"conversation:{conversation.id}",
                f"agent_session:{agent_session.id}",
            ],
            input_summary=_burst_prompt_text(burst_messages, message)[:2000],
            details={
                "trigger_telemetry": _clean_telemetry(trigger_telemetry),
                "turn_session": {
                    "turn_session_id": turn_session.id,
                    "turn_revision": turn_revision,
                    "generation": generation,
                },
            },
        )
    )
    if run.deduped:
        return True

    started = time.perf_counter()
    await TurnLifecycle(db).begin_run(turn_session, run.run_id, agent_id=agent_id)
    from app.modules.agent_runtime_v2.media_perception import stage_turn_media

    current_turn_media = await stage_turn_media(
        burst_messages or [message],
        workspace_id=workspace_id,
        chat_id=telegram_chat_id,
        channel=conversation.channel or message.channel,
    )
    # Messages whose bytes we attached natively render as a modality marker
    # (not the transcript) so the model perceives the audio/image, not text.
    # customer_query_text keeps the transcript for catalog grounding.
    staged_media_ids = {
        int(part.message_ref.split("message:", 1)[-1])
        for part in current_turn_media
        if str(part.message_ref).startswith("message:")
    }
    live_media_text = _burst_prompt_text(
        burst_messages, message, native_media_ids=staged_media_ids, bare=True
    )
    runtime = AgentRuntimeService(db)
    context = await runtime.gather_turn_context(
        workspace_id=workspace_id,
        agent_id=agent_id,
        customer_message=_burst_prompt_text(
            burst_messages, message, native_media_ids=staged_media_ids
        ),
        customer_query_text=_burst_prompt_text(burst_messages, message),
        conversation_id=conversation.id,
        hermes_run_id=run.run_id,
        reply_to_message_ref=f"message:{message.id}",
        turn_session_id=turn_session.id,
        turn_revision_start=turn_revision,
        agent_session_id=agent_session.id,
        hermes_session_id=agent_session.hermes_session_id,
        session_db=hermes_session_db,
        current_turn_media=current_turn_media,
        live_media_text=live_media_text,
    )
    await run_service.record_event(
        HermesRunEventInput(
            run_id=run.run_id,
            workspace_id=workspace_id,
            kind=HermesRunEventKind.CONTEXT_GATHERED,
            visibility="internal",
            # Counts/refs only: the FULL runtime_context_packet payload lives
            # once, in hermes_runs.details at completion — one authoritative
            # record per turn, not two copies of the same dump.
            payload={
                "grounding_lines": len(context.gathered.grounding),
                "history_lines": len(context.gathered.history),
                "voice_examples": len(context.gathered.voice_examples),
                "authority_warnings": len(context.gathered.authority_warnings),
                "runtime_context_packet_available": (
                    context.runtime_context_packet is not None
                ),
            },
            correlation_id=run.correlation_id,
            idempotency_key=f"{run.idempotency_key}:context_gathered",
        )
    )
    await db.commit()

    chat_id = (
        conversation.external_chat_id
        or (str(conversation.telegram_chat_id) if conversation.telegram_chat_id is not None else "")
        or (str(telegram_chat_id) if telegram_chat_id is not None else "")
    )
    _settings = get_settings()
    typing_task: asyncio.Task | None = None
    _channel = (conversation.channel or "telegram_dm").strip().lower()
    if _channel == "dm":
        _channel = "telegram_dm"
    if chat_id and _channel == "telegram_dm":
        _presence = TalkPresenceService(
            sidecar_url=_settings.sidecar_url,
            sidecar_api_key=_settings.sidecar_api_key,
        )
        typing_task = asyncio.create_task(
            _reply_typing_heartbeat(_presence, workspace_id=workspace_id, chat_id=chat_id),
            name=f"reply-typing:{workspace_id}:{chat_id}",
        )

    try:
        with agent_runtime_trace_session():
            outcome = await runtime.run_from_context(context)
            trace_events = get_trace_events()
        await hermes_session_db.flush()
        trace_metrics = summarize_trace_metrics(trace_events)
    except Exception as exc:
        await hermes_session_db.flush()
        await run_service.fail(
            run.run_id,
            error_code="agent_runtime_failed",
            error_message=str(exc)[:2000],
        )
        await db.commit()
        raise
    finally:
        if typing_task is not None:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

    turn_details = outcome.turn_details or {}
    finalization = await turn_service.finalize_run_observation(
        turn_session_id=turn_session.id,
        hermes_run_id=run.run_id,
        observed_revision=int(turn_details.get("observed_revision") or turn_revision),
        pending_steer_count=int(turn_details.get("pending_steer_count") or 0),
    )
    if not finalization.can_deliver:
        await run_service.patch(
            run.run_id,
            HermesRunPatch(
                state=HermesRunState.SKIPPED,
                completed_at=spine_utc_now(),
                output_action=SendAction.PROPOSE.value,
                confidence=outcome.confidence,
                details={
                    "generic_agent_runtime": _generic_runtime_payload(
                        outcome=outcome,
                        output_action=SendAction.PROPOSE.value,
                    ),
                    "agent_session": _agent_session_payload(agent_session),
                    "runtime_context_packet": _runtime_context_packet_payload(
                        context.runtime_context_packet
                    ),
                    "agent_action": _agent_action_payload(None, None),
                    "delivery": _delivery_payload(None),
                    "runtime_telemetry": outcome.telemetry or {},
                    "turn_finalization": _turn_finalization_payload(finalization),
                    "trace_events": trace_events,
                    "trace_metrics": trace_metrics,
                },
            ),
            event_kind=HermesRunEventKind.SKIPPED,
        )
        await db.commit()
        return True

    generic_actions = list(outcome.agent_actions or [])
    if generic_actions:
        recorded_actions = await _record_generic_agent_actions(
            db,
            workspace_id=workspace_id,
            agent_id=agent_id,
            run_id=run.run_id,
            correlation_id=run.correlation_id,
            actions=generic_actions,
        )
        output_ref = (
            recorded_actions[0]["action_id"] if recorded_actions else "agent_actions:none"
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        await AgentSessionService(db).append_event(
            agent_session_id=agent_session.id,
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            agent_id=agent_id,
            event_type="agent_action",
            direction="outbound",
            hermes_run_id=run.run_id,
            text="",
            payload={"agent_actions": recorded_actions},
            idempotency_key=f"{run.idempotency_key}:agent_actions",
        )
        await TurnLifecycle(db).complete_finalized(
            turn_session,
            run_id=run.run_id,
            run_patch=HermesRunPatch(
                completed_at=spine_utc_now(),
                total_latency_ms=elapsed_ms,
                llm_latency_ms=trace_metrics.get("llm_latency_ms"),
                llm_calls=trace_metrics.get("llm_calls"),
                tokens_in=trace_metrics.get("input_tokens"),
                tokens_out=trace_metrics.get("output_tokens"),
                total_tokens=trace_metrics.get("total_tokens"),
                confidence=outcome.confidence,
                warnings_count=0,
                tool_errors_count=outcome.tool_errors,
                output_action="agent_actions",
                output_ref=output_ref,
                details={
                    "generic_agent_runtime": _generic_runtime_payload(
                        outcome=outcome,
                        output_action="agent_actions",
                    ),
                    "agent_session": _agent_session_payload(agent_session),
                    "runtime_context_packet": _runtime_context_packet_payload(
                        context.runtime_context_packet
                    ),
                    "agent_action": (
                        recorded_actions[0]
                        if recorded_actions
                        else {"schema_version": "agent_action_ref.v1", "status": "none"}
                    ),
                    "agent_actions": recorded_actions,
                    "delivery": _delivery_payload(None),
                    "runtime_telemetry": outcome.telemetry or {},
                    "trace_events": trace_events,
                    "trace_metrics": trace_metrics,
                    "turn_finalization": _turn_finalization_payload(finalization),
                },
            ),
            finalized_revision=finalization.finalized_revision,
        )
        await db.commit()

        # Converged tail: this path returns early (no reply bubble — it commits
        # generic agent actions, e.g. a handoff), so the records pass MUST run
        # here too, not only on the normal talk-bundle tail below. Same gating
        # signals as the normal path; the committed actions are the delivery.
        from app.modules.agent_business_actions.service import (
            handoff_kinds_from_refs,
        )

        await _records_pass_post_commit(
            db=db,
            workspace_id=workspace_id,
            conversation=conversation,
            agent_session=agent_session,
            customer_id=customer.id,
            agent_id=agent_id,
            run=run,
            context=context,
            hermes_session_db=hermes_session_db,
            reply_delivered=True,
            customer_text=message.content or "",
            reply_text="",
            intelligence_payloads=list(outcome.intelligence_payloads or []),
            handoff_kinds=handoff_kinds_from_refs(
                outcome.committed_action_refs or []
            ),
        )
        return True

    bundle = outcome.talk_bundle or _plain_text_bundle(
        workspace_id=workspace_id,
        agent_id=agent_id,
        hermes_run_id=run.run_id,
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        text=outcome.reply_text,
    )
    bundle.confidence = outcome.confidence
    if not bundle.actions:
        if not _should_send_safe_ack(outcome):
            await run_service.patch(
                run.run_id,
                HermesRunPatch(
                    state=HermesRunState.SKIPPED,
                    completed_at=spine_utc_now(),
                    error_code="empty_agent_actions",
                    output_action=SendAction.PROPOSE.value,
                    confidence=outcome.confidence,
                    details={"trace_events": trace_events, "trace_metrics": trace_metrics},
                ),
                event_kind=HermesRunEventKind.SKIPPED,
            )
            await db.commit()
            return True
        # Never silently drop a run (#413): deliver one safe ack in the
        # agent's voice and raise an owner incident so a human closes the loop.
        # The ack rides the normal delivery path below, so in non-autopilot
        # trust modes it is only PROPOSED (not sent) — there the owner incident
        # is the real guarantee, and the human approves or replies themselves.
        bundle = _plain_text_bundle(
            workspace_id=workspace_id,
            agent_id=agent_id,
            hermes_run_id=run.run_id,
            conversation_id=conversation.id,
            trigger_message_id=message.id,
            text=_SAFE_ACK_TEXT,
        )
        bundle.confidence = outcome.confidence
        try:
            from app.modules.agent_business_actions.service import (
                AgentBusinessActionService,
            )

            # Dedupe the owner incident per conversation per hour: a sustained
            # provider outage must not flood the owner inbox with one incident
            # per customer message (notify_owner is idempotent on this key).
            incident_bucket = spine_utc_now().strftime("%Y%m%d%H")
            await AgentBusinessActionService(db).notify_owner(
                workspace_id=workspace_id,
                agent_session_id=agent_session.id,
                agent_id=agent_id,
                conversation_id=conversation.id,
                customer_id=agent_session.customer_id,
                hermes_run_id=run.run_id,
                title="Agent javob bera olmadi",
                summary=(
                    "Agent bu xabarga to'liq javob tuza olmadi; mijozga qisqa "
                    "kutish xabari yuborildi. Suhbatni o'zingiz davom ettiring."
                ),
                recommended_action="Mijozga o'zingiz javob yozing yoki qo'ng'iroq qiling.",
                idempotency_key=(
                    f"safe-ack-incident:{workspace_id}:{conversation.id}:{incident_bucket}"
                ),
            )
        except Exception as exc:
            # The customer ack must still go out even if the incident write
            # fails; the failure is loud in logs, not swallowed.
            logger.error(
                "safe_ack_incident_failed",
                exc_info=exc,
                extra={"run_id": run.run_id},
            )

    talk = TalkBundleService(db, delivery=delivery)
    agent_action = None
    proposal = None
    execution = None
    if _autopilot_enabled(agent):
        execution = await talk.execute_bundle(
            bundle=bundle,
            correlation_id=run.correlation_id,
        )
        agent_action = await talk.record_execution_action(
            bundle=bundle,
            result=execution,
            actor_ref="agent_runtime",
            correlation_id=run.correlation_id,
        )
        output_action = "auto_send"
        output_ref = execution.bundle_key
        await AgentSessionService(db).append_event(
            agent_session_id=agent_session.id,
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            agent_id=agent_id,
            event_type="agent_action",
            direction="outbound",
            hermes_run_id=run.run_id,
            message_id=_first_message_id(execution),
            text=execution.text_preview(),
            payload={
                "talk_bundle": bundle.model_dump(mode="json"),
                "talk_bundle_execution": execution.model_dump(mode="json"),
            },
            idempotency_key=f"{run.idempotency_key}:agent_action",
        )
    else:
        proposal = await talk.propose_bundle(
            bundle=bundle,
            reason="owner_approval_required",
        )
        output_action = "propose"
        output_ref = proposal.proposal_id

    # React to the committed turn via the pre-commit consumer registry, which after
    # slice 4/5 is just the facts reducer (it persists the turn's facts snapshot
    # row). CRM stage/note + promoter opt-out + deal_value now run POST-commit in
    # run_records_pass (the sole commercial recorder). The reducer is independently
    # non-fatal and never blocks the reply; new pre-commit reactions register in
    # turn_consumers.py instead of editing this hot path.
    from app.modules.agent_business_actions.service import handoff_kinds_from_refs
    from app.modules.agent_runtime_v2.turn_consumers import TurnContext, finalize_turn

    handoff_kinds = handoff_kinds_from_refs(outcome.committed_action_refs or [])
    intelligence_payloads = list(outcome.intelligence_payloads or [])
    reply_delivered = int(getattr(execution, "sent_count", 0) or 0) > 0
    await finalize_turn(
        TurnContext(
            db=db,
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            customer_id=customer.id,
            agent_id=agent_id,
            agent_session_id=agent_session.id,
            hermes_run_id=run.run_id,
            committed_action_refs=list(outcome.committed_action_refs or []),
            handoff_kinds=handoff_kinds,
            intelligence_payloads=intelligence_payloads,
            reply_delivered=reply_delivered,
            customer_texts=[message.content or ""],
        )
    )

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    await TurnLifecycle(db).complete_finalized(
        turn_session,
        run_id=run.run_id,
        run_patch=HermesRunPatch(
            completed_at=spine_utc_now(),
            total_latency_ms=elapsed_ms,
            llm_latency_ms=trace_metrics.get("llm_latency_ms"),
            llm_calls=trace_metrics.get("llm_calls"),
            tokens_in=trace_metrics.get("input_tokens"),
            tokens_out=trace_metrics.get("output_tokens"),
            total_tokens=trace_metrics.get("total_tokens"),
            confidence=outcome.confidence,
            warnings_count=len(bundle.warnings),
            tool_errors_count=outcome.tool_errors,
            output_action=output_action,
            output_ref=output_ref,
            details={
                "generic_agent_runtime": _generic_runtime_payload(
                    outcome=outcome,
                    output_action=output_action,
                ),
                "agent_session": _agent_session_payload(agent_session),
                "runtime_context_packet": _runtime_context_packet_payload(
                    context.runtime_context_packet
                ),
                "agent_action": _agent_action_payload(agent_action, proposal),
                "delivery": _delivery_payload(execution),
                "runtime_telemetry": outcome.telemetry or {},
                "trace_events": trace_events,
                "trace_metrics": trace_metrics,
                "turn_finalization": _turn_finalization_payload(finalization),
                "talk_bundle": bundle.model_dump(mode="json"),
            },
        ),
        finalized_revision=finalization.finalized_revision,
    )
    await db.commit()

    # POST-COMMIT: ENQUEUE the forced records pass onto the off-lease records queue
    # (a supervised consumer pool re-invokes the engine in the forced
    # conversation.record pass and writes the quoted deal_value; PRD #433). This runs
    # after the commit above and never blocks the dispatch/lease path — the enqueue
    # returns immediately, so a slow records-pass model degrades recording freshness
    # instead of holding this turn lease toward its TTL. The reply is already
    # delivered, so this is off BOTH the customer reply-latency path AND the lease.
    await _records_pass_post_commit(
        db=db,
        workspace_id=workspace_id,
        conversation=conversation,
        agent_session=agent_session,
        customer_id=customer.id,
        agent_id=agent_id,
        run=run,
        context=context,
        hermes_session_db=hermes_session_db,
        reply_delivered=reply_delivered,
        customer_text=message.content or "",
        reply_text=outcome.reply_text,
        intelligence_payloads=intelligence_payloads,
        handoff_kinds=handoff_kinds,
    )
    return True


async def _records_pass_post_commit(
    *,
    db: AsyncSession,
    workspace_id: int,
    conversation: Conversation,
    agent_session: Any,
    customer_id: int,
    agent_id: int,
    run: Any,
    context: Any,
    hermes_session_db: Any,
    reply_delivered: bool,
    customer_text: str,
    reply_text: str,
    intelligence_payloads: list[dict[str, Any]],
    handoff_kinds: list[str],
) -> None:
    """Converged tail: ENQUEUE a RecordsJob onto the off-lease records queue (a
    supervised consumer pool runs the forced ``record`` pass; PRD #433). Called from
    BOTH reply-delivering paths so recording never depends on which path the turn
    took, and never blocks the reply lease — the enqueue returns immediately so a
    slow records-pass model degrades recording freshness instead of holding the turn
    lease toward its TTL. Non-fatal: a failed enqueue is logged, never raised, so it
    cannot undo the committed turn."""
    try:
        from app.modules.agent_runtime_v2.records_queue import (
            RecordsJob,
            enqueue_records_job,
        )

        enqueue_records_job(
            RecordsJob(
                workspace_id=workspace_id,
                conversation_id=conversation.id,
                customer_id=customer_id,
                agent_id=agent_id,
                agent_session_id=agent_session.id,
                hermes_run_id=run.run_id,
                agent_config=context.config,
                agent_kind=context.gathered.agent_kind,
                hermes_session_id=agent_session.hermes_session_id,
                session_db=hermes_session_db,
                grounding=list(context.gathered.grounding or []),
                conversation_state=dict(context.conversation_state or {}),
                reply_delivered=reply_delivered,
                customer_text=customer_text,
                reply_text=reply_text,
                intelligence_payloads=list(intelligence_payloads or []),
                handoff_kinds=list(handoff_kinds or []),
            )
        )
    except Exception:
        logger.exception("records pass enqueue failed (non-fatal)")


# Customer-safe ack when a run produced nothing deliverable. Plain seller
# voice, no promise of an automated follow-up the system cannot make — the
# paired owner incident is what actually brings a human back.
_SAFE_ACK_TEXT = "Bir daqiqa, buni aniqlab keyin yozaman."


def _should_send_safe_ack(outcome: Any) -> bool:
    """An empty outcome gets a safe ack UNLESS it is the budget fail-fast
    (capped workspaces must stay silent, not spam acks on every message)."""
    reason = str(getattr(outcome, "reason", "") or "")
    return not reason.startswith("budget_exceeded")


def _plain_text_bundle(
    *,
    workspace_id: int,
    agent_id: int,
    hermes_run_id: str,
    conversation_id: int,
    trigger_message_id: int,
    text: str,
) -> TalkBundle:
    cleaned = (text or "").strip()
    actions: list[TalkAction] = []
    if cleaned:
        actions.append(
            TalkAction(
                kind=TalkActionKind.REPLY_TO_MSG,
                text=cleaned,
                target_message_ref=f"message:{trigger_message_id}",
                idempotency_key=f"{hermes_run_id}:bubble:0",
            )
        )
    return TalkBundle(
        workspace_id=workspace_id,
        agent_id=agent_id,
        hermes_run_id=hermes_run_id,
        trigger_ref=f"message:{trigger_message_id}",
        conversation_id=conversation_id,
        actions=actions,
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )


def _autopilot_enabled(agent: Agent) -> bool:
    return str(agent.trust_mode or "").strip().lower() == "autopilot"


# Agent kinds that reply to inbound CUSTOMER messages via the talk-bundle send
# path. These are the only kinds the disabled gate silences. Setup/owner/bi
# agents also reach dispatch_agent_turn (to emit owner approval proposals via the
# generic-actions path) and must keep running regardless of trust_mode — they
# never auto-send to a customer.
_CUSTOMER_REPLY_KINDS = {"customer", "seller", "support", "follow_up"}


def _is_customer_reply_agent(agent: Agent) -> bool:
    return str(agent.agent_type or "").strip().lower() in _CUSTOMER_REPLY_KINDS


def _burst_prompt_text(
    burst_messages: list[Message] | None,
    message: Message,
    *,
    native_media_ids: set[int] | None = None,
    bare: bool = False,
) -> str:
    """The model must see the WHOLE coalesced burst, in order. Messages whose
    media bytes are natively attached this turn (``native_media_ids``) render as
    a labeled transcript for the session, or a BARE marker when ``bare`` (the
    live Gemini call, where the transcript must not compete with the audio)."""
    ids = native_media_ids or set()

    def _render(item: Message) -> str:
        return _message_prompt_text(
            item, native_media=int(getattr(item, "id", 0) or 0) in ids, bare=bare
        )

    parts = [text for text in (_render(item) for item in (burst_messages or [])) if text]
    if not parts:
        return _render(message)
    return "\n".join(parts)


def _clean_telemetry(value: dict | None) -> dict:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        try:
            cleaned[key] = float(raw)
        except (TypeError, ValueError):
            continue
    return cleaned


def _first_message_id(execution: Any) -> int | None:
    for bubble in getattr(execution, "bubbles", []) or []:
        message_id = getattr(bubble, "message_id", None)
        if message_id is not None:
            return int(message_id)
    return None


def _turn_finalization_payload(finalization: Any) -> dict[str, Any]:
    return {
        "can_deliver": bool(finalization.can_deliver),
        "needs_successor": bool(finalization.needs_successor),
        "turn_session_id": int(finalization.turn_session_id),
        "turn_revision": int(finalization.turn_revision),
        "observed_revision": int(finalization.observed_revision),
        "finalized_revision": finalization.finalized_revision,
        "latest_customer_message_id": int(finalization.latest_customer_message_id),
        "reason": finalization.reason,
    }


def _generic_runtime_payload(
    *,
    outcome: Any,
    output_action: str,
) -> dict[str, Any]:
    telemetry = dict(getattr(outcome, "telemetry", None) or {})
    return {
        "schema_version": "generic_agent_runtime_trace.v1",
        "entrypoint": "dispatch_agent_turn",
        "runtime_service": "AgentRuntimeService",
        "hermes_runner": "HermesEngineAdapter",
        "agent_action_mapper": "TalkBundleService",
        "profile_kind": telemetry.get("profile_kind"),
        "execution_mode": telemetry.get("execution_mode"),
        "profile_hash": telemetry.get("profile_hash"),
        "tools_exposed": list(telemetry.get("tools_exposed") or []),
        "output_action": output_action,
    }


def _agent_session_payload(agent_session: Any) -> dict[str, Any]:
    return {
        "schema_version": "agent_session_runtime_ref.v1",
        "agent_session_id": int(agent_session.id),
        "hermes_session_id": str(agent_session.hermes_session_id or ""),
        "event_count": int(agent_session.event_count or 0),
    }


def _runtime_context_packet_payload(packet: Any | None) -> dict[str, Any]:
    if packet is None:
        return {"schema_version": "runtime_context_packet_ref.v1", "available": False}
    # packet is now a plain telemetry dict built inline in gather_turn_context (#412).
    data = dict(packet or {})
    customer_turn = str(data.get("customer_turn_text") or "")
    customer_query = str(data.get("customer_query_text") or "")
    static_context = dict(data.get("static_context") or {})
    dynamic_context = dict(data.get("dynamic_context") or {})
    transcript_hit_count = dynamic_context.get("transcript_hit_count")
    if transcript_hit_count is None:
        transcript_hit_count = len(data.get("transcript_hits") or [])
    return {
        "schema_version": "runtime_context_packet_ref.v1",
        "available": True,
        "workspace_id": data.get("workspace_id"),
        "agent_id": data.get("agent_id"),
        "agent_session_id": data.get("agent_session_id"),
        "hermes_session_id": data.get("hermes_session_id"),
        "customer_turn_chars": len(customer_turn),
        "customer_query_chars": len(customer_query),
        "session_summary_chars": len(str(data.get("session_summary") or "")),
        "transcript_hit_count": len(data.get("transcript_hits") or []),
        "authority_line_count": len(data.get("authority_lines") or []),
        "style_line_count": len(data.get("style_lines") or []),
        "policy_warning_count": len(data.get("policy_warnings") or []),
        "agent_material_refs": list(data.get("agent_material_refs") or []),
        "tool_grants": list(data.get("tool_grants") or []),
        "cache_keys": list(data.get("cache_keys") or []),
        "static_context": {
            "schema_version": static_context.get(
                "schema_version", "agent_static_context_ref.v1"
            ),
            "cache_key": static_context.get("cache_key"),
            "material_hash": static_context.get("material_hash"),
            "cache_keys": list(static_context.get("cache_keys") or []),
            "invalidation_refs": list(static_context.get("invalidation_refs") or []),
            "cacheable": bool(static_context.get("cacheable", True)),
        },
        "dynamic_context": {
            "schema_version": dynamic_context.get(
                "schema_version", "agent_dynamic_turn_context_ref.v1"
            ),
            "customer_turn_chars": int(
                dynamic_context.get("customer_turn_chars") or len(customer_turn)
            ),
            "customer_query_chars": int(
                dynamic_context.get("customer_query_chars") or len(customer_query)
            ),
            "session_summary_chars": int(
                dynamic_context.get("session_summary_chars")
                or len(str(data.get("session_summary") or ""))
            ),
            "transcript_hit_count": int(transcript_hit_count or 0),
            "conversation_state_chars": int(
                dynamic_context.get("conversation_state_chars") or 0
            ),
            "authority_line_count": int(
                dynamic_context.get("authority_line_count")
                or len(data.get("authority_lines") or [])
            ),
            "style_line_count": int(
                dynamic_context.get("style_line_count")
                or len(data.get("style_lines") or [])
            ),
            "policy_warning_count": int(
                dynamic_context.get("policy_warning_count")
                or len(data.get("policy_warnings") or [])
            ),
            "estimated_bytes": int(dynamic_context.get("estimated_bytes") or 0),
            "estimated_tokens": int(
                dynamic_context.get("estimated_tokens")
                or max(1, int(dynamic_context.get("estimated_bytes") or 0) // 4)
            ),
            "full_history_rebuild": bool(dynamic_context.get("full_history_rebuild")),
        },
    }


def _agent_action_payload(agent_action: Any | None, proposal: Any | None) -> dict[str, Any]:
    if agent_action is not None:
        return {
            "schema_version": "agent_action_ref.v1",
            "action_id": str(agent_action.action_id),
            "proposal_id": str(agent_action.proposal_id),
            "action_kind": str(agent_action.action_kind),
            "status": str(agent_action.status),
            "policy_decision": str(agent_action.policy_decision),
            "target_ref": str(agent_action.target_ref),
        }
    if proposal is not None:
        return {
            "schema_version": "agent_action_ref.v1",
            "proposal_id": str(proposal.proposal_id),
            "action_kind": str(proposal.action_type),
            "status": str(proposal.lifecycle_state),
            "policy_decision": "approve",
            "target_ref": f"conversation:{proposal.conversation_id}",
        }
    return {"schema_version": "agent_action_ref.v1", "status": "none"}


def _delivery_payload(execution: Any | None) -> dict[str, Any]:
    if execution is None:
        return {"schema_version": "agent_delivery_ref.v1", "state": "not_executed"}
    bubbles = list(getattr(execution, "bubbles", []) or [])
    message_ids = [
        int(bubble.message_id)
        for bubble in bubbles
        if getattr(bubble, "message_id", None) is not None
    ]
    external_message_ids = [
        str(bubble.external_message_id)
        for bubble in bubbles
        if getattr(bubble, "external_message_id", None)
    ]
    return {
        "schema_version": "agent_delivery_ref.v1",
        "status": str(getattr(execution, "status", "") or ""),
        "state": str(getattr(execution, "delivery_state", "") or ""),
        "reason": str(getattr(execution, "reason", "") or ""),
        "bundle_key": str(getattr(execution, "bundle_key", "") or ""),
        "sent_count": int(getattr(execution, "sent_count", 0) or 0),
        "failed_count": int(getattr(execution, "failed_count", 0) or 0),
        "unknown_count": int(getattr(execution, "unknown_count", 0) or 0),
        "blocked_count": int(getattr(execution, "blocked_count", 0) or 0),
        "message_ids": message_ids,
        "external_message_ids": external_message_ids,
    }


async def _record_generic_agent_actions(
    db: AsyncSession,
    *,
    workspace_id: int,
    agent_id: int,
    run_id: str,
    correlation_id: str,
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    service = AgentControlService(CommercialSpineRepository(db))
    recorded: list[dict[str, Any]] = []
    for index, raw in enumerate(actions):
        payload = dict(raw or {})
        action = await service.create_action(
            AgentControlActionInput(
                workspace_id=workspace_id,
                user_id=str(payload.get("user_id") or f"workspace:{workspace_id}"),
                agent_id=agent_id,
                hermes_run_id=run_id,
                action_kind=str(payload["action_kind"]),  # type: ignore[arg-type]
                target_ref=str(payload.get("target_ref") or f"agent:{agent_id}"),
                proposed_payload=dict(payload.get("proposed_payload") or {}),
                risk_level=str(payload.get("risk_level") or "low"),  # type: ignore[arg-type]
                evidence_refs=list(payload.get("evidence_refs") or [f"agent_run:{run_id}"]),
                approval_required=bool(payload.get("approval_required", False)),
                correlation_id=correlation_id,
                idempotency_key=str(
                    payload.get("idempotency_key")
                    or f"{run_id}:agent_action:{index}"
                ),
            )
        )
        recorded.append(action.model_dump(mode="json"))
    return recorded
