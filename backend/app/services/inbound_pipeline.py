"""Unified inbound message pipeline — PRD #139.

Extracted from message_intake.py so that multiple intake sources
(GramJS sidecar webhook, Business Bot, Instagram) can share the same flow:
persist -> classify -> prepare evidence -> reply lifecycle.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.agent_session import AgentSession
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.delivery_runtime import DeliveryRuntime
from app.models.message import Message, SenderType
from app.models.workspace import Workspace
from app.modules.agent_sessions.hot_path import AgentSessionHotPathService
from app.modules.agent_sessions.service import AgentSessionService
from app.modules.conversation_core.service import PersistMessageResult, bump_conversation_revision, persist_message
from app.modules.conversation_turns.service import ConversationTurnSessionService
from app.modules.message_intake.classifier import classify_local
from app.modules.message_intake.normalizer import normalize, normalize_text_entities
from app.services.conversation_state import (
    ConversationSyncState,
    get_customer_conversation_state,
    refresh_customer_conversation_state,
    resolved_pipeline_stage,
    set_customer_conversation_state,
)
from app.services.message_response_projection import build_delivery_runtime_response

if TYPE_CHECKING:
    from app.modules.conversation_turns.runner import ConversationTurnRunner

logger = get_logger("services.inbound_pipeline")

# Media types that carry audio content (can be transcribed)
_VOICE_MEDIA_TYPES = frozenset({"voice", "audio"})
# Media types that carry visual content (can be described)
_IMAGE_MEDIA_TYPES = frozenset({"photo"})


def _get_recovered_tail_trigger_message_id(conversation: Conversation) -> int | None:
    state = get_customer_conversation_state(conversation)
    sync_state = state.sync
    return sync_state.last_recovered_tail_trigger_message_id if sync_state else None


def _set_recovered_tail_trigger_message_id(conversation: Conversation, message_id: int) -> None:
    state = get_customer_conversation_state(conversation)
    sync_state = state.sync or ConversationSyncState()
    sync_state.last_recovered_tail_trigger_message_id = message_id
    state.sync = sync_state
    set_customer_conversation_state(conversation, state)


def record_pending_edit(
    *,
    conversation: Conversation,
    telegram_message_id: int,
    text: str,
    edited_at: datetime | None = None,
) -> None:
    state = get_customer_conversation_state(conversation)
    sync_state = state.sync or ConversationSyncState()
    pending_edits = dict(sync_state.pending_edits or {})
    pending_edits[str(telegram_message_id)] = {
        "text": text,
        "edited_at": (edited_at or datetime.now(UTC)).isoformat(),
    }
    sync_state.pending_edits = pending_edits
    state.sync = sync_state
    set_customer_conversation_state(conversation, state)


def _consume_pending_edit(
    *,
    conversation: Conversation,
    telegram_message_id: int | None,
) -> tuple[str, datetime | None] | None:
    if telegram_message_id is None:
        return None

    state = get_customer_conversation_state(conversation)
    sync_state = state.sync
    pending_edits = dict(sync_state.pending_edits or {}) if sync_state else {}
    payload = pending_edits.pop(str(telegram_message_id), None)
    if payload is None:
        return None

    if sync_state is None:
        sync_state = ConversationSyncState()
    sync_state.pending_edits = pending_edits or None
    state.sync = sync_state
    set_customer_conversation_state(conversation, state)

    edited_at_raw = payload.get("edited_at")
    edited_at = datetime.fromisoformat(edited_at_raw) if edited_at_raw else None
    return str(payload.get("text") or ""), edited_at


async def _has_local_agent_message_after(
    *,
    session: AsyncSession,
    conversation_id: int,
    message: Message,
) -> bool:
    message_cursor = message.telegram_timestamp or message.created_at
    if message_cursor is None:
        return False

    later_agent_message = await session.scalar(
        select(Message.id)
        .where(
            Message.conversation_id == conversation_id,
            Message.sender_type == SenderType.SELLER.value,
            (
                (Message.telegram_timestamp > message_cursor)
                | ((Message.telegram_timestamp == message_cursor) & (Message.id > message.id))
            ),
        )
        .limit(1)
    )
    if later_agent_message is not None:
        return True

    later_agent_message_without_telegram_ts = await session.scalar(
        select(Message.id)
        .where(
            Message.conversation_id == conversation_id,
            Message.sender_type == SenderType.SELLER.value,
            Message.telegram_timestamp.is_(None),
            (
                (Message.created_at > message_cursor)
                | ((Message.created_at == message_cursor) & (Message.id > message.id))
            ),
        )
        .limit(1)
    )
    return later_agent_message_without_telegram_ts is not None


class CanonicalIntakeResult(BaseModel):
    """Canonical outcome for a message intake event.

    This is the stable contract for live ingress callers. Routes can still
    serialize it directly, while downstream reply-runtime code can rely on a
    single semantic shape instead of ad-hoc dicts.
    """

    status: Literal["persisted", "duplicate"]
    message_id: int
    conversation_id: int
    reply_generation_triggered: bool
    action: Literal[
        "created",
        "duplicate",
        "recovered",
        "outbound_echo",
        "edited",
        "deleted",
        "forwarded",
    ]
    direction: Literal["inbound", "outbound"]
    is_customer_inbound: bool
    is_new_or_recovered: bool
    requires_conversation_reconcile: bool
    requires_reply_evaluation: bool


async def recover_catch_up_message(
    *,
    session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    message: Message,
    customer: Customer,
    conversation_turn_runner: ConversationTurnRunner,
) -> CanonicalIntakeResult | None:
    """Recover a persisted catch-up message through the canonical intake contract.

    This keeps reply-generation trigger semantics aligned between live ingress
    and recovered unread messages without re-persisting the message.
    """
    results = await recover_catch_up_window(
        session=session,
        workspace=workspace,
        conversation=conversation,
        messages=[message],
        customer=customer,
        conversation_turn_runner=conversation_turn_runner,
    )
    return results[0] if results else None


async def recover_catch_up_window(
    *,
    session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    messages: list[Message],
    customer: Customer,
    conversation_turn_runner: ConversationTurnRunner,
) -> list[CanonicalIntakeResult]:
    """Recover a persisted catch-up window through the canonical intake contract.

    Reply-generation semantics are computed from the recovered tail window, not
    a single latest inbound message:
    - if seller already replied in the recovered tail, do not enqueue a reply
    - if the recovered tail ends with consecutive customer messages, choose
      exactly one latest customer message as the reply trigger
    """

    if not messages:
        return []

    ordered_messages = sorted(
        messages,
        key=lambda msg: (
            msg.telegram_timestamp or msg.created_at,
            msg.id,
        ),
    )
    recovered_customers = [
        message for message in ordered_messages
        if message.sender_type == SenderType.CUSTOMER.value
    ]
    if not recovered_customers:
        return []

    tail_candidate: Message | None = None
    tail_customer_messages: list[Message] = []
    if ordered_messages[-1].sender_type == SenderType.CUSTOMER.value:
        for message in reversed(ordered_messages):
            if message.sender_type != SenderType.CUSTOMER.value:
                break
            tail_customer_messages.append(message)
        tail_customer_messages.reverse()
        tail_candidate = tail_customer_messages[-1] if tail_customer_messages else None

    should_trigger = False
    if tail_candidate is not None:
        replayed_trigger_message_id = _get_recovered_tail_trigger_message_id(conversation)
        local_agent_message_exists = await _has_local_agent_message_after(
            session=session,
            conversation_id=conversation.id,
            message=tail_candidate,
        )
        if (
            replayed_trigger_message_id != tail_candidate.id
            and not local_agent_message_exists
        ):
            classification = classify_local(
                tail_candidate.content or "",
                media_type=tail_candidate.media_type,
            )
            should_trigger = classification.should_enter_reply_lifecycle
            if should_trigger:
                turn_service = ConversationTurnSessionService(session)
                for customer_message in tail_customer_messages:
                    await turn_service.append_customer_message(
                        workspace_id=workspace.id,
                        conversation=conversation,
                        customer=customer,
                        message=customer_message,
                    )
                _set_recovered_tail_trigger_message_id(conversation, tail_candidate.id)
                session.add(conversation)
                await conversation_turn_runner.enqueue_message(
                    workspace_id=workspace.id,
                    conversation_id=conversation.id,
                    message_id=tail_candidate.id,
                )

    await _refresh_reply_state(
        session=session,
        conversation=conversation,
    )

    return [
        CanonicalIntakeResult(
            status="persisted",
            message_id=recovered.id,
            conversation_id=conversation.id,
            reply_generation_triggered=bool(
                should_trigger and recovered.id == getattr(tail_candidate, "id", None)
            ),
            action="recovered",
            direction="inbound",
            is_customer_inbound=True,
            is_new_or_recovered=True,
            requires_conversation_reconcile=False,
            requires_reply_evaluation=bool(
                should_trigger and recovered.id == getattr(tail_candidate, "id", None)
            ),
        )
        for recovered in recovered_customers
    ]


async def process_inbound_message(
    *,
    raw_payload: dict[str, Any],
    workspace: Workspace,
    session: AsyncSession,
    conversation_turn_runner: ConversationTurnRunner,
    channel: str = "telegram_dm",
) -> CanonicalIntakeResult:
    """Process an inbound message through the full pipeline.

    Returns the canonical intake outcome for this message event.
    """
    msg_input = normalize(raw_payload, channel=channel)
    msg_input.workspace_id = workspace.id
    msg_input.sender_name = raw_payload.get("senderName", "")

    result = await persist_message(session, msg_input)

    return await process_persisted_message_event(
        raw_payload=raw_payload,
        workspace=workspace,
        session=session,
        conversation_turn_runner=conversation_turn_runner,
        persist_result=result,
    )


async def process_persisted_message_event(
    *,
    raw_payload: dict[str, Any],
    workspace: Workspace,
    session: AsyncSession,
    conversation_turn_runner: ConversationTurnRunner,
    persist_result: PersistMessageResult,
) -> CanonicalIntakeResult:
    """Run canonical post-persist side effects for one message event.

    EventSpine authoritative mode persists messages in the consumer, then calls
    this function so reply-runtime/state/WS behavior stays identical.
    """
    result = persist_result
    customer = result.customer
    conversation = result.conversation
    message = result.message
    is_outgoing = bool(raw_payload.get("isOutgoing", False))

    if result.is_duplicate:
        return CanonicalIntakeResult(
            status="duplicate",
            message_id=result.message.id,
            conversation_id=conversation.id,
            reply_generation_triggered=False,
            action="duplicate",
            direction="outbound" if is_outgoing else "inbound",
            is_customer_inbound=not is_outgoing,
            is_new_or_recovered=False,
            requires_conversation_reconcile=False,
            requires_reply_evaluation=False,
        )

    pending_edit = _consume_pending_edit(
        conversation=conversation,
        telegram_message_id=message.telegram_message_id,
    )
    if pending_edit is not None:
        pending_text, pending_edited_at = pending_edit
        message.content = pending_text
        message.edited_at = pending_edited_at
        session.add(message)
        session.add(conversation)

    reply_generation_triggered = False

    if is_outgoing:
        await _handle_seller_message(
            session=session,
            workspace=workspace,
            conversation=conversation,
            message=message,
            conversation_turn_runner=conversation_turn_runner,
        )
    else:
        reply_generation_triggered = await _handle_customer_message(
            session=session,
            workspace=workspace,
            conversation=conversation,
            message=message,
            customer=customer,
            conversation_turn_runner=conversation_turn_runner,
            raw_payload=raw_payload,
        )

    await _refresh_reply_state(
        session=session,
        conversation=conversation,
    )

    await _broadcast_new_message(
        session=session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        message=message,
    )

    action: Literal["created", "forwarded", "outbound_echo"] = "created"
    if is_outgoing:
        action = "outbound_echo"
    elif message.forward_from_name or message.forward_date:
        action = "forwarded"

    return CanonicalIntakeResult(
        status="persisted",
        message_id=message.id,
        conversation_id=conversation.id,
        reply_generation_triggered=reply_generation_triggered,
        action=action,
        direction="outbound" if is_outgoing else "inbound",
        is_customer_inbound=not is_outgoing,
        is_new_or_recovered=True,
        requires_conversation_reconcile=True,
        requires_reply_evaluation=reply_generation_triggered,
    )


REPLY_STATE_TAIL_WINDOW = 50
"""Recent-tail size fed into refresh_customer_conversation_state.

Covers unresolved-tail/seller-response boundary detection and media-readiness
aggregation with comfortable headroom. OQIM Intelligence owns semantic
customer and commercial projections outside this hot path. Bounding here
turns an O(conversation-tenure) SELECT into O(1) for every webhook/edit/
delete/recovery cycle.
"""


async def _refresh_reply_state(
    *,
    session: AsyncSession,
    conversation: Conversation,
) -> None:
    recent = (
        await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(
                Message.telegram_timestamp.desc().nullslast(),
                Message.telegram_message_id.desc().nullslast(),
                Message.created_at.desc(),
                Message.id.desc(),
            )
            .limit(REPLY_STATE_TAIL_WINDOW)
        )
    ).scalars().all()
    messages = list(reversed(recent))
    await refresh_customer_conversation_state(
        conversation,
        messages=messages,
    )
    session.add(conversation)


async def process_message_edit(
    *,
    session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    message: Message,
    conversation_turn_runner: ConversationTurnRunner,
    edited_text: str,
    edited_at: datetime | None = None,
    text_entities: list[dict] | None = None,
) -> CanonicalIntakeResult:
    """Apply an edit event and return the canonical intake outcome."""
    message.content = edited_text
    if text_entities is not None:
        message.text_entities = normalize_text_entities(text_entities) or []
    if edited_at is not None:
        message.edited_at = edited_at
    session.add(message)
    await bump_conversation_revision(session, conversation)
    await _append_message_action_session_events(
        session=session,
        workspace=workspace,
        conversation=conversation,
        message=message,
        event_type=(
            "customer_message_edited"
            if message.sender_type == SenderType.CUSTOMER.value
            else "agent_message_edited"
        ),
        direction="inbound" if message.sender_type == SenderType.CUSTOMER.value else "outbound",
        text=edited_text,
        payload={
            "action": "edited",
            "telegram_message_id": message.telegram_message_id,
            "edited_at": (edited_at or message.edited_at).isoformat()
            if (edited_at or message.edited_at)
            else None,
            "text_entities": message.text_entities,
        },
    )

    await _refresh_reply_state(
        session=session,
        conversation=conversation,
    )
    await session.commit()

    from app.api.routes.ws import manager as ws_manager

    await ws_manager.broadcast(
        workspace.id,
        {
            "type": "message_edited",
            "data": {
                "message_id": message.id,
                "conversation_id": conversation.id,
                "content": message.content,
                "text_entities": message.text_entities,
                "edited_at": message.edited_at.isoformat() if message.edited_at else None,
                "conversation_revision": conversation.message_revision,
            },
        },
    )

    return CanonicalIntakeResult(
        status="persisted",
        message_id=message.id,
        conversation_id=conversation.id,
        reply_generation_triggered=False,
        action="edited",
        direction="outbound" if message.sender_type == SenderType.SELLER.value else "inbound",
        is_customer_inbound=message.sender_type == SenderType.CUSTOMER.value,
        is_new_or_recovered=False,
        requires_conversation_reconcile=True,
        requires_reply_evaluation=False,
    )


async def process_message_delete(
    *,
    session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    message: Message,
) -> CanonicalIntakeResult:
    """Apply a delete event and return the canonical intake outcome."""
    message.content = "[deleted]"
    message.is_deleted = True
    session.add(message)
    await bump_conversation_revision(session, conversation)
    await _append_message_action_session_events(
        session=session,
        workspace=workspace,
        conversation=conversation,
        message=message,
        event_type=(
            "customer_message_deleted"
            if message.sender_type == SenderType.CUSTOMER.value
            else "agent_message_deleted"
        ),
        direction="inbound" if message.sender_type == SenderType.CUSTOMER.value else "outbound",
        text="[deleted]",
        payload={
            "action": "deleted",
            "telegram_message_id": message.telegram_message_id,
        },
    )

    await _refresh_reply_state(
        session=session,
        conversation=conversation,
    )
    await session.commit()

    from app.api.routes.ws import manager as ws_manager

    await ws_manager.broadcast(
        workspace.id,
        {
            "type": "message_deleted",
            "data": {
                "message_id": message.id,
                "conversation_id": conversation.id,
                "conversation_revision": conversation.message_revision,
            },
        },
    )

    return CanonicalIntakeResult(
        status="persisted",
        message_id=message.id,
        conversation_id=conversation.id,
        reply_generation_triggered=False,
        action="deleted",
        direction="outbound" if message.sender_type == SenderType.SELLER.value else "inbound",
        is_customer_inbound=message.sender_type == SenderType.CUSTOMER.value,
        is_new_or_recovered=False,
        requires_conversation_reconcile=True,
        requires_reply_evaluation=False,
    )


async def _append_message_action_session_events(
    *,
    session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    message: Message,
    event_type: str,
    direction: str,
    text: str,
    payload: dict[str, Any],
) -> None:
    rows = (
        await session.execute(
            select(AgentSession).where(
                AgentSession.workspace_id == workspace.id,
                AgentSession.conversation_id == conversation.id,
            )
        )
    ).scalars().all()
    if not rows:
        return
    service = AgentSessionService(session)
    digest_seed = {
        "event_type": event_type,
        "text": text,
        "payload": payload,
    }
    digest = hashlib.sha256(str(digest_seed).encode("utf-8")).hexdigest()[:16]
    for agent_session in rows:
        await service.append_event(
            agent_session_id=agent_session.id,
            workspace_id=workspace.id,
            conversation_id=conversation.id,
            agent_id=agent_session.agent_id,
            event_type=event_type,
            direction=direction,
            message_id=message.id,
            text=text,
            payload={
                "message_id": message.id,
                "channel": conversation.channel or message.channel,
                **payload,
            },
            idempotency_key=(
                f"message:{message.id}:{event_type}:agent:{agent_session.agent_id}:{digest}"
            ),
        )


async def _handle_seller_message(
    *,
    session: AsyncSession,
    workspace: Workspace,
    conversation,
    message,
    conversation_turn_runner,
) -> None:
    """Agent/operator message: close active turn sessions for this conversation."""
    await ConversationTurnSessionService(session).complete_active_turns_for_agent_message(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
    )

    await session.commit()

    await conversation_turn_runner.record_agent_message(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        message_id=message.id,
    )


async def _notify_owner_dnc_silent(session, workspace, conversation, customer) -> None:
    """Raise one owner-bot card (deduped per conversation per day) when a
    do-not-contact customer messages and the seller stays silent. Non-fatal:
    the silence is the guarantee — a card failure must never crash intake."""
    from app.modules.crm_connector.owner_cards import queue_crm_owner_notification

    day = datetime.now(UTC).strftime("%Y%m%d")
    try:
        await queue_crm_owner_notification(
            session,
            workspace_id=workspace.id,
            title="Bog'lanmaslik mijoz yozdi",
            summary=(
                "Bu mijozda 'Bog'lanmaslik' (do-not-contact) belgisi bor, shuning "
                "uchun agent javob bermadi."
            ),
            recommended_action="Suhbatni o'zingiz davom ettiring yoki belgini oling.",
            idempotency_key=f"dnc-silent:{workspace.id}:{conversation.id}:{day}",
            conversation_id=conversation.id,
        )
    except Exception:
        logger.warning("dnc-silent owner card failed (non-fatal)", exc_info=True)


async def _handle_customer_message(
    *,
    session: AsyncSession,
    workspace: Workspace,
    conversation,
    message,
    customer,
    conversation_turn_runner,
    raw_payload: dict,
) -> bool:
    """Customer message: prepare media evidence and enter the reply lifecycle."""
    if getattr(customer, "opted_out", False):
        # Bog'lanmaslik / do-not-contact: the agent stops engaging this lead.
        # Stay SILENT (no hot-path run, no turn enqueued) and card the owner once.
        await _notify_owner_dnc_silent(session, workspace, conversation, customer)
        await session.commit()
        return False

    text = raw_payload.get("text", "")
    media_type = message.media_type

    classify_text = text

    if media_type and not classify_text.strip():
        classify_text, _ = await _extract_media_semantics(
            media_type,
            raw_payload.get("mediaBytes"),
            session=session,
            workspace_id=workspace.id,
            message_id=message.id,
        )
        if classify_text and classify_text != text:
            message.content = classify_text
            session.add(message)

    classification = classify_local(
        classify_text,
        media_type=media_type,
    )

    reply_generation_triggered = classification.should_enter_reply_lifecycle
    if reply_generation_triggered:
        hot_path = await AgentSessionHotPathService(session).record_customer_message_and_prepare_run(
            workspace_id=workspace.id,
            conversation_id=conversation.id,
            customer_id=getattr(customer, "id", None),
            channel=getattr(conversation, "channel", None) or message.channel or "telegram_dm",
            message_id=message.id,
            text=classify_text,
            trigger_telemetry=raw_payload.get("triggerTelemetry"),
            payload={
                "telegram_message_id": message.telegram_message_id,
                "media_type": media_type,
                "reply_to_msg_id": message.reply_to_msg_id,
                "forward_from_name": message.forward_from_name,
                "forward_date": message.forward_date.isoformat()
                if message.forward_date
                else None,
                "grouped_id": message.grouped_id,
                "external_message_id": message.external_message_id,
                "external_author_id": message.external_author_id,
                "external_parent_id": message.external_parent_id,
                "text_entities": message.text_entities,
                "reactions": message.reactions,
            },
        )
        await ConversationTurnSessionService(session).append_customer_message(
            workspace_id=workspace.id,
            conversation=conversation,
            customer=customer,
            message=message,
            agent_id=hot_path.agent_id if hot_path is not None else None,
        )
        await session.commit()
        await conversation_turn_runner.enqueue_message(
            workspace_id=workspace.id,
            conversation_id=conversation.id,
            message_id=message.id,
            trigger_telemetry=raw_payload.get("triggerTelemetry"),
        )
    else:
        await session.commit()

    return reply_generation_triggered


async def _extract_media_semantics(
    media_type: str,
    media_bytes_b64: str | None,
    *,
    session: AsyncSession,
    workspace_id: int,
    message_id: int,
) -> tuple[str, str | None]:
    """Prepare media evidence text for classification and extraction."""
    if media_bytes_b64 and media_type in _VOICE_MEDIA_TYPES:
        import base64

        from app.modules.commercial_spine.llm_gateway import LLMGateway
        from app.modules.commercial_spine.repository import CommercialSpineRepository
        from app.modules.extraction_runtime.media_semantics import (
            normalize_voice_message,
        )
        try:
            audio_bytes = base64.b64decode(media_bytes_b64)
            normalized = await normalize_voice_message(
                audio_bytes,
                gateway=LLMGateway(repository=CommercialSpineRepository(session)),
                workspace_id=workspace_id,
                correlation_id=f"media:intake:{message_id}",
                source_refs=[f"message:{message_id}"],
            )
            logger.info(
                "Voice semantic extraction produced %d chars",
                len(normalized.text),
            )
            return normalized.text, "audio/ogg"
        except Exception:
            logger.warning("Voice semantic extraction failed, using placeholder")

    if media_bytes_b64 and media_type in _IMAGE_MEDIA_TYPES:
        import base64

        from app.modules.commercial_spine.llm_gateway import LLMGateway
        from app.modules.commercial_spine.repository import CommercialSpineRepository
        from app.modules.extraction_runtime.media_semantics import (
            normalize_image_message,
        )
        try:
            image_bytes = base64.b64decode(media_bytes_b64)
            normalized = await normalize_image_message(
                image_bytes,
                gateway=LLMGateway(repository=CommercialSpineRepository(session)),
                workspace_id=workspace_id,
                correlation_id=f"media:intake:{message_id}",
                source_refs=[f"message:{message_id}"],
            )
            logger.info(
                "Image semantic extraction produced: %s",
                normalized.text[:80],
            )
            return normalized.text, "image/jpeg"
        except Exception:
            logger.warning("Image semantic extraction failed, using placeholder")

    # Fallback: placeholder text for unsupported/failed media
    from app.modules.extraction_runtime.media_semantics import normalize_media_placeholder
    result = normalize_media_placeholder(media_type)
    logger.info("Media placeholder for %s: %s", media_type, result.text)
    return result.text, None


async def _broadcast_new_message(
    *,
    session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    message: Message,
) -> None:
    from app.api.routes.ws import manager as ws_manager
    from app.services.media_urls import (
        build_message_media_preview_url,
        canonicalize_message_media_url,
    )

    unread_count_result = await session.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id == conversation.id,
            Message.is_read.is_(False),
            Message.sender_type == SenderType.CUSTOMER.value,
        )
    )
    unread_count = int(unread_count_result.scalar() or 0)
    media_full_url = canonicalize_message_media_url(
        media_url=message.media_url,
        telegram_chat_id=conversation.telegram_chat_id,
        telegram_message_id=message.telegram_message_id,
        media_type=message.media_type,
    )
    media_preview_url = build_message_media_preview_url(
        telegram_chat_id=conversation.telegram_chat_id,
        telegram_message_id=message.telegram_message_id,
        media_type=message.media_type,
    )
    delivery_runtime = await session.scalar(
        select(DeliveryRuntime).where(
            DeliveryRuntime.workspace_id == workspace.id,
            DeliveryRuntime.message_id == message.id,
        )
    )
    delivery_runtime_payload = build_delivery_runtime_response(delivery_runtime)

    await ws_manager.broadcast(
        workspace.id,
        {
            "type": "new_message",
            "data": {
                "conversation_id": conversation.id,
                "conversation": {
                    "id": conversation.id,
                    "customer_id": conversation.customer_id,
                    "customer_name": customer.display_name,
                    "channel": conversation.channel,
                    "telegram_chat_id": conversation.telegram_chat_id,
                    "external_chat_id": conversation.external_chat_id,
                    "external_thread_id": conversation.external_thread_id,
                    "pipeline_stage": resolved_pipeline_stage(conversation),
                    "override_mode": conversation.override_mode,
                    "summary": conversation.summary,
                    "needs_attention": conversation.needs_attention,
                    "read_outbox_max_id": conversation.read_outbox_max_id,
                    "last_message_at": (
                        conversation.last_message_at.isoformat()
                        if conversation.last_message_at else None
                    ),
                    "unread_count": unread_count,
                    "created_at": conversation.created_at.isoformat(),
                    "last_message_text": (message.content or "")[:100],
                    "contact_type": customer.contact_type,
                    "has_pending_reply": False,
                    "latest_reply_confidence": None,
                },
                "message": {
                    "id": message.id,
                    "conversation_id": conversation.id,
                    "channel": conversation.channel,
                    "sender_type": message.sender_type,
                    "content": message.content,
                    "media_type": message.media_type,
                    "media_url": media_full_url,
                    "media_full_url": media_full_url,
                    "media_preview_url": media_preview_url,
                    "telegram_message_id": message.telegram_message_id,
                    "is_read": message.is_read,
                    "created_at": message.created_at.isoformat(),
                    "reply_to_msg_id": message.reply_to_msg_id,
                    "forward_from_name": message.forward_from_name,
                    "forward_date": (
                        message.forward_date.isoformat()
                        if message.forward_date else None
                    ),
                    "edited_at": (
                        message.edited_at.isoformat()
                        if message.edited_at else None
                    ),
                    "is_deleted": message.is_deleted,
                    "media_metadata": message.media_metadata,
                    "text_entities": message.text_entities,
                    "reactions": message.reactions,
                    "external_message_id": message.external_message_id,
                    "external_author_id": message.external_author_id,
                    "external_parent_id": message.external_parent_id,
                    "client_message_uuid": message.client_message_uuid,
                    "delivery_state": message.delivery_state,
                    "delivery_runtime": (
                        delivery_runtime_payload.model_dump(mode="json")
                        if delivery_runtime_payload else None
                    ),
                    "conversation_seq": message.conversation_seq,
                    "conversation_revision": conversation.message_revision,
                    "grouped_id": message.grouped_id,
                    "telegram_timestamp": (
                        message.telegram_timestamp.isoformat()
                        if message.telegram_timestamp else None
                    ),
                    "telegram_chat_id": conversation.telegram_chat_id,
                },
            },
        },
    )
