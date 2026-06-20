from __future__ import annotations

from app.models.message import Message
from app.services.channel_sync_models import ChannelMessageRecord


def duplicate_needs_repair(
    *,
    existing_stub: Message,
    incoming: ChannelMessageRecord,
) -> bool:
    if incoming.media_type and (
        not existing_stub.media_type
        or should_upgrade_media_type(
            existing_media_type=existing_stub.media_type,
            incoming_media_type=incoming.media_type,
        )
    ):
        return True
    if incoming.grouped_id and not existing_stub.grouped_id:
        return True
    incoming_meta = incoming.media_metadata or {}
    existing_meta = existing_stub.media_metadata or {}
    if incoming_meta and incoming_meta != existing_meta:
        return True
    return incoming.text_entities is not None and existing_stub.text_entities != incoming.text_entities


def apply_duplicate_repair(
    *,
    existing_message: Message,
    incoming: ChannelMessageRecord,
) -> bool:
    changed = False
    if incoming.media_type and (
        not existing_message.media_type
        or should_upgrade_media_type(
            existing_media_type=existing_message.media_type,
            incoming_media_type=incoming.media_type,
        )
    ):
        existing_message.media_type = incoming.media_type
        changed = True
    if incoming.media_metadata and not existing_message.media_metadata:
        existing_message.media_metadata = incoming.media_metadata
        changed = True
    elif incoming.media_metadata and existing_message.media_metadata != incoming.media_metadata:
        merged = dict(existing_message.media_metadata or {})
        merged.update(incoming.media_metadata)
        existing_message.media_metadata = merged
        changed = True
    if incoming.grouped_id and not existing_message.grouped_id:
        existing_message.grouped_id = incoming.grouped_id
        changed = True
    if incoming.text_entities is not None and existing_message.text_entities != incoming.text_entities:
        existing_message.text_entities = incoming.text_entities
        changed = True
    return changed


def should_upgrade_media_type(
    *,
    existing_media_type: str | None,
    incoming_media_type: str | None,
) -> bool:
    if not existing_media_type or not incoming_media_type:
        return False
    if existing_media_type == incoming_media_type:
        return False
    return existing_media_type in {
        "document",
        "MessageMediaDocument",
        "MessageMediaPhoto",
    }
