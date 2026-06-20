"""Event Spine — durable append-only log of canonical message events.

Current runtime: channel webhooks append here first, and the supervised
EventSpinePersistConsumer is the default authoritative message persistence
path. The diff consumer remains as an observation and migration safety net.

Keeping event types and the publisher co-located keeps the wire contract in
one place while the remaining lifecycle events move onto the spine.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import get_logger
from app.core.redis_streams import xadd_event
from app.models.event_spine_record import EventSpineRecord


class DivergenceKind(str, Enum):
    """Taxonomy of divergences the diff consumer can detect."""

    EVENT_NO_DB = "event_no_db"
    DB_NO_EVENT = "db_no_event"
    TEXT_MISMATCH = "text_mismatch"
    DEDUP_RACED = "dedup_raced"
    SEND_NO_CONFIRM = "send_no_confirm"
    CONFIRM_NO_SEND = "confirm_no_send"


class _EventBase(BaseModel):
    """Fields every event carries. Forbid extras to keep schema stable."""

    model_config = ConfigDict(extra="forbid")

    type: str
    schema_version: int = 1
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    workspace_id: int
    channel: str = "telegram_dm"
    channel_account_id: str | None = None
    channel_conversation_id: str | None = None
    channel_message_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    occurred_at: float = Field(default_factory=time.time)
    received_at: float = Field(default_factory=time.time)
    emitted_at: float = Field(default_factory=time.time)
    idempotency_key: str


class MsgInbound(_EventBase):
    type: Literal["msg.inbound", "message.received"] = "msg.inbound"
    telegram_chat_id: int
    telegram_message_id: int
    sender_telegram_id: int
    channel_sender_id: str | None = None
    sender_name: str | None = None
    sender_username: str | None = None
    is_outgoing: bool
    text: str | None
    media_type: str | None = None
    media_metadata: dict | None = None
    text_entities: list[dict] | None = None
    reply_to_msg_id: int | None = None
    forward_from_name: str | None = None
    forward_date: float | None = None
    grouped_id: int | None = None
    sent_at: float
    is_historical: bool = False
    source: str | None = None
    telegram_update_received_at: float | None = None
    telegram_state_applied_at: float | None = None
    hot_event_built_at: float | None = None
    outbox_enqueued_at: float | None = None
    backend_webhook_received_at: float | None = None

    @classmethod
    def from_webhook(
        cls,
        payload: dict,
        *,
        workspace_id: int,
        correlation_id: str | None = None,
    ) -> "MsgInbound":
        chat_id = int(payload["chatId"])
        message_id = int(payload["messageId"])
        source = str(payload.get("source") or "")
        idempotency_key = f"tg:{chat_id}:{message_id}"
        if source == "live_recovery":
            idempotency_key = f"{idempotency_key}:live_recovery"
        return cls(
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            emitted_at=time.time(),
            idempotency_key=idempotency_key,
            channel_account_id=str(payload.get("sellerUserId") or ""),
            channel_conversation_id=str(chat_id),
            channel_message_id=str(message_id),
            channel_sender_id=str(payload.get("senderId") or ""),
            sender_name=str(payload.get("senderName") or ""),
            sender_username=str(payload.get("senderUsername") or "").lstrip("@") or None,
            telegram_chat_id=chat_id,
            telegram_message_id=message_id,
            sender_telegram_id=int(payload["senderId"]),
            is_outgoing=bool(payload.get("isOutgoing", False)),
            # Empty string from Telegram means "no text" (media-only message) — normalize to None.
            text=payload.get("text") or None,
            media_type=payload.get("mediaType"),
            media_metadata=payload.get("mediaMetadata") if isinstance(payload.get("mediaMetadata"), dict) else None,
            text_entities=payload.get("textEntities") if isinstance(payload.get("textEntities"), list) else None,
            reply_to_msg_id=payload.get("replyToMsgId"),
            forward_from_name=payload.get("forwardFromName"),
            forward_date=payload.get("forwardDate"),
            grouped_id=payload.get("groupedId"),
            sent_at=float(payload["date"]),
            is_historical=bool(
                payload.get("isHistorical")
                or payload.get("historical")
                or payload.get("syncMode") == "history"
                or payload.get("source") in {"history", "catch_up", "backfill"}
            ),
            source=source or None,
            telegram_update_received_at=payload.get("telegram_update_received_at"),
            telegram_state_applied_at=payload.get("telegram_state_applied_at"),
            hot_event_built_at=payload.get("hot_event_built_at"),
            outbox_enqueued_at=payload.get("outbox_enqueued_at"),
            backend_webhook_received_at=payload.get("backend_webhook_received_at"),
        )


class MsgEdited(_EventBase):
    type: Literal["msg.edited", "message.edited"] = "msg.edited"
    telegram_chat_id: int
    telegram_message_id: int
    new_text: str
    text_entities: list[dict] | None = None
    edited_at: float

    @classmethod
    def from_webhook(
        cls,
        payload: dict,
        *,
        workspace_id: int,
        edited_at: float | None = None,
        correlation_id: str | None = None,
    ) -> "MsgEdited":
        chat_id = int(payload["chatId"])
        message_id = int(payload["messageId"])
        eat = edited_at if edited_at is not None else time.time()
        return cls(
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            emitted_at=time.time(),
            idempotency_key=f"tg:{chat_id}:{message_id}:edit:{eat:.6f}",
            channel_account_id=str(payload.get("sellerUserId") or ""),
            channel_conversation_id=str(chat_id),
            channel_message_id=str(message_id),
            telegram_chat_id=chat_id,
            telegram_message_id=message_id,
            new_text=payload["text"],
            text_entities=payload.get("textEntities") if isinstance(payload.get("textEntities"), list) else None,
            edited_at=eat,
        )


class MsgDeleted(_EventBase):
    type: Literal["msg.deleted", "message.deleted"] = "msg.deleted"
    telegram_chat_id: int
    telegram_message_ids: list[int]
    deleted_at: float

    @classmethod
    def from_webhook(
        cls,
        payload: dict,
        *,
        workspace_id: int,
        deleted_at: float | None = None,
        correlation_id: str | None = None,
    ) -> "MsgDeleted":
        chat_id = int(payload["chatId"])
        sorted_ids = sorted(int(i) for i in payload["messageIds"])
        key_digest = hashlib.sha256(
            ",".join(str(i) for i in sorted_ids).encode("utf-8")
        ).hexdigest()[:12]
        return cls(
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            emitted_at=time.time(),
            idempotency_key=f"tg:{chat_id}:del:{key_digest}",
            channel_account_id=str(payload.get("sellerUserId") or ""),
            channel_conversation_id=str(chat_id),
            telegram_chat_id=chat_id,
            telegram_message_ids=sorted_ids,
            deleted_at=deleted_at if deleted_at is not None else time.time(),
        )


class MsgSent(_EventBase):
    type: Literal["msg.sent", "message.send_requested"] = "msg.sent"
    conversation_id: int
    text: str
    action_record_id: int | None = None

    @classmethod
    def build(
        cls,
        *,
        workspace_id: int,
        conversation_id: int,
        text: str,
        action_record_id: int | None = None,
        client_idempotency_key: str | None = None,
        correlation_id: str | None = None,
        channel: str = "telegram_dm",
        channel_account_id: str | None = None,
        channel_conversation_id: str | None = None,
    ) -> "MsgSent":
        key = client_idempotency_key or f"send:{uuid.uuid4().hex}"
        return cls(
            workspace_id=workspace_id,
            channel=channel,
            channel_account_id=channel_account_id,
            channel_conversation_id=channel_conversation_id,
            correlation_id=correlation_id,
            emitted_at=time.time(),
            idempotency_key=key,
            conversation_id=conversation_id,
            text=text,
            action_record_id=action_record_id,
        )


class MsgMediaSent(_EventBase):
    type: Literal["msg.media_sent", "message.media_send_requested"] = (
        "msg.media_sent"
    )
    conversation_id: int
    media_type: str
    media_url: str
    media_asset_id: str | None = None
    caption: str | None = None
    action_record_id: int | None = None

    @classmethod
    def build(
        cls,
        *,
        workspace_id: int,
        conversation_id: int,
        media_type: str,
        media_url: str,
        media_asset_id: str | None = None,
        caption: str | None = None,
        action_record_id: int | None = None,
        client_idempotency_key: str | None = None,
        correlation_id: str | None = None,
        channel: str = "telegram_dm",
        channel_account_id: str | None = None,
        channel_conversation_id: str | None = None,
    ) -> "MsgMediaSent":
        key = client_idempotency_key or f"send:{uuid.uuid4().hex}"
        return cls(
            workspace_id=workspace_id,
            channel=channel,
            channel_account_id=channel_account_id,
            channel_conversation_id=channel_conversation_id,
            correlation_id=correlation_id,
            emitted_at=time.time(),
            idempotency_key=key,
            conversation_id=conversation_id,
            media_type=media_type,
            media_url=media_url,
            media_asset_id=media_asset_id,
            caption=caption,
            action_record_id=action_record_id,
        )


class DeliveryConfirmed(_EventBase):
    type: Literal["delivery.confirmed", "message.send_confirmed"] = "delivery.confirmed"
    conversation_id: int
    action_record_id: int | None
    external_message_id: str
    delivered_at: float

    @classmethod
    def build(
        cls,
        *,
        workspace_id: int,
        conversation_id: int,
        action_record_id: int | None,
        external_message_id: str,
        client_idempotency_key: str | None = None,
        delivered_at: float | None = None,
        correlation_id: str | None = None,
        channel: str = "telegram_dm",
        channel_account_id: str | None = None,
        channel_conversation_id: str | None = None,
    ) -> "DeliveryConfirmed":
        causation_key = client_idempotency_key or (
            f"action_record:{action_record_id}" if action_record_id is not None else None
        )
        key = f"delivery:{causation_key}" if causation_key else f"delivery:{uuid.uuid4().hex}"
        return cls(
            workspace_id=workspace_id,
            channel=channel,
            channel_account_id=channel_account_id,
            channel_conversation_id=channel_conversation_id,
            channel_message_id=external_message_id,
            correlation_id=correlation_id,
            causation_id=causation_key,
            emitted_at=time.time(),
            idempotency_key=key,
            conversation_id=conversation_id,
            action_record_id=action_record_id,
            external_message_id=external_message_id,
            delivered_at=delivered_at if delivered_at is not None else time.time(),
        )


class DeliveryUnknown(_EventBase):
    type: Literal["delivery.unknown", "message.send_unknown"] = "delivery.unknown"
    conversation_id: int
    action_record_id: int | None = None
    client_idempotency_key: str
    reason: str | None = None
    marked_at: float

    @classmethod
    def build(
        cls,
        *,
        workspace_id: int,
        conversation_id: int,
        client_idempotency_key: str,
        action_record_id: int | None = None,
        reason: str | None = None,
        marked_at: float | None = None,
        correlation_id: str | None = None,
        channel: str = "telegram_dm",
        channel_account_id: str | None = None,
        channel_conversation_id: str | None = None,
    ) -> "DeliveryUnknown":
        return cls(
            workspace_id=workspace_id,
            channel=channel,
            channel_account_id=channel_account_id,
            channel_conversation_id=channel_conversation_id,
            correlation_id=correlation_id,
            causation_id=client_idempotency_key,
            emitted_at=time.time(),
            idempotency_key=f"delivery_unknown:{client_idempotency_key}",
            conversation_id=conversation_id,
            action_record_id=action_record_id,
            client_idempotency_key=client_idempotency_key,
            reason=reason,
            marked_at=marked_at if marked_at is not None else time.time(),
        )


class DeliveryFailed(_EventBase):
    type: Literal["delivery.failed", "message.send_failed"] = "delivery.failed"
    conversation_id: int
    action_record_id: int | None = None
    client_idempotency_key: str
    error: str | None = None
    failed_at: float

    @classmethod
    def build(
        cls,
        *,
        workspace_id: int,
        conversation_id: int,
        client_idempotency_key: str,
        action_record_id: int | None = None,
        error: str | None = None,
        failed_at: float | None = None,
        correlation_id: str | None = None,
        channel: str = "telegram_dm",
        channel_account_id: str | None = None,
        channel_conversation_id: str | None = None,
    ) -> "DeliveryFailed":
        return cls(
            workspace_id=workspace_id,
            channel=channel,
            channel_account_id=channel_account_id,
            channel_conversation_id=channel_conversation_id,
            correlation_id=correlation_id,
            causation_id=client_idempotency_key,
            emitted_at=time.time(),
            idempotency_key=f"delivery_failed:{client_idempotency_key}",
            conversation_id=conversation_id,
            action_record_id=action_record_id,
            client_idempotency_key=client_idempotency_key,
            error=error,
            failed_at=failed_at if failed_at is not None else time.time(),
        )


class ReadReceipt(_EventBase):
    type: Literal["read.receipt"] = "read.receipt"
    telegram_chat_id: int
    max_telegram_message_id: int | None = None
    unread_count: int = 0
    read_at: float


class FollowUpScheduled(_EventBase):
    type: Literal["follow_up.scheduled"] = "follow_up.scheduled"
    telegram_chat_id: int
    root_telegram_message_id: int | None = None
    kind: str
    reason_code: str
    title: str
    due_at: float
    waiting_for: str = "customer"
    priority: str = "medium"
    suggested_message: str | None = None
    scheduled_at: float


class CrmUpdated(_EventBase):
    type: Literal["crm.updated"] = "crm.updated"
    telegram_chat_id: int
    pipeline_stage: str | None = None
    last_intent: str | None = None
    products_interested: list[str] | None = None
    urgency: bool | None = None
    lead_score: float | None = None
    updated_at: float


class BackfillWindowApplied(_EventBase):
    type: Literal["backfill.window_applied", "message.backfilled"] = "backfill.window_applied"
    telegram_chat_id: int
    oldest_external_message_id: str | None = None
    latest_external_message_id: str | None = None
    oldest_complete: bool = False
    latest_complete: bool = False
    applied_at: float


class MediaHydrationStateChanged(_EventBase):
    type: Literal[
        "media.hydration_state_changed",
        "media.hydration_started",
        "media.hydration_deferred",
        "media.hydration_completed",
        "media.hydration_failed",
    ] = "media.hydration_state_changed"
    telegram_chat_id: int
    telegram_message_id: int
    hydration_status: str
    asset_state: str
    semantic_state: str
    action_state: str
    ai_relevant: bool = True
    mime_type: str | None = None
    normalized_text: str | None = None
    media_evidence: dict[str, Any] | None = None
    commercial_semantics: dict[str, Any] | None = None
    last_error: str | None = None
    changed_at: float


Event = Annotated[
    Union[
        MsgInbound,
        MsgEdited,
        MsgDeleted,
        MsgSent,
        MsgMediaSent,
        DeliveryConfirmed,
        DeliveryUnknown,
        DeliveryFailed,
        ReadReceipt,
        FollowUpScheduled,
        CrmUpdated,
        BackfillWindowApplied,
        MediaHydrationStateChanged,
    ],
    Field(discriminator="type"),
]
_event_adapter: TypeAdapter[Event] = TypeAdapter(Event)


# --- Publisher -------------------------------------------------------------

logger = get_logger("event_spine")


def _stream_id_text(stream_id: Any) -> str:
    if isinstance(stream_id, bytes):
        return stream_id.decode("utf-8")
    return str(stream_id)


def _next_stream_id(stream_id: Any) -> str:
    text = _stream_id_text(stream_id)
    milliseconds, separator, sequence = text.partition("-")
    if not separator:
        return text
    return f"{milliseconds}-{int(sequence) + 1}"


class EventSpine:
    """Durable append-only log of canonical message events.

    ``append()`` is the authoritative write boundary for webhook intake.
    ``publish()`` is a fire-and-forget helper for side-channel lifecycle
    events; background tasks handle XADD and count failures without raising
    into the caller.
    """

    def __init__(
        self,
        redis: Any,
        *,
        db_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._redis = redis
        self._db_factory = db_factory
        self._pending: set[asyncio.Task] = set()

    def publish(self, event: Any) -> None:
        """Schedule an event publish. Returns synchronously in <1ms.

        Correlation ID is expected to be baked into ``event.correlation_id``
        at construction time (captured from the request's contextvar), so
        async task boundaries are irrelevant.
        """
        task = asyncio.create_task(self._do_publish(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _do_publish(self, event: Any) -> None:
        try:
            await self.append(event)
        except Exception as exc:
            logger.warning(
                "event_spine.publish_failed",
                extra={
                    "event_type": getattr(event, "type", "unknown"),
                    "workspace_id": getattr(event, "workspace_id", None),
                    "correlation_id": getattr(event, "correlation_id", None),
                    "error": str(exc),
                },
            )
            try:
                await self._redis.incr("oqim:event_spine:publish_failures")
            except Exception:
                # Redis really is down; counter increment is also a no-op.
                pass

    async def append(self, event: Any) -> str | None:
        """Validate and append one canonical event.

        Returns the Redis stream id for a new append, or ``None`` when the
        event was already appended for this workspace/idempotency key.
        """
        validated = _event_adapter.validate_python(event)
        stream_key = f"oqim:events:{validated.workspace_id}"
        dedupe_key = (
            "oqim:event_spine:appended:"
            f"{validated.workspace_id}:{validated.idempotency_key}"
        )
        fields = {
            "schema_version": str(validated.schema_version),
            "event_id": validated.event_id,
            "type": validated.type,
            "workspace_id": str(validated.workspace_id),
            "channel": validated.channel,
            "channel_account_id": validated.channel_account_id or "",
            "channel_conversation_id": validated.channel_conversation_id or "",
            "channel_message_id": validated.channel_message_id or "",
            "idempotency_key": validated.idempotency_key,
            "correlation_id": validated.correlation_id or "",
            "causation_id": validated.causation_id or "",
            "occurred_at": str(validated.occurred_at),
            "received_at": str(validated.received_at),
            "payload": validated.model_dump_json(),
        }
        await self._archive_event(validated)
        stream_id = await xadd_event(self._redis, stream_key, fields, maxlen=None)
        await self._mark_archived_stream_id(validated, stream_id)
        is_new = await self._redis.set(
            dedupe_key,
            stream_id,
            nx=True,
            ex=60 * 60 * 24 * 7,
        )
        if is_new:
            return stream_id

        # The event is already present. We append before setting the marker so
        # an XADD failure can never leave a false dedupe key that drops retries.
        # Best-effort cleanup keeps concurrent duplicates small; consumers still
        # enforce idempotency by event payload.
        try:
            await self._redis.xdel(stream_key, stream_id)
        except Exception:
            logger.warning(
                "event_spine.duplicate_cleanup_failed",
                extra={
                    "workspace_id": validated.workspace_id,
                    "stream_id": stream_id,
                    "idempotency_key": validated.idempotency_key,
                },
                exc_info=True,
            )
        return None

    async def _archive_event(self, event: Event) -> None:
        """Persist canonical events before publishing to the Redis fan-out bus."""
        if self._db_factory is None:
            return

        payload = event.model_dump(mode="json")
        stmt = (
            pg_insert(EventSpineRecord)
            .values(
                workspace_id=event.workspace_id,
                event_id=event.event_id,
                event_type=event.type,
                schema_version=event.schema_version,
                channel=event.channel,
                channel_account_id=event.channel_account_id,
                channel_conversation_id=event.channel_conversation_id
                or str(getattr(event, "telegram_chat_id", "") or ""),
                channel_message_id=event.channel_message_id,
                idempotency_key=event.idempotency_key,
                correlation_id=event.correlation_id,
                causation_id=event.causation_id,
                occurred_at=_event_dt(event.occurred_at),
                received_at=_event_dt(event.received_at),
                payload=payload,
            )
            .on_conflict_do_nothing()
        )
        async with self._db_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def _mark_archived_stream_id(self, event: Event, stream_id: str) -> None:
        if self._db_factory is None:
            return
        stmt = (
            update(EventSpineRecord)
            .where(
                EventSpineRecord.workspace_id == event.workspace_id,
                EventSpineRecord.idempotency_key == event.idempotency_key,
                EventSpineRecord.stream_id.is_(None),
            )
            .values(stream_id=stream_id)
        )
        async with self._db_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def replay_conversation(
        self,
        *,
        workspace_id: int,
        channel: str,
        channel_conversation_id: str,
        batch_size: int = 500,
    ) -> list[Event]:
        """Read canonical events for one conversation in stream order.

        Replay intentionally walks the workspace stream in bounded batches so
        admin repair or projection rebuilds do not load an entire tenant stream
        into memory before filtering to the requested conversation.
        """
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")

        if self._db_factory is not None:
            archived = await self._replay_archived_conversation(
                workspace_id=workspace_id,
                channel=channel,
                channel_conversation_id=channel_conversation_id,
                batch_size=batch_size,
            )
            if archived:
                return archived

        stream_key = f"oqim:events:{workspace_id}"
        events: list[Event] = []
        cursor = "-"
        while True:
            entries = await self._redis.xrange(
                stream_key,
                min=cursor,
                max="+",
                count=batch_size,
            )
            if not entries:
                break

            for _stream_id, fields in entries:
                event = _event_adapter.validate_json(fields["payload"])
                event_conversation_id = event.channel_conversation_id or str(
                    getattr(event, "telegram_chat_id", "")
                )
                if (
                    event.channel == channel
                    and event_conversation_id == channel_conversation_id
                ):
                    events.append(event)

            next_cursor = _next_stream_id(entries[-1][0])
            if next_cursor == cursor:
                break
            cursor = next_cursor

            if len(entries) < batch_size:
                break

        return events

    async def _replay_archived_conversation(
        self,
        *,
        workspace_id: int,
        channel: str,
        channel_conversation_id: str,
        batch_size: int,
    ) -> list[Event]:
        events: list[Event] = []
        offset = 0
        while True:
            stmt = (
                select(EventSpineRecord.payload)
                .where(
                    EventSpineRecord.workspace_id == workspace_id,
                    EventSpineRecord.channel == channel,
                    EventSpineRecord.channel_conversation_id
                    == channel_conversation_id,
                )
                .order_by(EventSpineRecord.id.asc())
                .offset(offset)
                .limit(batch_size)
            )
            async with self._db_factory() as session:
                result = await session.execute(stmt)
                rows = result.all()
            if not rows:
                break
            events.extend(
                _event_adapter.validate_python(row[0])
                for row in rows
            )
            if len(rows) < batch_size:
                break
            offset += len(rows)
        return events

    async def drain(self, timeout: float = 5.0) -> None:
        """Wait for in-flight publishes to complete. Call on shutdown."""
        if not self._pending:
            return
        tasks = list(self._pending)
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for t in pending:
            t.cancel()


def _event_dt(value: float) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc)
