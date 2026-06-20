from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial_action import CommercialActionProposalRecord
from app.modules.agent_control.contracts import (
    AgentControlAction,
    AgentControlActionInput,
)
from app.modules.agent_control.service import (
    AgentControlService,
)
from app.modules.agent_talking.contracts import (
    TalkAction,
    TalkActionKind,
    TalkBubbleExecutionResult,
    TalkBundle,
    TalkBundleExecutionResult,
)
from app.modules.channel_runtime.source import ChannelRuntimeCore
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.telegram_tools.runtime import TelegramToolRuntime

logger = logging.getLogger(__name__)


def _bundle_key(bundle: TalkBundle) -> str:
    return f"talk_bundle:{bundle.workspace_id}:{bundle.agent_id}:{bundle.hermes_run_id}"


class TalkBundleService:
    def __init__(self, session: AsyncSession, *, delivery=None, adapter=None, sleep=None) -> None:
        self._session = session
        self._delivery = delivery
        self._adapter = adapter
        self._sleep = sleep or asyncio.sleep

    async def propose_bundle(
        self,
        *,
        bundle: TalkBundle,
        reason: str,
    ) -> CommercialActionProposalRecord:
        key = _bundle_key(bundle)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        record = CommercialActionProposalRecord(
            proposal_id=f"talk_bundle:{digest}",
            workspace_id=bundle.workspace_id,
            conversation_id=bundle.conversation_id or 0,
            customer_id=0,
            action_type="send_reply_bundle",
            lifecycle_state="waiting_approval",
            execution_mode="proposal",
            risk_level="high",
            requires_approval=True,
            executor_runtime="talk_bundle_runtime",
            priority="normal",
            confidence=float(bundle.confidence or 0.0),
            reason_code=reason[:120],
            source_refs=[bundle.hermes_run_id],
            payload={"talk_bundle": bundle.model_dump(mode="json")},
            idempotency_key=key,
            correlation_id=bundle.hermes_run_id,
            trace_id=f"{bundle.hermes_run_id}:talk_bundle",
            raw_proposal={"reason": reason, "talk_bundle": bundle.model_dump(mode="json")},
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def execute_bundle(
        self,
        *,
        bundle: TalkBundle,
        correlation_id: str,
        action_record_id: int | None = None,
    ) -> TalkBundleExecutionResult:
        key = _bundle_key(bundle)
        if bundle.conversation_id is None:
            return TalkBundleExecutionResult(
                status="blocked",
                delivery_state="blocked",
                reason="missing_conversation_id",
                bundle_key=key,
                conversation_id=None,
            )
        delivery_plan = ChannelRuntimeCore().plan_talk_bundle_delivery(bundle)
        intents_by_action = {intent.action_index: intent for intent in delivery_plan.intents}
        runtime = TelegramToolRuntime(
            self._session,
            delivery=self._delivery,
            adapter=self._adapter,
        )
        bubbles: list[TalkBubbleExecutionResult] = []
        sendable_indexes = [
            idx
            for idx, action in enumerate(bundle.actions)
            if action.kind
            in {TalkActionKind.SEND_MSG, TalkActionKind.REPLY_TO_MSG, TalkActionKind.SEND_MEDIA}
        ]
        last_sendable_index = sendable_indexes[-1] if sendable_indexes else None
        for idx, action in enumerate(bundle.actions):
            intent = intents_by_action.get(idx)
            action_key = intent.client_idempotency_key if intent is not None else action.idempotency_key or f"{key}:{idx}"
            try:
                bubble = await self._dispatch_action(
                    idx=idx,
                    action=action,
                    intent=intent,
                    action_key=action_key,
                    bundle=bundle,
                    runtime=runtime,
                    correlation_id=correlation_id,
                    action_record_id=action_record_id,
                    last_sendable_index=last_sendable_index,
                )
            except Exception as exc:
                # Per-action isolation (#418): a single action that raises — a
                # cosmetic reaction 502, an unexpected delivery error — must
                # NEVER abort the whole bundle after an earlier bubble already
                # delivered. Mark THIS action failed, keep going. The failure is
                # loud in logs and visible in the bundle's failed_count.
                logger.exception(
                    "talk bundle action failed: bundle=%s action_index=%s kind=%s",
                    key,
                    idx,
                    action.kind,
                )
                bubble = TalkBubbleExecutionResult(
                    action_index=idx,
                    action_kind=action.kind,
                    status="failed",
                    delivery_state="failed",
                    text_preview=_preview(action.text),
                    reply_to_message_ref=action.target_message_ref,
                    idempotency_key=action_key,
                    reason_code="action_execution_error",
                    error=str(exc),
                )
            bubbles.append(bubble)
            delay_after_ms = intent.delay_after_ms if intent is not None else 0
            if delay_after_ms > 0 and idx < len(bundle.actions) - 1:
                sleep_result = self._sleep(delay_after_ms / 1000.0)
                if inspect.isawaitable(sleep_result):
                    await sleep_result

        return _bundle_execution_result(bundle=bundle, key=key, bubbles=bubbles)

    async def _dispatch_action(
        self,
        *,
        idx: int,
        action: TalkAction,
        intent,
        action_key: str,
        bundle: TalkBundle,
        runtime: TelegramToolRuntime,
        correlation_id: str,
        action_record_id: int | None,
        last_sendable_index: int | None,
    ) -> TalkBubbleExecutionResult:
        if action.kind is TalkActionKind.SEND_REACTION:
            target_ref = action.target_message_ref or bundle.trigger_ref
            result = await runtime.send_reaction(
                workspace_id=bundle.workspace_id,
                agent_id=bundle.agent_id,
                conversation_id=bundle.conversation_id,
                reaction=action.reaction or "",
                correlation_id=correlation_id,
                action_record_id=action_record_id,
                idempotency_key=action_key,
                target_message_ref=target_ref,
            )
            recorded_action = action.model_copy(update={"target_message_ref": target_ref})
            return _bubble_result(
                idx=idx,
                action=recorded_action,
                key=action_key,
                result=result,
            )

        if action.kind not in {
            TalkActionKind.SEND_MSG,
            TalkActionKind.REPLY_TO_MSG,
            TalkActionKind.SEND_MEDIA,
        }:
            return TalkBubbleExecutionResult(
                action_index=idx,
                action_kind=action.kind,
                status="unsupported",
                delivery_state="unsupported",
                text_preview=_preview(action.text),
                reply_to_message_ref=action.target_message_ref,
                idempotency_key=action_key,
                reason_code=f"{action.kind.value}_execution_not_enabled",
            )

        typing_ms = max(0, int(intent.typing_ms if intent is not None else 0))
        delivery_delay_seconds = typing_ms / 1000.0
        typing_indicator = typing_ms > 0
        online_tail_seconds = 1.5 if idx == last_sendable_index else 0.0
        if action.kind is TalkActionKind.SEND_MEDIA:
            result = await runtime.send_media(
                workspace_id=bundle.workspace_id,
                agent_id=bundle.agent_id,
                conversation_id=bundle.conversation_id,
                media_ref=(intent.media_ref if intent is not None else action.media_ref)
                or "",
                caption=(intent.text if intent is not None else action.text),
                correlation_id=correlation_id,
                action_record_id=action_record_id,
                idempotency_key=action_key,
                reply_to_message_ref=(
                    intent.reply_to_message_ref
                    if intent is not None
                    else action.target_message_ref
                ),
                delivery_delay_seconds=delivery_delay_seconds,
                typing_indicator=typing_indicator,
                online_tail_seconds=online_tail_seconds,
            )
        else:
            result = await runtime.send_message(
                workspace_id=bundle.workspace_id,
                agent_id=bundle.agent_id,
                conversation_id=bundle.conversation_id,
                text=(intent.text if intent is not None else action.text) or "",
                correlation_id=correlation_id,
                action_record_id=action_record_id,
                idempotency_key=action_key,
                reply_to_message_ref=(
                    intent.reply_to_message_ref
                    if intent is not None
                    else (
                        action.target_message_ref
                        if action.kind is TalkActionKind.REPLY_TO_MSG
                        else None
                    )
                ),
                delivery_delay_seconds=delivery_delay_seconds,
                typing_indicator=typing_indicator,
                online_tail_seconds=online_tail_seconds,
            )
        return _bubble_result(idx=idx, action=action, key=action_key, result=result)

    async def record_execution_action(
        self,
        *,
        bundle: TalkBundle,
        result: TalkBundleExecutionResult,
        actor_ref: str,
        correlation_id: str,
    ) -> AgentControlAction:
        control = AgentControlService(CommercialSpineRepository(self._session))
        action = await control.create_action(
            AgentControlActionInput(
                workspace_id=bundle.workspace_id,
                user_id=f"workspace:{bundle.workspace_id}",
                agent_id=bundle.agent_id,
                hermes_run_id=bundle.hermes_run_id,
                action_kind="reply.send",
                target_ref=f"conversation:{bundle.conversation_id}",
                proposed_payload={
                    "talk_bundle": bundle.model_dump(mode="json"),
                    "talk_bundle_execution": result.model_dump(mode="json"),
                },
                risk_level="low",
                evidence_refs=[f"agent_run:{bundle.hermes_run_id}", *_source_trace_refs(bundle)],
                approval_required=False,
                correlation_id=correlation_id,
                idempotency_key=f"{result.bundle_key}:execution",
            )
        )
        await control.mark_executed(
            workspace_id=bundle.workspace_id,
            action_id=action.action_id,
            actor_ref=actor_ref,
            correlation_id=correlation_id,
            execution_payload=result.model_dump(mode="json"),
        )
        return action


def _preview(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text[:160] if text else None


def _source_trace_refs(bundle: TalkBundle) -> list[str]:
    refs: list[str] = []
    for item in bundle.source_trace:
        if not isinstance(item, dict):
            continue
        for key in ("source_ref", "ref", "message_ref", "item_id"):
            value = str(item.get(key) or "").strip()
            if value:
                refs.append(value)
                break
    return list(dict.fromkeys(refs))


def _bubble_result(
    *,
    idx: int,
    action: TalkAction,
    key: str,
    result,
) -> TalkBubbleExecutionResult:
    status = str(getattr(result, "status", "") or "")
    delivery_state = str(getattr(result, "delivery_state", "") or "")
    reason_code = getattr(result, "reason_code", None)
    if status == "executed" and delivery_state == "confirmed":
        bubble_status = "executed"
        bubble_delivery = "confirmed"
    elif status == "replayed":
        bubble_status = "replayed"
        bubble_delivery = "replayed"
    elif delivery_state == "unknown" or (
        reason_code == "delivery_not_confirmed" and delivery_state != "failed"
    ):
        bubble_status = "unknown"
        bubble_delivery = "unknown"
    elif status == "blocked":
        bubble_status = "blocked"
        bubble_delivery = "blocked"
    else:
        bubble_status = "failed"
        bubble_delivery = "failed"
    payload = getattr(result, "payload", {}) or {}
    return TalkBubbleExecutionResult(
        action_index=idx,
        action_kind=action.kind,
        status=bubble_status,
        delivery_state=bubble_delivery,
        text_preview=_preview(action.text),
        message_id=getattr(result, "message_id", None),
        external_message_id=getattr(result, "external_message_id", None),
        reply_to_message_ref=action.target_message_ref,
        idempotency_key=key,
        reason_code=reason_code,
        error=payload.get("error"),
    )


def _bundle_execution_result(
    *,
    bundle: TalkBundle,
    key: str,
    bubbles: list[TalkBubbleExecutionResult],
) -> TalkBundleExecutionResult:
    sent_count = sum(1 for item in bubbles if item.status in {"executed", "replayed"})
    unknown_count = sum(1 for item in bubbles if item.status == "unknown")
    failed_count = sum(1 for item in bubbles if item.status == "failed")
    blocked_count = sum(1 for item in bubbles if item.status in {"blocked", "unsupported"})
    if not bubbles:
        status = "blocked"
        delivery_state = "blocked"
        reason = "empty_bundle"
    elif sent_count and (unknown_count or failed_count or blocked_count):
        status = "partial"
        delivery_state = "partially_sent"
        reason = "partial_delivery"
    elif sent_count == len(bubbles):
        status = "executed"
        delivery_state = "confirmed"
        reason = "delivery_confirmed"
    elif unknown_count:
        status = "unknown"
        delivery_state = "unknown"
        reason = "delivery_unknown"
    elif blocked_count == len(bubbles):
        status = "blocked"
        delivery_state = "blocked"
        reason = "bundle_blocked"
    else:
        status = "failed"
        delivery_state = "failed"
        reason = "delivery_failed"
    return TalkBundleExecutionResult(
        status=status,
        delivery_state=delivery_state,
        reason=reason,
        bundle_key=key,
        conversation_id=bundle.conversation_id,
        sent_count=sent_count,
        failed_count=failed_count,
        unknown_count=unknown_count,
        blocked_count=blocked_count,
        bubbles=bubbles,
    )
