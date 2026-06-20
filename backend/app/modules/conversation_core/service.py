from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import case as sql_case
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import or_
from sqlalchemy import select, update
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import NO_VALUE

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message, SenderType
from app.modules.conversation_core.hydration import choose_placeholder_candidate
from app.services.channel_media_access import build_media_runtime_metadata
from app.services.conversation_state import (
    ConversationDialogState,
    ConversationSyncState,
    get_customer_conversation_state,
    project_message_preview_text,
    set_customer_conversation_state,
)
from app.services.media_runtime import ensure_media_runtime_for_message
from app.services.media_types import normalize_media_type


@dataclass(slots=True)
class PersistMessageInput:
    """Channel-agnostic message input. Works for Telegram, Instagram, WhatsApp."""
    workspace_id: int
    sender_id: int | None
    sender_name: str
    text: str
    is_outgoing: bool
    channel: str = "telegram_dm"
    telegram_chat_id: int | None = None
    external_chat_id: str | None = None
    sender_external_id: str | None = None
    media_type: str | None = None
    media_url: str | None = None
    telegram_message_id: int | None = None
    external_message_id: str | None = None
    reply_to_msg_id: int | None = None
    forward_from_name: str | None = None
    forward_date: datetime | None = None
    media_metadata: dict | None = None
    text_entities: list[dict] | None = None
    message_ts: datetime | None = None
    grouped_id: int | None = None
    is_read: bool | None = None
    sender_username: str | None = None


@dataclass(slots=True)
class PersistMessageResult:
    customer: Customer
    conversation: Conversation
    message: Message
    is_duplicate: bool


async def persist_message(
    db: AsyncSession,
    payload: PersistMessageInput,
) -> PersistMessageResult:
    """Persist one message (any channel) and return the canonical local records."""
    normalized_channel = payload.channel or "telegram_dm"
    normalized_media_type = normalize_media_type(payload.media_type, payload.media_metadata)
    customer = await _find_or_create_customer(
        db,
        workspace_id=payload.workspace_id,
        telegram_id=payload.sender_id if normalized_channel == "telegram_dm" else None,
        external_id=(
            None
            if normalized_channel == "telegram_dm"
            else payload.sender_external_id or (
                str(payload.sender_id) if payload.sender_id is not None else None
            )
        ),
        channel=normalized_channel,
        display_name=payload.sender_name,
        telegram_username=payload.sender_username if not payload.is_outgoing else None,
    )
    conversation = await _find_or_create_conversation(
        db,
        workspace_id=payload.workspace_id,
        customer_id=customer.id,
        telegram_chat_id=payload.telegram_chat_id,
        external_chat_id=payload.external_chat_id,
        channel=normalized_channel,
    )

    # Dedup: check by telegram_message_id or external_message_id
    dedup_id = payload.telegram_message_id or payload.external_message_id
    if dedup_id:
        if payload.telegram_message_id:
            dedup_filter = Message.telegram_message_id == payload.telegram_message_id
        else:
            dedup_filter = Message.external_message_id == payload.external_message_id
        existing = await db.execute(
            select(Message).where(
                Message.conversation_id == conversation.id,
                dedup_filter,
            )
        )
        duplicate = existing.scalar_one_or_none()
        if duplicate:
            return PersistMessageResult(
                customer=customer,
                conversation=conversation,
                message=duplicate,
                is_duplicate=True,
            )

    if payload.is_outgoing and payload.telegram_message_id:
        placeholder_result = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.sender_type == SenderType.SELLER.value,
                Message.content == payload.text,
                Message.telegram_message_id.is_(None),
            )
            .order_by(Message.created_at.desc())
            .limit(5)
        )
        placeholder = choose_placeholder_candidate(
            placeholder_result.scalars().all(),
            payload.message_ts,
        )
        if placeholder is not None:
            placeholder.telegram_message_id = payload.telegram_message_id
            placeholder.external_message_id = payload.external_message_id
            placeholder.delivery_state = "confirmed"
            placeholder.telegram_timestamp = payload.message_ts
            placeholder.reply_to_msg_id = payload.reply_to_msg_id
            placeholder.forward_from_name = payload.forward_from_name
            placeholder.forward_date = payload.forward_date
            placeholder.media_metadata = _merge_outgoing_placeholder_media_metadata(
                existing=placeholder.media_metadata,
                incoming=payload.media_metadata,
            )
            placeholder.text_entities = payload.text_entities if payload.text_entities is not None else []
            placeholder.grouped_id = payload.grouped_id
            placeholder.media_type = normalized_media_type
            placeholder.media_url = payload.media_url
            placeholder.media_metadata = build_media_runtime_metadata(
                media_type=placeholder.media_type,
                content=placeholder.content,
                media_metadata=placeholder.media_metadata,
                transcription=placeholder.transcription,
                media_description=placeholder.media_description,
            )
            placeholder.is_read = True
            if payload.message_ts is not None:
                placeholder.created_at = payload.message_ts
            conversation.last_message_at = payload.message_ts or datetime.now(timezone.utc)
            _project_message_to_dialog_state(
                conversation,
                message=placeholder,
                message_ts=payload.message_ts,
                is_outgoing=True,
                is_read=True,
                text=payload.text,
                telegram_message_id=payload.telegram_message_id,
            )
            db.add(conversation)
            if isinstance(placeholder.media_type, str) and placeholder.media_type:
                await ensure_media_runtime_for_message(
                    db,
                    workspace_id=payload.workspace_id,
                    conversation=conversation,
                    message=placeholder,
                )
            await db.commit()
            await db.refresh(placeholder)
            return PersistMessageResult(
                customer=customer,
                conversation=conversation,
                message=placeholder,
                is_duplicate=False,
            )

    now = datetime.now(timezone.utc)
    sender_type = (
        SenderType.SELLER.value if payload.is_outgoing else SenderType.CUSTOMER.value
    )
    is_read = payload.is_read if payload.is_read is not None else payload.is_outgoing
    conversation_seq = await allocate_next_conversation_sequence(db, conversation)
    message = Message(
        conversation_id=conversation.id,
        channel=payload.channel,
        sender_type=sender_type,
        content=payload.text,
        media_type=normalized_media_type,
        media_url=payload.media_url,
        telegram_message_id=payload.telegram_message_id,
        external_message_id=payload.external_message_id,
        delivery_state="confirmed",
        reply_to_msg_id=payload.reply_to_msg_id,
        forward_from_name=payload.forward_from_name,
        forward_date=payload.forward_date,
        media_metadata=payload.media_metadata,
        text_entities=payload.text_entities if payload.text_entities is not None else [],
        telegram_timestamp=payload.message_ts,
        grouped_id=payload.grouped_id,
        is_read=is_read,
        conversation_seq=conversation_seq,
    )
    message.media_metadata = build_media_runtime_metadata(
        media_type=message.media_type,
        content=message.content,
        media_metadata=message.media_metadata,
    )
    db.add(message)

    conversation.last_message_at = payload.message_ts or now
    _project_message_to_dialog_state(
        conversation,
        message=message,
        message_ts=payload.message_ts,
        is_outgoing=payload.is_outgoing,
        is_read=is_read,
        text=payload.text,
        telegram_message_id=payload.telegram_message_id,
    )
    db.add(conversation)
    customer_id = customer.id
    conversation_id = conversation.id
    try:
        if message.media_type:
            await db.flush()
            await ensure_media_runtime_for_message(
                db,
                workspace_id=payload.workspace_id,
                conversation=conversation,
                message=message,
            )

        await db.commit()
    except IntegrityError:
        await db.rollback()
        duplicate = await _find_duplicate_message_after_race(
            db,
            conversation_id=conversation_id,
            telegram_message_id=payload.telegram_message_id,
            external_message_id=payload.external_message_id,
        )
        if duplicate is None:
            raise
        race_customer, race_conversation = await _load_message_context_after_race(
            db,
            customer_id=customer_id,
            conversation_id=conversation_id,
        )
        return PersistMessageResult(
            customer=race_customer or customer,
            conversation=race_conversation or conversation,
            message=duplicate,
            is_duplicate=True,
        )
    await db.refresh(message)

    return PersistMessageResult(
        customer=customer,
        conversation=conversation,
        message=message,
        is_duplicate=False,
    )


async def _find_duplicate_message_after_race(
    db: AsyncSession,
    *,
    conversation_id: int,
    telegram_message_id: int | None,
    external_message_id: str | None,
) -> Message | None:
    if telegram_message_id is not None:
        result = await db.execute(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.telegram_message_id == telegram_message_id,
            )
        )
        duplicate = result.scalar_one_or_none()
        if duplicate is not None:
            return duplicate
    if external_message_id:
        result = await db.execute(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.external_message_id == external_message_id,
            )
        )
        return result.scalar_one_or_none()
    return None


async def _load_message_context_after_race(
    db: AsyncSession,
    *,
    customer_id: int,
    conversation_id: int,
) -> tuple[Customer | None, Conversation | None]:
    customer_result = await db.execute(select(Customer).where(Customer.id == customer_id))
    conversation_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    return customer_result.scalar_one_or_none(), conversation_result.scalar_one_or_none()


async def create_seller_placeholder_message(
    db: AsyncSession,
    *,
    conversation: Conversation,
    content: str,
    client_message_uuid: str | None = None,
    delivery_state: str = "pending",
    media_type: str | None = None,
    media_metadata: dict | None = None,
    reply_to_msg_id: int | None = None,
) -> Message:
    """Create the local seller message before adapter delivery confirmation arrives."""
    now = datetime.now(timezone.utc)
    conversation_seq = await allocate_next_conversation_sequence(db, conversation)
    message = Message(
        conversation_id=conversation.id,
        channel=conversation.channel,
        sender_type=SenderType.SELLER.value,
        content=content,
        media_type=media_type,
        media_metadata=media_metadata,
        is_read=True,
        created_at=now,
        client_message_uuid=client_message_uuid,
        delivery_state=delivery_state,
        conversation_seq=conversation_seq,
        text_entities=[],
        reply_to_msg_id=reply_to_msg_id,
    )
    db.add(message)
    conversation.last_message_at = now
    _project_message_to_dialog_state(
        conversation,
        message=message,
        message_ts=now,
        is_outgoing=True,
        is_read=True,
        text=content,
        telegram_message_id=None,
    )
    await db.commit()
    await db.refresh(message)
    return message


def _project_message_to_dialog_state(
    conversation: Conversation,
    *,
    message: Message,
    message_ts: datetime | None,
    is_outgoing: bool,
    is_read: bool,
    text: str,
    telegram_message_id: int | None,
) -> None:
    """Keep the chat-list dialog projection aligned with canonical messages."""
    state = get_customer_conversation_state(conversation)
    sync_state = state.sync or ConversationSyncState()
    dialog = sync_state.dialog or ConversationDialogState()

    existing_top = int(dialog.top_message_id or 0)
    existing_at = _parse_dialog_datetime(dialog.last_message_date)
    effective_ts = message_ts or message.telegram_timestamp or message.created_at or datetime.now(timezone.utc)
    effective_ts = _ensure_aware_utc(effective_ts)
    should_replace_preview = (
        existing_at is None
        or effective_ts >= existing_at
        or (
            telegram_message_id is not None
            and existing_top > 0
            and telegram_message_id >= existing_top
        )
    )

    if should_replace_preview:
        dialog.top_message_id = telegram_message_id or dialog.top_message_id
        dialog.last_message_text = (
            project_message_preview_text(text, media_type=message.media_type) or ""
        )[:200]
        dialog.last_message_is_outgoing = bool(is_outgoing)
        dialog.last_message_date = effective_ts.isoformat()
        customer_display_name = _loaded_customer_display_name(conversation)
        if customer_display_name:
            dialog.title = customer_display_name

    unread_count = max(int(dialog.telegram_unread_count or 0), 0)
    if is_outgoing:
        unread_count = 0
    elif not is_read:
        unread_count += 1
    dialog.telegram_unread_count = unread_count

    sync_state.dialog = dialog
    state.sync = sync_state
    set_customer_conversation_state(conversation, state)


def _merge_outgoing_placeholder_media_metadata(
    *,
    existing: dict | None,
    incoming: dict | None,
) -> dict | None:
    if not existing:
        return incoming
    if not incoming:
        return existing

    merged = {**existing, **incoming}
    for key in ("url", "assetId", "outbound"):
        if key in existing:
            merged[key] = existing[key]
    return merged


def project_message_to_dialog_state(
    conversation: Conversation,
    *,
    message: Message,
    message_ts: datetime | None,
    is_outgoing: bool,
    is_read: bool,
    text: str,
    telegram_message_id: int | None,
) -> None:
    """Public state-plane helper for consumers that persist messages in batches."""
    _project_message_to_dialog_state(
        conversation,
        message=message,
        message_ts=message_ts,
        is_outgoing=is_outgoing,
        is_read=is_read,
        text=text,
        telegram_message_id=telegram_message_id,
    )


def _loaded_customer_display_name(conversation: Conversation) -> str | None:
    """Return customer name only when already loaded; never async lazy-load here."""
    try:
        loaded_customer = sa_inspect(conversation).attrs.customer.loaded_value
        if loaded_customer is NO_VALUE:
            return None
    except Exception:
        loaded_customer = getattr(conversation, "customer", None)
    display_name = getattr(loaded_customer, "display_name", None)
    return display_name if isinstance(display_name, str) and display_name else None


def _parse_dialog_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _ensure_aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def allocate_conversation_sequence_block(
    db: AsyncSession,
    conversation: Conversation,
    count: int,
) -> list[int]:
    """Reserve one or more monotonic message sequence values for a conversation."""
    if count <= 0:
        return []

    result = await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(
            message_sequence=Conversation.message_sequence + count,
            message_revision=Conversation.message_revision + count,
        )
        .returning(Conversation.message_sequence, Conversation.message_revision)
    )
    try:
        end_seq, end_revision = result.one()
        end_seq = int(end_seq)
        end_revision = int(end_revision)
    except (AttributeError, TypeError, ValueError):
        end_seq = int(result.scalar_one())
        current_revision = getattr(conversation, "message_revision", 0) or 0
        end_revision = int(current_revision) + count
    start_seq = end_seq - count + 1
    conversation.message_sequence = end_seq
    conversation.message_revision = end_revision
    return list(range(start_seq, end_seq + 1))


async def allocate_next_conversation_sequence(
    db: AsyncSession,
    conversation: Conversation,
) -> int:
    return (await allocate_conversation_sequence_block(db, conversation, 1))[0]


async def bump_conversation_revision(
    db: AsyncSession,
    conversation: Conversation,
) -> int:
    result = await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(message_revision=Conversation.message_revision + 1)
        .returning(Conversation.message_revision)
    )
    revision = int(result.scalar_one())
    conversation.message_revision = revision
    return revision


async def upsert_customer_and_conversation(
    db: AsyncSession,
    *,
    workspace_id: int,
    telegram_chat_id: int | None = None,
    external_id: str | None = None,
    external_chat_id: str | None = None,
    display_name: str = "",
    contact_type: str | None = None,
    classification_confidence: float | None = None,
    channel: str = "telegram_dm",
) -> tuple[Customer, Conversation]:
    """Shared upsert for Customer + Conversation. Used by dialog-sync and ingestion.

    - Creates customer if new, updates display_name if it was "Unknown"
    - Preserves contact_type if already classified (doesn't overwrite with None)
    - Creates conversation if new, updates last_message_at if existing
    """
    normalized_channel = str(channel or "telegram_dm").strip().lower()
    if normalized_channel == "dm":
        normalized_channel = "telegram_dm"
    customer = await _find_or_create_customer(
        db,
        workspace_id=workspace_id,
        telegram_id=telegram_chat_id if normalized_channel == "telegram_dm" else None,
        external_id=external_id or (
            str(telegram_chat_id) if telegram_chat_id is not None else None
        ),
        channel=normalized_channel,
        display_name=display_name,
        contact_type=contact_type,
        classification_confidence=classification_confidence,
    )
    conversation = await _find_or_create_conversation(
        db,
        workspace_id=workspace_id,
        customer_id=customer.id,
        telegram_chat_id=telegram_chat_id if normalized_channel == "telegram_dm" else None,
        external_chat_id=external_chat_id or (
            str(telegram_chat_id) if telegram_chat_id is not None else None
        ),
        channel=normalized_channel,
    )
    return customer, conversation


async def _find_or_create_customer(
    db: AsyncSession,
    *,
    workspace_id: int,
    telegram_id: int | None = None,
    external_id: str | None = None,
    channel: str = "telegram_dm",
    display_name: str,
    contact_type: str | None = None,
    classification_confidence: float | None = None,
    telegram_username: str | None = None,
) -> Customer:
    normalized_username = (telegram_username or "").strip().lstrip("@") or None
    normalized_channel = str(channel or "telegram_dm").strip().lower()
    if normalized_channel == "dm":
        normalized_channel = "telegram_dm"
    external_lookup = external_id or (
        str(telegram_id) if telegram_id is not None else None
    )
    if telegram_id is not None and external_lookup is not None:
        existing_result = await db.execute(
            select(Customer)
            .where(
                Customer.workspace_id == workspace_id,
                or_(
                    Customer.telegram_id == telegram_id,
                    (
                        (Customer.external_id == external_lookup)
                        & (Customer.channel == normalized_channel)
                    ),
                ),
            )
            .order_by(Customer.telegram_id.is_(None), Customer.id.asc())
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            _refresh_existing_customer(
                existing,
                display_name=display_name,
                telegram_id=telegram_id,
                external_lookup=external_lookup,
                normalized_channel=normalized_channel,
                normalized_username=normalized_username,
                contact_type=contact_type,
                classification_confidence=classification_confidence,
            )
            db.add(existing)
            await db.flush()
            return existing

    update_set: dict = {
        "display_name": sql_case(
            (Customer.display_name == "Unknown", display_name or "Unknown"),
            else_=Customer.display_name,
        ),
    }
    if normalized_username:
        update_set["telegram_username"] = normalized_username
    if contact_type is not None:
        update_set["contact_type"] = contact_type
    if classification_confidence is not None:
        update_set["classification_confidence"] = classification_confidence

    values: dict = {
        "workspace_id": workspace_id,
        "display_name": display_name or "Unknown",
        "channel": normalized_channel,
    }
    if normalized_username:
        values["telegram_username"] = normalized_username
    if contact_type is not None:
        values["contact_type"] = contact_type
    if classification_confidence is not None:
        values["classification_confidence"] = classification_confidence

    # Two dedup paths: telegram_id (legacy) or external_id + channel (multi-channel)
    if telegram_id is not None:
        values["telegram_id"] = telegram_id
        values["external_id"] = str(telegram_id)
        stmt = pg_insert(Customer).values(**values).on_conflict_do_update(
            constraint="uq_customer_workspace_telegram",
            set_=update_set,
        ).returning(Customer)
    elif external_id is not None:
        values["external_id"] = external_id
        stmt = pg_insert(Customer).values(**values).on_conflict_do_update(
            index_elements=["workspace_id", "external_id", "channel"],
            index_where=sa_text("external_id IS NOT NULL"),
            set_=update_set,
        ).returning(Customer)
    else:
        raise ValueError("Either telegram_id or external_id is required")
    result = await db.execute(stmt)
    return result.scalar_one()


def _refresh_existing_customer(
    existing: Customer,
    *,
    display_name: str,
    telegram_id: int | None,
    external_lookup: str | None,
    normalized_channel: str,
    normalized_username: str | None,
    contact_type: str | None,
    classification_confidence: float | None,
) -> None:
    if existing.display_name == "Unknown" and display_name:
        existing.display_name = display_name
    if existing.telegram_id is None:
        existing.telegram_id = telegram_id
    if not existing.external_id:
        existing.external_id = external_lookup
    existing.channel = normalized_channel
    if normalized_username and existing.telegram_username != normalized_username:
        existing.telegram_username = normalized_username
    if contact_type is not None:
        existing.contact_type = contact_type
    if classification_confidence is not None:
        existing.classification_confidence = classification_confidence


async def _find_or_create_conversation(
    db: AsyncSession,
    *,
    workspace_id: int,
    customer_id: int,
    telegram_chat_id: int | None = None,
    external_chat_id: str | None = None,
    channel: str = "telegram_dm",
) -> Conversation:
    normalized_channel = str(channel or "telegram_dm").strip().lower()
    if normalized_channel == "dm":
        normalized_channel = "telegram_dm"
    external_lookup = external_chat_id or (
        str(telegram_chat_id) if telegram_chat_id is not None else None
    )
    if telegram_chat_id:
        if external_lookup:
            existing_result = await db.execute(
                select(Conversation)
                .where(
                    Conversation.workspace_id == workspace_id,
                    or_(
                        Conversation.telegram_chat_id == telegram_chat_id,
                        (
                            (Conversation.external_chat_id == external_lookup)
                            & (Conversation.channel.in_([normalized_channel, "dm"]))
                        ),
                    ),
                )
                .order_by(Conversation.telegram_chat_id.is_(None), Conversation.id.asc())
                .limit(1)
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                existing.customer_id = customer_id
                existing.channel = normalized_channel
                existing.telegram_chat_id = telegram_chat_id
                existing.external_chat_id = external_lookup
                db.add(existing)
                await db.flush()
                return existing

        # Telegram: upsert by workspace + telegram_chat_id (unique constraint)
        stmt = pg_insert(Conversation).values(
            workspace_id=workspace_id,
            customer_id=customer_id,
            channel=normalized_channel,
            telegram_chat_id=telegram_chat_id,
            external_chat_id=str(telegram_chat_id),
        ).on_conflict_do_update(
            constraint="uq_conversation_workspace_chat",
            set_={
                "customer_id": customer_id,
                "channel": normalized_channel,
                "external_chat_id": str(telegram_chat_id),
            },
        ).returning(Conversation)
        result = await db.execute(stmt)
        return result.scalar_one()
    else:
        # Non-Telegram (Instagram, WhatsApp): find by external chat when present,
        # otherwise by workspace + customer + channel.
        if external_chat_id:
            result = await db.execute(
                select(Conversation).where(
                    Conversation.workspace_id == workspace_id,
                    Conversation.external_chat_id == external_chat_id,
                    Conversation.channel == normalized_channel,
                )
            )
            conversation = result.scalar_one_or_none()
            if conversation:
                conversation.last_message_at = datetime.now(timezone.utc)
                return conversation

        result = await db.execute(
            select(Conversation).where(
                    Conversation.workspace_id == workspace_id,
                    Conversation.customer_id == customer_id,
                    Conversation.channel == normalized_channel,
                )
            )
        conversation = result.scalar_one_or_none()
        if conversation:
            conversation.last_message_at = datetime.now(timezone.utc)
            return conversation

        conversation = Conversation(
            workspace_id=workspace_id,
            customer_id=customer_id,
            channel=normalized_channel,
            external_chat_id=external_chat_id,
        )
        db.add(conversation)
        await db.flush()
        return conversation
