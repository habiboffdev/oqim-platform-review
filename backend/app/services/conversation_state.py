from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, literal

from app.models.conversation import Conversation
from app.models.message import Message, SenderType
from app.schemas.crm import CanonicalPipelineStage, CrmStageProjection

FIELD_PROVENANCE_SELLER = "seller"
FIELD_PROVENANCE_AI = "ai"
FIELD_PROVENANCE_SYSTEM = "system"

MEDIA_READINESS_READY = "ready"
MEDIA_READINESS_PENDING = "pending"
MEDIA_READINESS_UNAVAILABLE = "unavailable"
MEDIA_READINESS_NOT_APPLICABLE = "not_applicable"
TELEGRAM_CONVERSATION_CHANNELS = frozenset({"telegram_dm", "dm"})
TELEGRAM_VISIBLE_GAP_THRESHOLD = 25
MEDIA_PREVIEW_LABELS: dict[str, str] = {
    "photo": "Rasm",
    "video": "Video",
    "video_note": "Video xabar",
    "voice": "Ovozli xabar",
    "audio": "Audio",
    "sticker": "Stiker",
    "gif": "GIF",
    "document": "Fayl",
    "contact": "Kontakt",
    "location": "Lokatsiya",
}
CANONICAL_PIPELINE_STAGES: tuple[CanonicalPipelineStage, ...] = (
    "new",
    "qualified",
    "negotiation",
    "proposal",
    "payment",
    "delivery",
    "waiting",
    "won",
    "lost",
    "manual_review",
)
PIPELINE_STAGE_ALIASES: dict[str, CanonicalPipelineStage] = {
    "lead": "new",
    "talking": "qualified",
    "considering": "qualified",
    "interested": "qualified",
    "negotiating": "negotiation",
    "quote": "proposal",
    "quoted": "proposal",
    "proposal_sent": "proposal",
    "checkout": "payment",
    "payment_pending": "payment",
    "paid": "payment",
    "paid_candidate": "payment",
    "delivery_pending": "delivery",
    "delivering": "delivery",
    "cold": "lost",
}


class ConversationSyncWatermarks(BaseModel):
    model_config = ConfigDict(extra="allow")

    oldest_external_message_id: str | None = None
    latest_external_message_id: str | None = None
    oldest_complete: bool = False
    latest_complete: bool = False


class ConversationDialogState(BaseModel):
    model_config = ConfigDict(extra="allow")

    telegram_unread_count: int = 0
    title: str | None = None
    top_message_id: int | None = None
    last_message_text: str | None = None
    last_message_is_outgoing: bool = False
    last_message_date: str | None = None


class ConversationSyncState(BaseModel):
    model_config = ConfigDict(extra="allow")

    watermarks: ConversationSyncWatermarks | None = None
    dialog: ConversationDialogState | None = None
    last_recovered_tail_trigger_message_id: int | None = None
    pending_edits: dict[str, dict[str, str | None]] | None = None


class ConversationFollowUpState(BaseModel):
    model_config = ConfigDict(extra="allow")

    obligation_id: int | None = None
    status: str | None = None
    kind: str | None = None
    due_at: str | None = None
    reason_code: str | None = None
    waiting_for: str | None = None
    task_id: int | None = None
    task_status: str | None = None
    source_evidence_ref: str | None = None


class ConversationReplyState(BaseModel):
    model_config = ConfigDict(extra="allow")

    unresolved_customer_message_ids: list[int] = []
    latest_unresolved_customer_message_id: int | None = None
    seller_responded_after_latest_customer: bool = False
    seller_response_message_id: int | None = None


class CustomerConversationState(BaseModel):
    model_config = ConfigDict(extra="allow")

    sync: ConversationSyncState | None = None
    follow_up: ConversationFollowUpState | None = None
    reply: ConversationReplyState | None = None
    pipeline_stage: str = "new"
    last_intent: str | None = None
    products_interested: list[str] = Field(default_factory=list)
    urgency: bool | None = None
    last_updated: str | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConversationRepairRequest:
    reason: str
    before_external_message_id: str | None = None
    after_external_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationTailProjection:
    schema_version: str
    status: str
    source: str
    latest_message_text: str | None
    latest_message_at: datetime | None
    unread_count: int
    unread_source: str
    latest_conversation_seq: int
    latest_conversation_revision: int
    gap: ConversationRepairRequest | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "source": self.source,
            "latest_message_text": self.latest_message_text,
            "latest_message_at": self.latest_message_at,
            "unread_count": self.unread_count,
            "unread_source": self.unread_source,
            "latest_conversation_seq": self.latest_conversation_seq,
            "latest_conversation_revision": self.latest_conversation_revision,
            "gap": (
                {
                    "reason": self.gap.reason,
                    "before_external_message_id": self.gap.before_external_message_id,
                    "after_external_message_id": self.gap.after_external_message_id,
                }
                if self.gap
                else None
            ),
        }


def get_customer_conversation_state(conversation: Conversation) -> CustomerConversationState:
    raw_crm_state = getattr(conversation, "crm_state", None)
    raw_state = raw_crm_state if isinstance(raw_crm_state, dict) else {}
    return CustomerConversationState.model_validate(raw_state)


def normalize_pipeline_stage(raw_stage: str | None) -> CanonicalPipelineStage:
    if not raw_stage:
        return "new"
    normalized = str(raw_stage).strip().lower()
    if normalized in CANONICAL_PIPELINE_STAGES:
        return normalized  # type: ignore[return-value]
    return PIPELINE_STAGE_ALIASES.get(normalized, "new")


def project_crm_stage(conversation: Conversation) -> CrmStageProjection:
    raw_crm_state = getattr(conversation, "crm_state", None)
    raw_state = raw_crm_state if isinstance(raw_crm_state, dict) else {}
    raw_stage = raw_state.get("pipeline_stage")
    source: Literal["crm_state", "defaulted"] = "crm_state"
    if not isinstance(raw_stage, str) or not raw_stage.strip():
        raw_stage = None
        source = "defaulted"

    state = get_customer_conversation_state(conversation)
    stage = normalize_pipeline_stage(raw_stage)
    raw_stage_text = str(raw_stage).strip() if raw_stage else None
    lead_score = resolved_lead_score(conversation)
    return CrmStageProjection(
        stage=stage,
        source=source,
        raw_stage=raw_stage_text,
        normalized_from=raw_stage_text if raw_stage_text and raw_stage_text != stage else None,
        confidence=lead_score,
        last_intent=state.last_intent,
        products_interested=resolved_products_interested(conversation),
        urgency=resolved_urgency(conversation),
        needs_attention=bool(getattr(conversation, "needs_attention", False)),
        last_updated=_parse_state_datetime(state.last_updated),
        field_provenance=state.field_provenance,
    )


def resolved_pipeline_stage(conversation: Conversation) -> str:
    return project_crm_stage(conversation).stage


def resolved_products_interested(conversation: Conversation) -> list[str]:
    state = get_customer_conversation_state(conversation)
    return state.products_interested if isinstance(state.products_interested, list) else []


def resolved_urgency(conversation: Conversation) -> bool | None:
    state = get_customer_conversation_state(conversation)
    return state.urgency if isinstance(state.urgency, bool) else None


def resolved_lead_score(conversation: Conversation) -> float | None:
    state = get_customer_conversation_state(conversation)
    raw_score = state.model_extra.get("lead_score") if state.model_extra else None
    if raw_score is None:
        return None
    try:
        return float(raw_score)
    except (TypeError, ValueError):
        return None


def resolved_pipeline_stage_expr(model: type[Conversation] = Conversation):
    raw_stage = func.lower(func.trim(func.coalesce(model.crm_state["pipeline_stage"].astext, "")))
    alias_branches = tuple(
        (raw_stage == alias, literal(canonical))
        for alias, canonical in PIPELINE_STAGE_ALIASES.items()
    )
    return case(
        (raw_stage.in_(CANONICAL_PIPELINE_STAGES), raw_stage),
        *alias_branches,
        else_=literal("new"),
    )


def project_dialog_unread_count(conversation: Conversation) -> int | None:
    """Return adapter-projected unread count when canonical state has it."""
    state = get_customer_conversation_state(conversation)
    dialog_state = state.sync.dialog if state.sync else None
    if dialog_state is None:
        return None
    try:
        return max(int(dialog_state.telegram_unread_count), 0)
    except (TypeError, ValueError):
        return None


def project_dialog_last_message_text(
    conversation: Conversation,
    *,
    local_text: str | None,
    local_at: datetime | None,
) -> str | None:
    """Choose the canonical dialog preview when it is newer than local rows.

    The route can still fetch local rows for pagination/query purposes, but
    the projected Telegram dialog state owns the latest preview when it has a
    fresher adapter timestamp. This keeps chat-list previews aligned with the
    state plane while route-time repair remains disabled.
    """
    state = get_customer_conversation_state(conversation)
    dialog_state = state.sync.dialog if state.sync else None
    if dialog_state is None or not dialog_state.last_message_text:
        return local_text

    projected_text = dialog_state.last_message_text[:100]
    projected_at = _parse_state_datetime(dialog_state.last_message_date)
    if local_at is None:
        return projected_text
    if projected_at is None:
        if conversation.last_message_at and conversation.last_message_at > local_at:
            return projected_text
        return local_text
    if projected_at >= _ensure_aware_utc(local_at):
        return projected_text
    return local_text


def project_message_preview_text(
    text: str | None,
    *,
    media_type: str | None = None,
) -> str | None:
    """Return the chat-list preview for a canonical message row.

    Empty text on a media message is not "no message"; it is a real Telegram
    tail with media-only content. Keep this deterministic because it is display
    projection, not semantic intent classification.
    """
    stripped = (text or "").strip()
    if stripped:
        return stripped[:100]
    if not media_type:
        return "" if text is not None else None
    return MEDIA_PREVIEW_LABELS.get(str(media_type).strip().lower(), "Media")


def project_conversation_tail(
    conversation: Conversation,
    *,
    local_text: str | None,
    local_at: datetime | None,
    local_media_type: str | None = None,
    db_unread_count: int,
    messages: Sequence[Message] | None = None,
) -> ConversationTailProjection:
    """Project list/detail tail truth from one place.

    Dialog state may be ahead of local message rows after sidecar downtime or
    dialog-shell sync. This projection makes that mismatch explicit so the
    frontend can render stale/gap states instead of guessing.
    """
    state = get_customer_conversation_state(conversation)
    dialog_state = state.sync.dialog if state.sync else None
    dialog_at = _parse_state_datetime(dialog_state.last_message_date) if dialog_state else None
    dialog_text = dialog_state.last_message_text[:100] if dialog_state and dialog_state.last_message_text else None

    unread_count = max(int(db_unread_count or 0), 0)
    unread_source = "local_rows"
    telegram_unread = project_dialog_unread_count(conversation)
    if telegram_unread is not None and conversation.channel in TELEGRAM_CONVERSATION_CHANNELS:
        unread_count = telegram_unread
        unread_source = "dialog_projection"

    local_preview_text = project_message_preview_text(
        local_text,
        media_type=local_media_type,
    )
    latest_text = project_dialog_last_message_text(
        conversation,
        local_text=local_preview_text,
        local_at=local_at,
    )
    latest_at = local_at
    source = "local_message" if local_preview_text is not None else "none"
    if dialog_text and latest_text == dialog_text:
        source = "dialog_projection"
        latest_at = dialog_at or conversation.last_message_at or local_at
    elif conversation.summary and latest_text == local_preview_text and _projection_ahead_of_local(conversation, local_at):
        source = "summary_fallback"
        latest_text = conversation.summary[:100]
        latest_at = conversation.last_message_at

    gap = project_visible_gap_repair_request(conversation, messages=messages) if messages is not None else None
    if gap is None and _projection_ahead_of_local(conversation, local_at):
        gap = ConversationRepairRequest(reason="conversation_preview_ahead")

    status = "ok"
    if gap is not None:
        status = "gap_detected" if gap.reason == "visible_telegram_id_gap" else "stale"
    elif source in {"dialog_projection", "summary_fallback"} and _projection_ahead_of_local(conversation, local_at):
        status = "stale"

    return ConversationTailProjection(
        schema_version="conversation_tail.v1",
        status=status,
        source=source,
        latest_message_text=latest_text,
        latest_message_at=latest_at,
        unread_count=unread_count,
        unread_source=unread_source,
        latest_conversation_seq=int(conversation.message_sequence or 0),
        latest_conversation_revision=int(conversation.message_revision or 0),
        gap=gap,
    )


def is_repairable_channel_conversation(conversation: Conversation) -> bool:
    """Return whether explicit channel repair may fetch remote history.

    Normal reads should not mutate truth by default, but fallback/repair paths
    still need one canonical definition of "this conversation can be repaired
    from the Telegram adapter." Keeping this next to sync watermarks avoids
    route-local Telegram heuristics becoming a second state model.
    """
    return (
        conversation.channel in TELEGRAM_CONVERSATION_CHANNELS
        and (conversation.external_chat_id is not None or conversation.telegram_chat_id is not None)
    )


def external_cursor_for_message(message: Message) -> str | None:
    """Return the adapter cursor for a persisted message, if one exists."""
    if message.telegram_message_id is not None:
        return str(message.telegram_message_id)
    if message.external_message_id:
        return message.external_message_id
    return None


def has_exhausted_older_history(
    conversation: Conversation,
    *,
    external_cursor: str | None,
) -> bool:
    """Return whether state proves no older remote history before cursor."""
    if not external_cursor:
        return False
    state = get_customer_conversation_state(conversation)
    watermarks = state.sync.watermarks if state.sync else None
    if watermarks is None or not watermarks.oldest_complete:
        return False
    oldest_external = watermarks.oldest_external_message_id
    if oldest_external is None:
        return False
    oldest_numeric = _safe_int(oldest_external)
    cursor_numeric = _safe_int(external_cursor)
    if oldest_numeric is not None and cursor_numeric is not None:
        return cursor_numeric <= oldest_numeric
    return external_cursor == oldest_external


def should_surface_older_history_from_state(
    conversation: Conversation,
    *,
    page_has_older: bool,
    oldest_message: Message | None,
) -> bool:
    """Project `has_older` from local page data plus sync watermarks."""
    if page_has_older:
        return True
    if oldest_message is None or not is_repairable_channel_conversation(conversation):
        return False
    external_cursor = external_cursor_for_message(oldest_message)
    return bool(
        external_cursor
        and not has_exhausted_older_history(
            conversation,
            external_cursor=external_cursor,
        )
    )


def message_effective_time(message: Message) -> datetime | None:
    return message.telegram_timestamp or message.created_at


def newer_external_cursor_after_visible_gap(
    messages: Sequence[Message],
    *,
    threshold: int = TELEGRAM_VISIBLE_GAP_THRESHOLD,
) -> str | None:
    previous_id: int | None = None
    for message in messages:
        current_id = message.telegram_message_id
        if current_id is None and message.external_message_id:
            current_id = _safe_int(message.external_message_id)
        if current_id is None:
            continue
        if previous_id is not None and current_id - previous_id > threshold:
            return str(current_id)
        previous_id = current_id
    return None


def project_visible_gap_repair_request(
    conversation: Conversation,
    *,
    messages: Sequence[Message],
) -> ConversationRepairRequest | None:
    if not messages or not is_repairable_channel_conversation(conversation):
        return None
    gap_cursor = newer_external_cursor_after_visible_gap(messages)
    if not gap_cursor:
        return None
    return ConversationRepairRequest(
        reason="visible_telegram_id_gap",
        before_external_message_id=gap_cursor,
    )


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_state_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_aware_utc(parsed)


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _projection_ahead_of_local(conversation: Conversation, local_at: datetime | None) -> bool:
    if conversation.last_message_at is None:
        return False
    if local_at is None:
        return True
    return _ensure_aware_utc(conversation.last_message_at) > _ensure_aware_utc(local_at)


def set_customer_conversation_state(
    conversation: Conversation,
    state: CustomerConversationState,
) -> None:
    state.pipeline_stage = normalize_pipeline_stage(state.pipeline_stage)
    serialized = state.model_dump(exclude_none=True)
    if state.sync is not None:
        serialized["sync"] = state.sync.model_dump(exclude_none=True)
    if state.follow_up is not None:
        serialized["follow_up"] = state.follow_up.model_dump(exclude_none=False)
    conversation.crm_state = serialized or None
    if hasattr(conversation, "pipeline_stage"):
        conversation.pipeline_stage = state.pipeline_stage
    if hasattr(conversation, "products_mentioned"):
        conversation.products_mentioned = list(state.products_interested)


def derive_conversation_reply_state(messages: Sequence[Message]) -> ConversationReplyState | None:
    """Derive unresolved-tail state from a canonical message batch.

    Telegram can deliver several same-second bubbles to the backend out of
    arrival order. When channel order hints exist, Telegram/external ids own the
    tie-breaker; otherwise we preserve the caller's positional order for legacy
    backfill cases where ids are not chronological truth.
    """
    ordered_messages = _reply_state_messages_in_order(messages)
    relevant_messages = [
        message
        for message in ordered_messages
        if not getattr(message, "is_deleted", False)
        and getattr(message, "sender_type", None) in (SenderType.CUSTOMER.value, SenderType.SELLER.value)
    ]
    if not relevant_messages:
        return None

    latest_customer_index = None
    latest_seller_index = None
    for index, message in enumerate(relevant_messages):
        if message.sender_type == SenderType.CUSTOMER.value:
            latest_customer_index = index
        elif message.sender_type == SenderType.SELLER.value:
            latest_seller_index = index

    if latest_customer_index is None:
        return ConversationReplyState()

    latest_seller = (
        relevant_messages[latest_seller_index] if latest_seller_index is not None else None
    )

    unresolved_customer_message_ids = [
        message.id
        for index, message in enumerate(relevant_messages)
        if message.sender_type == SenderType.CUSTOMER.value
        and (latest_seller_index is None or index > latest_seller_index)
    ]
    seller_responded = (
        latest_seller_index is not None
        and latest_seller_index > latest_customer_index
    )
    latest_seller_id = latest_seller.id if latest_seller is not None else None
    return ConversationReplyState(
        unresolved_customer_message_ids=unresolved_customer_message_ids,
        latest_unresolved_customer_message_id=(
            unresolved_customer_message_ids[-1] if unresolved_customer_message_ids else None
        ),
        seller_responded_after_latest_customer=seller_responded,
        seller_response_message_id=latest_seller_id if seller_responded else None,
    )


def _reply_state_messages_in_order(messages: Sequence[Message]) -> list[Message]:
    items = list(messages)
    if not any(_has_external_order_hint(message) for message in items):
        return items
    return sorted(items, key=_reply_state_order_key)


def _has_external_order_hint(message: Message) -> bool:
    return any(
        getattr(message, attr, None) is not None
        for attr in (
            "telegram_timestamp",
            "telegram_message_id",
            "external_message_id",
            "conversation_seq",
        )
    )


def _reply_state_order_key(message: Message) -> tuple[datetime, int, int, int]:
    observed_at = (
        getattr(message, "telegram_timestamp", None)
        or getattr(message, "created_at", None)
        or datetime.min.replace(tzinfo=UTC)
    )
    return (
        _ensure_aware_utc(observed_at),
        _message_numeric_order_hint(message),
        _safe_int(getattr(message, "conversation_seq", None)) or 0,
        int(getattr(message, "id", 0) or 0),
    )


def _message_numeric_order_hint(message: Message) -> int:
    return (
        _safe_int(getattr(message, "telegram_message_id", None))
        or _safe_int(getattr(message, "external_message_id", None))
        or 0
    )


async def sync_media_readiness_for_conversation(
    *,
    session: Any,
    conversation: Conversation,
    window: int = 50,
) -> bool | None:
    """Re-aggregate state.media_ready from recent message hydration_status.

    Fast path for in-flight hydration completion — avoids the LLM call in
    refresh_customer_conversation_state. Respects seller-override provenance:
    if the field is seller-owned, returns the existing value untouched.
    Returns the newly-applied bool, or None when no AI-relevant media is
    present in the window.
    """
    from sqlalchemy import select as _select

    result = await session.execute(
        _select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(
            Message.telegram_timestamp.desc().nullslast(),
            Message.created_at.desc(),
            Message.id.desc(),
        )
        .limit(window)
    )
    recent = list(reversed(result.scalars().all()))
    status = derive_media_readiness_status_from_messages(recent)
    derived = _media_readiness_bool(status)

    state = get_customer_conversation_state(conversation)
    apply_derived_field_update(
        state,
        field="media_readiness_status",
        value=status,
        source=FIELD_PROVENANCE_SYSTEM,
    )
    if derived is None:
        set_customer_conversation_state(conversation, state)
        session.add(conversation)
        return None
    apply_derived_field_update(
        state,
        field="media_ready",
        value=derived,
        source=FIELD_PROVENANCE_SYSTEM,
    )
    set_customer_conversation_state(conversation, state)
    session.add(conversation)
    return derived


def derive_media_readiness_from_messages(messages: Sequence[Message]) -> bool | None:
    """Aggregate message-level hydration_status into a conversation-level fact.

    Returns True when every AI-relevant media message in the batch has reached
    hydration_status == "hydrated". Returns False when any AI-relevant media
    message is still pending, unavailable, or otherwise unready. Returns None
    when no AI-relevant media is present — absence of signal, not a fact.
    """
    return _media_readiness_bool(derive_media_readiness_status_from_messages(messages))


def derive_media_readiness_status_from_messages(messages: Sequence[Message]) -> str:
    """Return the explicit canonical readiness status for AI-relevant media."""
    has_relevant_media = False
    has_unavailable = False
    for message in messages:
        metadata = message.media_metadata if isinstance(message.media_metadata, dict) else None
        if not metadata or not metadata.get("ai_relevant"):
            continue
        has_relevant_media = True
        hydration_status = str(metadata.get("hydration_status") or "").strip().lower()
        if hydration_status == "hydrated":
            continue
        if hydration_status in {"unavailable", "failed"}:
            has_unavailable = True
            continue
        return MEDIA_READINESS_PENDING
    if not has_relevant_media:
        return MEDIA_READINESS_NOT_APPLICABLE
    if has_unavailable:
        return MEDIA_READINESS_UNAVAILABLE
    return MEDIA_READINESS_READY


def project_media_readiness_block_reason(state: CustomerConversationState) -> str | None:
    """Return the draft-blocking reason implied by canonical media state.

    The explicit ``media_readiness_status`` is authoritative whenever present.
    Only media that is still hydrating (``pending``) blocks a draft; ``ready``,
    ``not_applicable``, and terminal ``unavailable`` never block. Terminal
    ``unavailable`` is deliberately non-blocking: the hydration worker has given
    up, so the agent replies honestly that it could not open the media rather
    than stalling the conversation forever.

    The legacy ``media_ready`` bool is consulted ONLY when no status is present
    (state persisted before ``media_readiness_status`` existed). It must never
    override an explicit status — otherwise a bool left stale by an earlier
    window (status rolled to ``not_applicable`` while ``media_ready`` was still
    ``False``) blocks the conversation permanently. See the conv-2791 regression
    in tests/test_conversation_state_media_ready.py.
    """
    model_extra = state.model_extra or {}
    media_readiness_status = model_extra.get("media_readiness_status")
    if media_readiness_status is not None:
        if media_readiness_status == MEDIA_READINESS_PENDING:
            return "awaiting_media_hydration"
        return None

    if model_extra.get("media_ready") is False:
        return "awaiting_media_hydration"
    return None


def _media_readiness_bool(status: str) -> bool | None:
    if status == MEDIA_READINESS_READY:
        return True
    if status in {MEDIA_READINESS_PENDING, MEDIA_READINESS_UNAVAILABLE}:
        return False
    return None


async def refresh_customer_conversation_state(
    conversation: Conversation,
    *,
    messages: Sequence[Message],
) -> CustomerConversationState:
    """Refresh deterministic conversation state from a message batch.

    Semantic customer/commercial projections belong to OQIM Intelligence
    through the commercial spine; this function only maintains deterministic
    chat state.
    """
    state = get_customer_conversation_state(conversation)
    state.reply = derive_conversation_reply_state(messages)

    media_readiness_status = derive_media_readiness_status_from_messages(messages)
    apply_derived_field_update(
        state,
        field="media_readiness_status",
        value=media_readiness_status,
        source=FIELD_PROVENANCE_SYSTEM,
    )
    media_ready = _media_readiness_bool(media_readiness_status)
    if media_ready is not None:
        apply_derived_field_update(
            state,
            field="media_ready",
            value=media_ready,
            source=FIELD_PROVENANCE_SYSTEM,
        )
    elif (state.model_extra or {}).get("media_ready") is not None:
        # No AI-relevant media in the window (status == not_applicable): drop a
        # previously system/AI-derived media_ready so the canonical status and the
        # legacy bool can never contradict (a stale False would otherwise block the
        # conversation forever). apply_derived_field_update is a no-op when the
        # field is seller-owned, preserving manual overrides.
        apply_derived_field_update(
            state,
            field="media_ready",
            value=None,
            source=FIELD_PROVENANCE_SYSTEM,
        )

    set_customer_conversation_state(conversation, state)
    return state


class ConversationNextBestAction(BaseModel):
    """Projection of what the seller/agent should do next for a conversation.

    The action is an enum-like label; readiness indicates whether the action
    can be taken right now or whether it's waiting on a precondition. Reason
    explains the decision so downstream surfaces can show "why" per the PRD's
    explainability requirement.
    """

    model_config = ConfigDict(extra="forbid")

    action: str
    ready: bool
    reason: str


NBA_ATTENTION_FLAGGED = "attention_flagged"
NBA_FOLLOW_UP_DUE = "follow_up_due"
NBA_REPLY_TO_CUSTOMER = "reply_to_customer"
NBA_WAIT_ON_CUSTOMER_REPLY = "wait_on_customer_reply"
NBA_WAIT_ON_FOLLOW_UP = "wait_on_follow_up"
NBA_CONVERSATION_SETTLED = "conversation_settled"


def _parse_follow_up_due_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def project_next_best_action(
    state: CustomerConversationState,
    *,
    needs_attention: bool = False,
    override_mode: str = "auto",
    now: datetime | None = None,
) -> ConversationNextBestAction:
    """Layered projection over canonical OQIM Intelligence state.

    Priority order, highest first:
    1. Seller-flagged attention — always wins.
    2. Due sales follow-up projection — work it before anything else.
    3. Unresolved customer tail — reply (may wait on media hydration).
    4. Follow-up waiting on seller with future due-at — wait_on_follow_up.
    5. Follow-up waiting on customer — wait_on_customer_reply.
    6. Settled — nothing to do.
    """
    if needs_attention:
        return ConversationNextBestAction(
            action=NBA_ATTENTION_FLAGGED,
            ready=True,
            reason="seller_flagged_attention",
        )

    current_time = now or datetime.now(UTC)
    follow_up = state.follow_up
    follow_up_due_at = _parse_follow_up_due_at(follow_up.due_at) if follow_up else None
    follow_up_is_due = (
        follow_up is not None
        and follow_up_due_at is not None
        and follow_up_due_at <= current_time
    )

    if follow_up_is_due:
        return ConversationNextBestAction(
            action=NBA_FOLLOW_UP_DUE,
            ready=True,
            reason=f"follow_up_due:{follow_up.kind or 'unknown'}",
        )

    reply = state.reply
    has_unresolved_tail = bool(
        reply and reply.latest_unresolved_customer_message_id is not None
    )

    if has_unresolved_tail:
        if override_mode == "off":
            return ConversationNextBestAction(
                action=NBA_REPLY_TO_CUSTOMER,
                ready=False,
                reason="agent_actions_disabled",
            )
        media_block_reason = project_media_readiness_block_reason(state)
        if media_block_reason is not None:
            return ConversationNextBestAction(
                action=NBA_REPLY_TO_CUSTOMER,
                ready=False,
                reason=(
                    "waiting_on_media_hydration"
                    if media_block_reason == "awaiting_media_hydration"
                    else media_block_reason
                ),
            )
        return ConversationNextBestAction(
            action=NBA_REPLY_TO_CUSTOMER,
            ready=True,
            reason="unresolved_customer_tail",
        )

    if follow_up is not None and follow_up_due_at is not None:
        if follow_up.waiting_for == "customer":
            return ConversationNextBestAction(
                action=NBA_WAIT_ON_CUSTOMER_REPLY,
                ready=False,
                reason=f"follow_up_waiting_customer:{follow_up.kind or 'unknown'}",
            )
        return ConversationNextBestAction(
            action=NBA_WAIT_ON_FOLLOW_UP,
            ready=False,
            reason=f"follow_up_not_due_yet:{follow_up.kind or 'unknown'}",
        )

    return ConversationNextBestAction(
        action=NBA_CONVERSATION_SETTLED,
        ready=True,
        reason="no_unresolved_tail_no_follow_up",
    )


def _assign_state_field(state: CustomerConversationState, field: str, value: Any) -> None:
    """Set a field on state whether it's a declared field or an extension."""
    if field in type(state).model_fields:
        setattr(state, field, value)
        return
    extras = state.__pydantic_extra__
    if extras is None:
        state.__pydantic_extra__ = {}
        extras = state.__pydantic_extra__
    extras[field] = value


def apply_derived_field_update(
    state: CustomerConversationState,
    *,
    field: str,
    value: Any,
    source: str = FIELD_PROVENANCE_AI,
) -> bool:
    """Write an AI/system-derived value to state, respecting seller overrides.

    Returns True when the value was applied, False when skipped because the
    field is seller-owned. The provenance map records the source for every
    successful write so later readers can explain why a field is set.
    """
    if state.field_provenance.get(field) == FIELD_PROVENANCE_SELLER:
        return False
    _assign_state_field(state, field, value)
    state.field_provenance[field] = source
    return True


def apply_manual_field_override(
    state: CustomerConversationState,
    *,
    field: str,
    value: Any,
) -> None:
    """Write a seller-set value, stamping provenance as seller — always wins
    against later AI-derived updates until explicitly cleared."""
    _assign_state_field(state, field, value)
    state.field_provenance[field] = FIELD_PROVENANCE_SELLER
