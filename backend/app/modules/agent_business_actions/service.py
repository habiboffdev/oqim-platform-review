from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_session import AgentSession
from app.models.commerce_catalog import CatalogOfferRecord, CatalogProductRecord
from app.models.customer import Customer as CustomerRecord
from app.modules.commercial_spine.contracts import (
    BusinessBrainProjection,
    CommercialActionProposal,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.hermes_runtime.contracts import HermesRunEventInput, HermesRunEventKind
from app.modules.hermes_runtime.service import HermesRunService


@dataclass(frozen=True)
class OwnerTaskResult:
    task_ref: str
    proposal_id: str
    status: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class OwnerNotificationResult:
    notification_ref: str
    status: str
    bot_payload: dict[str, Any]


@dataclass(frozen=True)
class CustomerIntelligenceResult:
    intelligence_ref: str
    status: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CheckoutIntentResult:
    checkout_ref: str
    order_ref: str
    proposal_id: str
    status: str
    missing_fields: list[str]
    authority_refs: list[str]
    payload: dict[str, Any]


HANDOFF_KINDS: frozenset[str] = frozenset(
    {"lead", "support", "complaint", "refund", "human_requested"}
)


def handoff_kinds_from_refs(source_refs: list[str] | None) -> list[str]:
    """All handoff kinds carried in ``source_refs`` as ``handoff:{kind}`` refs, in
    order, validated against HANDOFF_KINDS. The single canonical parser (#421) —
    kept next to the kind authority so adding a kind can't drift the two layers."""
    kinds: list[str] = []
    for ref in source_refs or []:
        text = str(ref)
        if text.startswith("handoff:"):
            kind = text.split(":", 1)[1]
            if kind in HANDOFF_KINDS:
                kinds.append(kind)
    return kinds


def handoff_kind_from_refs(source_refs: list[str] | None) -> str | None:
    """The first handoff kind in ``source_refs`` (validated), or ``None``."""
    return next(iter(handoff_kinds_from_refs(source_refs)), None)


_HANDOFF_TASK_KIND = {
    "lead": "call",
    "support": "follow_up",
    "complaint": "follow_up",
    "human_requested": "call",
}
_HANDOFF_PRIORITY = {
    "lead": "high",
    "support": "medium",
    "complaint": "urgent",
    "human_requested": "high",
}
_HANDOFF_RECOMMENDED = {
    "lead": "Mijozga qo'ng'iroq qilib, keyingi qadamni kelishib oling.",
    "support": "Mijozning support so'rovini ko'rib chiqing.",
    "complaint": "Mijoz bilan tezda bog'lanib, shikoyatni hal qiling.",
    "human_requested": "Mijoz operator so'radi — o'zingiz bog'laning.",
}


@dataclass(frozen=True)
class HandoffResult:
    kind: str
    task_ref: str
    notification_ref: str
    status: str


class AgentBusinessActionService:
    """Generic business work primitives callable by Hermes tools."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repository = CommercialSpineRepository(db)

    async def create_task(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
        agent_id: int,
        conversation_id: int,
        customer_id: int | None = None,
        hermes_run_id: str | None = None,
        conversation_state_snapshot_id: int | None = None,
        task_kind: str,
        title: str,
        reason: str,
        priority: str = "medium",
        selected_item_refs: list[str] | None = None,
        missing_authority: list[str] | None = None,
        due_at: str | None = None,
        source_refs: list[str] | None = None,
        context: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> OwnerTaskResult:
        session = await self._validate_scope(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
        )
        actual_customer_id = customer_id or session.customer_id or 0
        source_refs = _source_refs(
            [
                *(source_refs or []),
                f"agent_session:{agent_session_id}",
                *([f"hermes_run:{hermes_run_id}"] if hermes_run_id else []),
                *(
                    [f"conversation_state:{conversation_state_snapshot_id}"]
                    if conversation_state_snapshot_id is not None
                    else []
                ),
            ],
            fallback=f"agent_session:{agent_session_id}",
        )
        payload = {
            "owner_task": {
                "task_kind": _clean_choice(
                    task_kind,
                    allowed={"business", "meeting", "delivery", "stock", "call", "payment", "follow_up"},
                    default="business",
                ),
                "title": _required_text(title, "title"),
                "detail": _required_text(reason, "reason"),
                "reason": _required_text(reason, "reason"),
                "agent_session_id": agent_session_id,
                "conversation_state_snapshot_id": conversation_state_snapshot_id,
                "selected_item_refs": _string_list(selected_item_refs),
                "missing_authority": _string_list(missing_authority),
                "due_at": _clean_optional(due_at),
            }
        }
        if context:
            # owner UX context (customer label, chat summary, next step) —
            # rendered on the approval card so ONE message tells the story
            payload["owner_task"]["context"] = _object_arg(context)
        key = idempotency_key or _stable_key(
            "work.create_task",
            workspace_id,
            agent_session_id,
            payload,
            source_refs,
        )
        existing = await self._repository.get_action_proposal_by_idempotency_key(
            workspace_id=workspace_id,
            idempotency_key=key,
        )
        if existing is None:
            proposal_id = f"owner_task:{uuid.uuid4().hex}"
            proposal = CommercialActionProposal(
                proposal_id=proposal_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                customer_id=actual_customer_id,
                action_type="create_business_task",
                lifecycle_state="proposed",
                execution_mode="suggest_only",
                risk_level="medium" if payload["owner_task"]["missing_authority"] else "low",
                requires_approval=True,
                executor_runtime="owner_task",
                priority=_clean_priority(priority),
                confidence=1.0,
                reason_code="agent_created_owner_task",
                source_refs=source_refs,
                payload=payload,
                idempotency_key=key,
                correlation_id=f"work.create_task:{workspace_id}:{agent_session_id}",
                trace_id=hermes_run_id,
            )
            await self._repository.persist_action_proposal(proposal)
            existing = proposal
        await self._record_event(
            workspace_id=workspace_id,
            hermes_run_id=hermes_run_id,
            tool_name="work.create_task",
            payload={
                "proposal_id": existing.proposal_id,
                "task_kind": existing.payload.get("owner_task", {}).get("task_kind"),
                "missing_authority": existing.payload.get("owner_task", {}).get("missing_authority", []),
            },
            idempotency_key=f"{key}:hermes-event",
        )
        return OwnerTaskResult(
            task_ref=f"owner_task:{existing.proposal_id}",
            proposal_id=existing.proposal_id,
            status=existing.lifecycle_state,
            payload=dict(existing.payload),
        )

    async def notify_owner(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
        agent_id: int,
        conversation_id: int,
        customer_id: int | None = None,
        hermes_run_id: str | None = None,
        task_ref: str | None = None,
        order_ref: str | None = None,
        title: str,
        summary: str,
        recommended_action: str,
        selected_item_refs: list[str] | None = None,
        shown_price_refs: list[str] | None = None,
        missing_authority: list[str] | None = None,
        source_refs: list[str] | None = None,
        customer_label: str | None = None,
        chat_summary: str | None = None,
        idempotency_key: str | None = None,
    ) -> OwnerNotificationResult:
        session = await self._validate_scope(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
        )
        actual_customer_id = customer_id or session.customer_id or 0
        bot_payload = {
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "agent_session_id": agent_session_id,
            "customer_id": actual_customer_id,
            "conversation_id": conversation_id,
            "task_ref": _clean_optional(task_ref),
            "order_ref": _clean_optional(order_ref),
            "title": _required_text(title, "title"),
            "summary": _required_text(summary, "summary"),
            "recommended_action": _required_text(recommended_action, "recommended_action"),
            "selected_item_refs": _string_list(selected_item_refs),
            "shown_price_refs": _string_list(shown_price_refs),
            "missing_authority": _string_list(missing_authority),
            "source_refs": _string_list(source_refs),
            # owner UX context: who the customer is + what the chat was about
            "customer_label": _clean_optional(customer_label),
            "chat_summary": _clean_optional(chat_summary),
            "hermes_run_id": hermes_run_id,
        }
        key = idempotency_key or _stable_key(
            "owner.notify",
            workspace_id,
            agent_session_id,
            bot_payload,
        )
        notification_ref = f"owner_notification:{workspace_id}:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}"
        existing = await self._repository.get_projection(
            workspace_id=workspace_id,
            projection_ref=notification_ref,
        )
        if existing is not None:
            existing_payload = dict(existing.state.get("bot_payload") or {})
            return OwnerNotificationResult(
                notification_ref=notification_ref,
                status=str(existing.state.get("status") or "queued"),
                bot_payload=existing_payload,
            )
        source_refs = _source_refs(
            [
                *(source_refs or []),
                *([task_ref] if task_ref else []),
                *([order_ref] if order_ref else []),
                f"agent_session:{agent_session_id}",
                *([f"hermes_run:{hermes_run_id}"] if hermes_run_id else []),
            ],
            fallback=f"agent_session:{agent_session_id}",
        )
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=notification_ref,
                workspace_id=workspace_id,
                projection_type="owner_notification",
                entity_ref=f"agent_session:{agent_session_id}",
                state={
                    "status": "queued",
                    "channel": "owner_bot",
                    "bot_payload": bot_payload,
                    "idempotency_key": key,
                },
                source_refs=source_refs,
            )
        )
        await self._record_event(
            workspace_id=workspace_id,
            hermes_run_id=hermes_run_id,
            tool_name="owner.notify",
            payload={
                "notification_ref": notification_ref,
                "task_ref": task_ref,
                "order_ref": order_ref,
                "missing_authority": bot_payload["missing_authority"],
            },
            idempotency_key=f"{key}:hermes-event",
        )
        return OwnerNotificationResult(
            notification_ref=notification_ref,
            status="queued",
            bot_payload=bot_payload,
        )

    async def handoff(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
        agent_id: int,
        conversation_id: int,
        customer_id: int | None,
        hermes_run_id: str | None,
        kind: str,
        title: str,
        detail: str,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        idempotency_key: str,
    ) -> HandoffResult:
        """Atomically hand the conversation to a human: task + notification.

        ONE call, one idempotency stem — the half-done escalation class
        (promise a human, record nothing) is impossible by construction.
        Both records carry ``handoff:{kind}`` in source_refs so the owner
        bot renders the right card/notification header.
        """
        if kind not in HANDOFF_KINDS:
            raise ValueError(f"invalid_handoff_kind: {kind}")
        kind_ref = f"handoff:{kind}"
        # Dedup: at most ONE open handoff of this kind per conversation. The tool
        # prompt says never repeat a handoff, but a weak model re-emits on every
        # "still waiting" turn; on a no-executor pilot these proposals pile up and
        # `_open_handoff_state` feeds them back as "queued/stale" -> the model
        # apologizes AND re-handoffs, an output->input loop (live 2026-06-13).
        # Reuse the open one (no new card/notification) instead of stacking.
        existing_open = await self._open_handoff_proposal(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            kind=kind,
            exclude_idempotency_key=f"{idempotency_key}:task",
        )
        if existing_open is not None:
            ref = f"owner_task:{existing_open.proposal_id}"
            return HandoffResult(
                kind=kind,
                task_ref=ref,
                notification_ref=ref,
                status=existing_open.lifecycle_state,
            )
        # Owner UX context (bookkeeping, not action): who + what the chat was
        # about, so the bot notification is decision-ready at a glance.
        context = await self._handoff_context(
            agent_session_id=agent_session_id,
            customer_id=customer_id,
        )
        # Agent-recorded customer details win: the agent judged the chat and
        # recorded who the customer is — the host only stores what the tool
        # call says (no host-side chat parsing; founder rule 2026-06-10).
        recorded_name = (customer_name or "").strip()
        recorded_phone = (customer_phone or "").strip()
        if recorded_name:
            context["customer_label"] = recorded_name
        if recorded_phone:
            context["customer_phone"] = recorded_phone
            await self._store_agent_recorded_phone(
                agent_session_id=agent_session_id,
                customer_id=customer_id,
                phone=recorded_phone,
            )
        customer_label = context.get("customer_label")
        chat_summary = context.get("chat_summary")
        card_context = {
            **context,
            "recommended_action": _HANDOFF_RECOMMENDED[kind],
        }
        task = await self.create_task(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            hermes_run_id=hermes_run_id,
            conversation_state_snapshot_id=None,
            task_kind=_HANDOFF_TASK_KIND[kind],
            title=title,
            reason=detail,
            priority=_HANDOFF_PRIORITY[kind],
            selected_item_refs=[],
            missing_authority=[],
            due_at=None,
            source_refs=[kind_ref],
            context=card_context,
            idempotency_key=f"{idempotency_key}:task",
        )
        notification = await self.notify_owner(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            hermes_run_id=hermes_run_id,
            task_ref=task.task_ref,
            order_ref=None,
            title=title,
            summary=detail,
            recommended_action=_HANDOFF_RECOMMENDED[kind],
            selected_item_refs=[],
            shown_price_refs=[],
            missing_authority=[],
            source_refs=[kind_ref],
            customer_label=customer_label,
            chat_summary=chat_summary,
            idempotency_key=f"{idempotency_key}:notify",
        )
        # ONE bot message per handoff: the approval card carries the full
        # story, so the notification stays as an audit row and never flushes.
        status = await self._merge_notification_into_card(
            workspace_id=workspace_id,
            notification_ref=notification.notification_ref,
        )
        return HandoffResult(
            kind=kind,
            task_ref=task.task_ref,
            notification_ref=notification.notification_ref,
            status=status,
        )

    async def _open_handoff_proposal(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
        kind: str,
        exclude_idempotency_key: str | None = None,
    ) -> CommercialActionProposal | None:
        """The conversation's open (proposed/approved) handoff task of this kind,
        if any. Mirrors `_open_handoff_state`'s read so dedup and status agree.

        `exclude_idempotency_key` skips THIS call's own task so a same-key replay
        falls through to the normal idempotent path (which returns the real task
        AND notification refs); only a DIFFERENT-key repeat is deduped.
        """
        kind_ref = f"handoff:{kind}"
        proposals = await self._repository.list_action_proposals(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            action_type="create_business_task",
            lifecycle_states=("proposed", "approved"),
            limit=20,
        )
        for proposal in proposals:
            if proposal.idempotency_key == exclude_idempotency_key:
                continue
            if kind_ref in (proposal.source_refs or []):
                return proposal
        return None

    async def _merge_notification_into_card(
        self,
        *,
        workspace_id: int,
        notification_ref: str,
    ) -> str:
        projection = await self._repository.get_projection(
            workspace_id=workspace_id,
            projection_ref=notification_ref,
        )
        if projection is None:
            return "queued"
        status = str(projection.state.get("status") or "queued")
        if status != "queued":
            return status
        await self._repository.upsert_projection(
            projection.model_copy(
                update={"state": {**projection.state, "status": "merged_into_card"}}
            )
        )
        return "merged_into_card"

    async def record_intelligence(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
        agent_id: int,
        conversation_id: int,
        customer_id: int | None = None,
        hermes_run_id: str | None = None,
        lead_stage: str = "unknown",
        buying_signals: list[str] | None = None,
        objections: list[str] | None = None,
        preferences: dict[str, Any] | None = None,
        next_best_action: str | None = None,
        owner_notes: list[str] | None = None,
        risk_flags: list[str] | None = None,
        source_refs: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> CustomerIntelligenceResult:
        session = await self._validate_scope(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
        )
        actual_customer_id = customer_id or session.customer_id or 0
        payload = {
            "lead_stage": _clean_choice(
                lead_stage,
                allowed={
                    "unknown",
                    "new",
                    "interested",
                    "qualified",
                    "checkout",
                    "blocked",
                    "won",
                    "lost",
                    "follow_up",
                },
                default="unknown",
            ),
            "buying_signals": _string_list(buying_signals),
            "objections": _string_list(objections),
            "preferences": _object_arg(preferences),
            "next_best_action": _clean_optional(next_best_action),
            "owner_notes": _string_list(owner_notes),
            "risk_flags": _string_list(risk_flags),
            "agent_session_id": agent_session_id,
            "customer_id": actual_customer_id,
            "conversation_id": conversation_id,
            "hermes_run_id": hermes_run_id,
        }
        key = idempotency_key or _stable_key(
            "conversation.record_intelligence",
            workspace_id,
            agent_session_id,
            payload,
            source_refs,
        )
        intelligence_ref = (
            "customer_intelligence:"
            f"{workspace_id}:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}"
        )
        existing = await self._repository.get_projection(
            workspace_id=workspace_id,
            projection_ref=intelligence_ref,
        )
        if existing is not None:
            return CustomerIntelligenceResult(
                intelligence_ref=intelligence_ref,
                status=str(existing.state.get("status") or "recorded"),
                payload=dict(existing.state.get("intelligence") or {}),
            )
        refs = _source_refs(
            [
                *(source_refs or []),
                f"agent_session:{agent_session_id}",
                *([f"hermes_run:{hermes_run_id}"] if hermes_run_id else []),
            ],
            fallback=f"agent_session:{agent_session_id}",
        )
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=intelligence_ref,
                workspace_id=workspace_id,
                projection_type="customer_intelligence",
                entity_ref=f"customer:{actual_customer_id}",
                state={
                    "status": "recorded",
                    "intelligence": payload,
                    **payload,
                    "idempotency_key": key,
                },
                source_refs=refs,
            )
        )
        await self._record_event(
            workspace_id=workspace_id,
            hermes_run_id=hermes_run_id,
            tool_name="conversation.record_intelligence",
            payload={
                "intelligence_ref": intelligence_ref,
                "lead_stage": payload["lead_stage"],
                "buying_signal_count": len(payload["buying_signals"]),
                "risk_flags": payload["risk_flags"],
            },
            idempotency_key=f"{key}:hermes-event",
        )
        return CustomerIntelligenceResult(
            intelligence_ref=intelligence_ref,
            status="recorded",
            payload=payload,
        )

    async def create_checkout_intent(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
        agent_id: int,
        conversation_id: int,
        customer_id: int | None = None,
        hermes_run_id: str | None = None,
        selected_items: list[dict[str, Any]],
        shown_prices: list[dict[str, Any]] | None = None,
        payment_method: str | None = None,
        fulfillment_method: str | None = None,
        status: str = "pending",
        missing_fields: list[str] | None = None,
        linked_task_refs: list[str] | None = None,
        source_refs: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> CheckoutIntentResult:
        return await self._create_commerce_intent(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            hermes_run_id=hermes_run_id,
            selected_items=selected_items,
            shown_prices=shown_prices,
            payment_method=payment_method,
            fulfillment_method=fulfillment_method,
            status=status,
            missing_fields=missing_fields,
            linked_task_refs=linked_task_refs,
            source_refs=source_refs,
            idempotency_key=idempotency_key,
            intent_kind="checkout",
        )

    async def create_order_intent(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
        agent_id: int,
        conversation_id: int,
        customer_id: int | None = None,
        hermes_run_id: str | None = None,
        selected_items: list[dict[str, Any]],
        shown_prices: list[dict[str, Any]] | None = None,
        payment_method: str | None = None,
        fulfillment_method: str | None = None,
        status: str = "pending",
        missing_fields: list[str] | None = None,
        linked_task_refs: list[str] | None = None,
        source_refs: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> CheckoutIntentResult:
        return await self._create_commerce_intent(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            hermes_run_id=hermes_run_id,
            selected_items=selected_items,
            shown_prices=shown_prices,
            payment_method=payment_method,
            fulfillment_method=fulfillment_method,
            status=status,
            missing_fields=missing_fields,
            linked_task_refs=linked_task_refs,
            source_refs=source_refs,
            idempotency_key=idempotency_key,
            intent_kind="order",
        )

    async def _create_commerce_intent(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
        agent_id: int,
        conversation_id: int,
        customer_id: int | None,
        hermes_run_id: str | None,
        selected_items: list[dict[str, Any]],
        shown_prices: list[dict[str, Any]] | None,
        payment_method: str | None,
        fulfillment_method: str | None,
        status: str,
        missing_fields: list[str] | None,
        linked_task_refs: list[str] | None,
        source_refs: list[str] | None,
        idempotency_key: str | None,
        intent_kind: str,
    ) -> CheckoutIntentResult:
        session = await self._validate_scope(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
        )
        actual_customer_id = customer_id or session.customer_id or 0
        normalized_items = _object_list(selected_items)
        if not normalized_items:
            raise ValueError("selected_items_required")
        shown_prices = _object_list(shown_prices)
        validation = await self._validate_checkout_authority(
            workspace_id=workspace_id,
            selected_items=normalized_items,
            shown_prices=shown_prices,
        )
        all_missing = list(
            dict.fromkeys([
                *_string_list(missing_fields),
                *validation["missing_fields"],
            ])
        )
        final_status = _checkout_status(status=status, missing_fields=all_missing)
        intent_key = "order_intent" if intent_kind == "order" else "checkout_intent"
        payload = {
            intent_key: {
                "agent_session_id": agent_session_id,
                "selected_items": normalized_items,
                "shown_prices": shown_prices,
                "payment_method": _clean_optional(payment_method),
                "fulfillment_method": _clean_optional(fulfillment_method),
                "status": final_status,
                "missing_fields": all_missing,
                "authority_refs": validation["authority_refs"],
                "linked_task_refs": _string_list(linked_task_refs),
            }
        }
        key = idempotency_key or _stable_key(
            f"commerce.create_{intent_key}",
            workspace_id,
            agent_session_id,
            payload,
            source_refs,
        )
        existing = await self._repository.get_action_proposal_by_idempotency_key(
            workspace_id=workspace_id,
            idempotency_key=key,
        )
        if existing is None:
            proposal_id = f"{intent_key}:{uuid.uuid4().hex}"
            proposal = CommercialActionProposal(
                proposal_id=proposal_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                customer_id=actual_customer_id,
                action_type=f"create_{intent_key}",
                lifecycle_state="proposed",
                execution_mode="suggest_only",
                risk_level="medium" if all_missing else "low",
                requires_approval=True,
                executor_runtime=f"commerce_{intent_kind}",
                priority="high" if all_missing else "medium",
                confidence=1.0,
                reason_code=f"agent_created_{intent_key}",
                source_refs=_source_refs(
                    [
                        *(source_refs or []),
                        f"agent_session:{agent_session_id}",
                        *([f"hermes_run:{hermes_run_id}"] if hermes_run_id else []),
                        *validation["authority_refs"],
                    ],
                    fallback=f"agent_session:{agent_session_id}",
                ),
                payload=payload,
                idempotency_key=key,
                correlation_id=f"commerce.create_{intent_key}:{workspace_id}:{agent_session_id}",
                trace_id=hermes_run_id,
            )
            await self._repository.persist_action_proposal(proposal)
            existing = proposal
        intent_ref = f"{intent_key}:{existing.proposal_id}"
        existing_payload = dict(existing.payload.get(intent_key) or {})
        projection = BusinessBrainProjection(
            projection_ref=intent_ref,
            workspace_id=workspace_id,
            projection_type=f"commerce_{intent_key}",
            entity_ref=f"agent_session:{agent_session_id}",
            state={
                **existing_payload,
                "checkout_ref": intent_ref,
                "order_ref": intent_ref,
                "proposal_id": existing.proposal_id,
                "customer_id": actual_customer_id,
                "conversation_id": conversation_id,
                "hermes_run_id": hermes_run_id,
            },
            source_refs=existing.source_refs,
        )
        await self._repository.upsert_projection(projection)
        await self._record_event(
            workspace_id=workspace_id,
            hermes_run_id=hermes_run_id,
            tool_name=f"commerce.create_{intent_key}",
            payload={
                "checkout_ref": intent_ref,
                "order_ref": intent_ref,
                "proposal_id": existing.proposal_id,
                "status": existing_payload.get("status"),
                "missing_fields": existing_payload.get("missing_fields", []),
                "authority_refs": existing_payload.get("authority_refs", []),
            },
            idempotency_key=f"{key}:hermes-event",
        )
        return CheckoutIntentResult(
            checkout_ref=intent_ref,
            order_ref=intent_ref,
            proposal_id=existing.proposal_id,
            status=str(existing_payload.get("status") or "pending"),
            missing_fields=_string_list(existing_payload.get("missing_fields")),
            authority_refs=_string_list(existing_payload.get("authority_refs")),
            payload=dict(existing.payload),
        )

    async def _store_agent_recorded_phone(
        self,
        *,
        agent_session_id: int,
        customer_id: int | None,
        phone: str,
    ) -> None:
        """Persist the phone the AGENT explicitly recorded (never parsed)."""
        actual_customer_id = customer_id
        if not actual_customer_id:
            session = await self._db.get(AgentSession, agent_session_id)
            actual_customer_id = session.customer_id if session is not None else None
        if not actual_customer_id:
            return
        customer = await self._db.get(CustomerRecord, actual_customer_id)
        if customer is not None and not (customer.phone_number or "").strip():
            customer.phone_number = phone

    async def _handoff_context(
        self,
        *,
        agent_session_id: int,
        customer_id: int | None,
    ) -> dict[str, str]:
        """Best-effort owner-facing context: who, how to reach them, chat gist.

        ``telegram_link`` is deterministic (tg://user?id=...) so the owner can
        jump straight to the customer's profile from the card.
        """
        session = await self._db.get(AgentSession, agent_session_id)
        chat_summary = (session.summary or "").strip() if session is not None else ""
        actual_customer_id = customer_id or (session.customer_id if session is not None else None)
        context: dict[str, str] = {}
        if chat_summary:
            context["chat_summary"] = chat_summary
        if actual_customer_id:
            customer = await self._db.get(CustomerRecord, actual_customer_id)
            if customer is not None:
                name = (customer.display_name or "").strip()
                phone = (customer.phone_number or "").strip()
                label = f"{name} ({phone})" if name and phone else (name or phone or "")
                if label:
                    context["customer_label"] = label
                if phone:
                    context["customer_phone"] = phone
                username = (customer.telegram_username or "").strip().lstrip("@")
                if username:
                    # t.me links always render clickable; tg://user?id mentions
                    # are stripped for users who never interacted with the bot
                    context["telegram_link"] = f"https://t.me/{username}"
                elif customer.telegram_id:
                    context["telegram_link"] = f"tg://user?id={int(customer.telegram_id)}"
        return context

    async def _validate_scope(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
        agent_id: int,
        conversation_id: int,
        customer_id: int | None,
    ) -> AgentSession:
        session = await self._db.get(AgentSession, agent_session_id)
        if session is None:
            raise ValueError("agent_session_not_found")
        if (
            session.workspace_id != workspace_id
            or session.agent_id != agent_id
            or session.conversation_id != conversation_id
        ):
            raise ValueError("agent_session_scope_mismatch")
        if customer_id is not None and session.customer_id is not None and session.customer_id != customer_id:
            raise ValueError("agent_session_customer_mismatch")
        return session

    async def _record_event(
        self,
        *,
        workspace_id: int,
        hermes_run_id: str | None,
        tool_name: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        if not hermes_run_id:
            return
        try:
            await HermesRunService(self._db).record_event(
                HermesRunEventInput(
                    run_id=hermes_run_id,
                    workspace_id=workspace_id,
                    kind=HermesRunEventKind.TOOL_CALLED,
                    visibility="internal",
                    tool_name=tool_name,
                    tool_state="ok",
                    payload=payload,
                    correlation_id=f"{tool_name}:{workspace_id}",
                    idempotency_key=idempotency_key,
                )
            )
        except Exception:
            return

    async def _validate_checkout_authority(
        self,
        *,
        workspace_id: int,
        selected_items: list[dict[str, Any]],
        shown_prices: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        missing: list[str] = []
        authority_refs: list[str] = []
        for item in selected_items:
            product_ref = str(item.get("product_ref") or "").strip()
            offer_ref = str(item.get("offer_ref") or "").strip()
            if product_ref:
                product = await self._db.scalar(
                    select(CatalogProductRecord).where(
                        CatalogProductRecord.workspace_id == workspace_id,
                        CatalogProductRecord.product_ref == product_ref,
                    )
                )
                if product is None or product.authority_state != "approved":
                    missing.append(f"approved_product:{product_ref}")
            if offer_ref:
                offer = await self._db.scalar(
                    select(CatalogOfferRecord).where(
                        CatalogOfferRecord.workspace_id == workspace_id,
                        CatalogOfferRecord.offer_ref == offer_ref,
                    )
                )
                if offer is None or offer.authority_state != "approved":
                    missing.append(f"approved_offer:{offer_ref}")
                    continue
                authority_refs.extend(str(ref) for ref in list(offer.source_fact_ids or []) if ref)
                if not _shown_price_matches_offer(offer, shown_prices):
                    missing.append(f"shown_price:{offer_ref}")
        return {
            "missing_fields": list(dict.fromkeys(missing)),
            "authority_refs": list(dict.fromkeys(authority_refs)),
        }


def _required_text(value: str | None, field_name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_name}_required")
    return text


def _clean_optional(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _object_arg(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _source_refs(values: list[str], *, fallback: str) -> list[str]:
    refs = _string_list(values)
    return list(dict.fromkeys(refs)) or [fallback]


def _clean_priority(value: str) -> str:
    return _clean_choice(
        value,
        allowed={"low", "medium", "high", "urgent"},
        default="medium",
    )


def _clean_choice(value: str, *, allowed: set[str], default: str) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in allowed else default


def _stable_key(prefix: str, *parts: Any) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _checkout_status(*, status: str, missing_fields: list[str]) -> str:
    normalized = (status or "pending").strip().lower()
    if missing_fields:
        return "blocked"
    return normalized if normalized in {"pending", "blocked", "ready"} else "pending"


def _shown_price_matches_offer(
    offer: CatalogOfferRecord,
    shown_prices: list[dict[str, Any]],
) -> bool:
    if not offer.price:
        return False
    for price in shown_prices:
        if str(price.get("offer_ref") or "").strip() != offer.offer_ref:
            continue
        amount = str(price.get("amount") or price.get("price") or "").strip()
        currency = str(price.get("currency") or "").strip()
        if amount == str(offer.price) and (not offer.currency or currency == str(offer.currency)):
            return True
    return False
