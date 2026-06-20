"""Channel-agnostic message normalizer — Issue #38.

Pure function: normalize(raw_payload, channel) → PersistMessageInput.
No side effects, no DB, no IO. Just data transformation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.modules.conversation_core.service import PersistMessageInput
from app.services.media_types import normalize_media_type


def normalize_text_entities(raw_entities: object) -> list[dict] | None:
    if not isinstance(raw_entities, list):
        return None

    normalized: list[dict] = []
    for entity in raw_entities:
        if not isinstance(entity, dict):
            continue
        entity_type = str(entity.get("type") or "").strip()
        if not entity_type:
            continue
        try:
            offset = int(entity.get("offset"))
            length = int(entity.get("length"))
        except (TypeError, ValueError):
            continue
        normalized_entity = {
            "type": entity_type,
            "offset": offset,
            "length": length,
        }
        document_id = entity.get("document_id") or entity.get("documentId")
        if document_id is not None:
            normalized_entity["document_id"] = str(document_id)
        normalized.append(normalized_entity)
    return normalized


def normalize(raw: dict, *, channel: str) -> PersistMessageInput:
    """Normalize a raw bridge/webhook payload into a PersistMessageInput."""
    if channel == "telegram_dm":
        return _normalize_telegram(raw)
    return _normalize_generic(raw, channel=channel)


def _normalize_generic(raw: dict, *, channel: str) -> PersistMessageInput:
    """Generic normalizer for non-Telegram channels (Instagram, WhatsApp, etc.)."""
    return PersistMessageInput(
        workspace_id=0,  # Caller sets this from auth context
        sender_id=None,
        sender_external_id=str(raw.get("senderId") or ""),
        sender_name="",
        text=raw.get("text", ""),
        is_outgoing=bool(raw.get("isOutgoing", False)),
        channel=channel,
        external_chat_id=str(raw.get("chatId") or ""),
        external_message_id=str(raw.get("messageId", "")),
        media_metadata=raw.get("mediaMetadata") if isinstance(raw.get("mediaMetadata"), dict) else None,
        media_type=normalize_media_type(
            raw.get("mediaType") or None,
            raw.get("mediaMetadata") if isinstance(raw.get("mediaMetadata"), dict) else None,
        ),
        text_entities=normalize_text_entities(raw.get("textEntities")),
        reply_to_msg_id=raw.get("replyToMsgId"),
        forward_from_name=raw.get("forwardFromName"),
        forward_date=(
            datetime.fromtimestamp(raw.get("forwardDate"), tz=timezone.utc)
            if raw.get("forwardDate")
            else None
        ),
        grouped_id=raw.get("groupedId"),
        message_ts=datetime.fromtimestamp(raw.get("date", 0), tz=timezone.utc) if raw.get("date") else None,
    )


def _normalize_telegram(raw: dict) -> PersistMessageInput:
    return PersistMessageInput(
        workspace_id=0,  # Caller sets this from auth context
        sender_id=int(raw["senderId"]),
        sender_name="",  # Caller sets this from customer lookup
        text=raw.get("text", ""),
        is_outgoing=bool(raw.get("isOutgoing", False)),
        channel="telegram_dm",
        telegram_chat_id=int(raw["chatId"]),
        telegram_message_id=int(raw["messageId"]),
        media_metadata=raw.get("mediaMetadata") if isinstance(raw.get("mediaMetadata"), dict) else None,
        media_type=normalize_media_type(
            raw.get("mediaType") or None,
            raw.get("mediaMetadata") if isinstance(raw.get("mediaMetadata"), dict) else None,
        ),
        text_entities=normalize_text_entities(raw.get("textEntities")),
        reply_to_msg_id=raw.get("replyToMsgId"),
        forward_from_name=raw.get("forwardFromName"),
        forward_date=(
            datetime.fromtimestamp(raw.get("forwardDate"), tz=timezone.utc)
            if raw.get("forwardDate")
            else None
        ),
        grouped_id=raw.get("groupedId"),
        message_ts=datetime.fromtimestamp(raw["date"], tz=timezone.utc),
    )
