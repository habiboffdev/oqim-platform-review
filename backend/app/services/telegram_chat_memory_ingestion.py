from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.media_runtime import MediaRuntime
from app.models.message import Message
from app.modules.conversation_core.service import upsert_customer_and_conversation
from app.services.channel_conversation_sync import ChannelConversationSync
from app.services.channel_media_access import build_media_runtime_metadata
from app.services.channel_sync_models import ChannelMessageRecord
from app.services.media_runtime import ensure_media_runtime_for_message
from app.services.media_types import normalize_media_type


@dataclass(slots=True)
class TelegramChatMemoryIngestionResult:
    scanned: int = 0
    persisted: int = 0
    duplicates: int = 0
    conversations: int = 0
    media_runtime_queued: int = 0
    degraded_reason: str | None = None


@dataclass(slots=True)
class _SidecarRawMessage:
    chat_id: str
    message_id: str
    sender_id: str
    sent_at: datetime
    text: str
    is_outgoing: bool
    source: str
    chat_title: str
    media_ref: dict[str, Any]
    media_refs: list[dict[str, Any]]


class TelegramChatMemoryIngestionService:
    """Project durable sidecar raw messages into backend chat memory.

    The sidecar owns Telegram update/local-state tables. This backend service is
    the low-priority ingestion bridge: it consumes those rows outside the live
    update callback and reuses the existing conversation/message projection.
    """

    async def ingest_due_raw_messages(
        self,
        *,
        session: AsyncSession,
        workspace_id: int,
        limit: int = 100,
        channel: str = "telegram_dm",
    ) -> TelegramChatMemoryIngestionResult:
        if limit <= 0:
            return TelegramChatMemoryIngestionResult()
        if not await self._sidecar_messages_table_exists(session):
            return TelegramChatMemoryIngestionResult(
                degraded_reason="sidecar_messages_table_missing",
            )

        raw_messages = await self._load_raw_messages(
            session=session,
            workspace_id=workspace_id,
            limit=limit,
        )
        if not raw_messages:
            return TelegramChatMemoryIngestionResult()

        sync = ChannelConversationSync()
        grouped: dict[str, list[_SidecarRawMessage]] = defaultdict(list)
        for item in raw_messages:
            grouped[item.chat_id].append(item)

        result = TelegramChatMemoryIngestionResult(scanned=len(raw_messages))
        for chat_id, items in grouped.items():
            telegram_chat_id = _safe_int(chat_id)
            if telegram_chat_id is None:
                continue
            _customer, conversation = await upsert_customer_and_conversation(
                session,
                workspace_id=workspace_id,
                telegram_chat_id=telegram_chat_id,
                external_id=chat_id,
                external_chat_id=chat_id,
                display_name=items[-1].chat_title,
                channel=channel,
            )
            records = [_channel_record_from_sidecar(item) for item in items]
            sync_result = await sync.persist_history_batch(
                session=session,
                workspace_id=workspace_id,
                conversation=conversation,
                messages=records,
                batch_limit=len(records),
            )
            result.conversations += 1
            result.persisted += sync_result.persisted
            result.duplicates += sync_result.duplicates
            result.media_runtime_queued += await self._ensure_media_runtimes(
                session=session,
                workspace_id=workspace_id,
                conversation_id=conversation.id,
                external_message_ids=[record.external_message_id for record in records],
            )

        return result

    async def _sidecar_messages_table_exists(self, session: AsyncSession) -> bool:
        exists = await session.scalar(
            text("SELECT to_regclass('public.telegram_sidecar_messages') IS NOT NULL")
        )
        return bool(exists)

    async def _load_raw_messages(
        self,
        *,
        session: AsyncSession,
        workspace_id: int,
        limit: int,
    ) -> list[_SidecarRawMessage]:
        rows = await session.execute(
            text(
                """
                SELECT
                  m.chat_id,
                  m.message_id,
                  COALESCE(m.sender_id, m.chat_id) AS sender_id,
                  m.message_date,
                  m.text,
                  m.is_outgoing,
                  m.media_ref,
                  m.source,
                  COALESCE(chat_peer.display_name, m.chat_id) AS chat_title,
                  COALESCE(
                    jsonb_agg(
                      jsonb_build_object(
                        'media_key', r.media_key,
                        'media_kind', r.media_kind,
                        'document_id', r.document_id,
                        'photo_id', r.photo_id,
                        'mime_type', r.mime_type,
                        'size', r.size,
                        'status', r.status,
                        'attempts', r.attempts,
                        'last_error', r.last_error
                      )
                      ORDER BY r.media_key
                    ) FILTER (WHERE r.media_key IS NOT NULL),
                    '[]'::jsonb
                  ) AS media_refs
                FROM telegram_sidecar_messages m
                LEFT JOIN telegram_sidecar_peers chat_peer
                  ON chat_peer.workspace_id = m.workspace_id
                 AND chat_peer.peer_id = m.chat_id
                LEFT JOIN telegram_sidecar_media_refs r
                  ON r.workspace_id = m.workspace_id
                 AND r.chat_id = m.chat_id
                 AND r.message_id = m.message_id
                WHERE m.workspace_id = :workspace_id
                  AND m.chat_id ~ '^-?[0-9]+$'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM conversations c
                    JOIN messages backend_message
                      ON backend_message.conversation_id = c.id
                    WHERE c.workspace_id = m.workspace_id
                      AND c.telegram_chat_id = CASE
                        WHEN m.chat_id ~ '^-?[0-9]+$' THEN m.chat_id::bigint
                        ELSE NULL
                      END
                      AND (
                        backend_message.external_message_id = m.message_id
                        OR backend_message.telegram_message_id = CASE
                          WHEN m.message_id ~ '^-?[0-9]+$' THEN m.message_id::bigint
                          ELSE NULL
                        END
                      )
                  )
                GROUP BY
                  m.chat_id,
                  m.message_id,
                  m.sender_id,
                  m.message_date,
                  m.text,
                  m.is_outgoing,
                  m.media_ref,
                  m.source,
                  chat_peer.display_name
                ORDER BY m.message_date NULLS LAST, m.chat_id, m.message_id
                LIMIT :limit
                """
            ),
            {"workspace_id": workspace_id, "limit": limit},
        )
        return [_raw_message_from_row(row._mapping) for row in rows.all()]

    async def _ensure_media_runtimes(
        self,
        *,
        session: AsyncSession,
        workspace_id: int,
        conversation_id: int,
        external_message_ids: list[str],
    ) -> int:
        if not external_message_ids:
            return 0
        messages = list(
            (
                await session.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.external_message_id.in_(external_message_ids),
                        Message.media_type.is_not(None),
                    )
                    .order_by(Message.id.asc())
                )
            ).all()
        )
        queued = 0
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            return 0
        for message in messages:
            existing_runtime_id = await session.scalar(
                select(MediaRuntime.id).where(MediaRuntime.message_id == message.id).limit(1)
            )
            runtime = await ensure_media_runtime_for_message(
                session,
                workspace_id=workspace_id,
                conversation=conversation,
                message=message,
            )
            if existing_runtime_id is None and runtime is not None:
                queued += 1
        if queued:
            await session.commit()
        return queued


def _raw_message_from_row(row: Any) -> _SidecarRawMessage:
    message_date = row.get("message_date")
    sent_at = (
        datetime.fromtimestamp(float(message_date), UTC)
        if message_date is not None
        else datetime.now(UTC)
    )
    media_ref = _as_dict(row.get("media_ref"))
    media_refs = _as_list(row.get("media_refs"))
    return _SidecarRawMessage(
        chat_id=str(row.get("chat_id") or ""),
        message_id=str(row.get("message_id") or ""),
        sender_id=str(row.get("sender_id") or row.get("chat_id") or ""),
        sent_at=sent_at,
        text=str(row.get("text") or ""),
        is_outgoing=bool(row.get("is_outgoing")),
        source=str(row.get("source") or "sidecar_raw_message"),
        chat_title=str(row.get("chat_title") or row.get("chat_id") or "Telegram chat"),
        media_ref=media_ref,
        media_refs=media_refs,
    )


def _channel_record_from_sidecar(item: _SidecarRawMessage) -> ChannelMessageRecord:
    metadata = _media_metadata(item)
    media_type = _sidecar_media_type(metadata)
    text_value = item.text.strip() or _placeholder_for_media(media_type)
    if media_type:
        metadata = build_media_runtime_metadata(
            media_type=media_type,
            content=text_value,
            media_metadata=metadata,
            hydration_status=_hydration_status_from_refs(item.media_refs),
        ) or metadata
    return ChannelMessageRecord(
        external_message_id=item.message_id,
        sender_external_id=item.sender_id,
        text=text_value,
        sent_at=item.sent_at,
        is_outgoing=item.is_outgoing,
        media_type=media_type,
        media_metadata=metadata if metadata else None,
    )


def _media_metadata(item: _SidecarRawMessage) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    class_name = item.media_ref.get("className") or item.media_ref.get("media_kind")
    if class_name:
        metadata["className"] = class_name
    mime_type = item.media_ref.get("mimeType") or item.media_ref.get("mime_type")
    if mime_type:
        metadata["mime_type"] = mime_type
    if item.media_ref.get("documentId"):
        metadata["document_id"] = str(item.media_ref["documentId"])
    if item.media_ref.get("photoId"):
        metadata["photo_id"] = str(item.media_ref["photoId"])
    if item.media_ref.get("size") is not None:
        metadata["size"] = item.media_ref["size"]
    if item.media_refs:
        metadata["sidecar_media_refs"] = item.media_refs
        first_ref = item.media_refs[0]
        metadata.setdefault("className", first_ref.get("media_kind"))
        metadata.setdefault("mime_type", first_ref.get("mime_type"))
        if first_ref.get("document_id"):
            metadata.setdefault("document_id", str(first_ref["document_id"]))
        if first_ref.get("photo_id"):
            metadata.setdefault("photo_id", str(first_ref["photo_id"]))
    if metadata:
        metadata["sidecar_source"] = item.source
    return metadata


def _sidecar_media_type(metadata: dict[str, Any]) -> str | None:
    class_name = metadata.get("className")
    if not class_name:
        return None
    return normalize_media_type(str(class_name), metadata)


def _hydration_status_from_refs(media_refs: list[dict[str, Any]]) -> str | None:
    if not media_refs:
        return None
    statuses = {str(ref.get("status") or "").strip().lower() for ref in media_refs}
    if "hydrated" in statuses:
        return "hydrated"
    if "failed" in statuses:
        return "unavailable"
    return "pending"


def _placeholder_for_media(media_type: str | None) -> str:
    labels = {
        "voice": "Mijoz ovozli xabar yubordi",
        "audio": "Mijoz ovozli xabar yubordi",
        "photo": "Mijoz rasm yubordi",
        "video": "Mijoz video yubordi",
        "video_note": "Mijoz video xabar yubordi",
        "document": "Mijoz hujjat yubordi",
        "sticker": "😊",
    }
    if not media_type:
        return ""
    return f"[{media_type}] {labels.get(media_type, media_type)}"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
