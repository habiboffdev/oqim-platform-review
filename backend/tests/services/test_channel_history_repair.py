from __future__ import annotations

from datetime import datetime, timezone

from app.models.message import Message
from app.services.channel_history_repair import (
    apply_duplicate_repair,
    duplicate_needs_repair,
    should_upgrade_media_type,
)
from app.services.channel_sync_models import ChannelMessageRecord


def _incoming(**overrides) -> ChannelMessageRecord:
    data = {
        "external_message_id": "10",
        "sender_external_id": "customer",
        "text": "salom",
        "sent_at": datetime.now(timezone.utc),
        "is_outgoing": False,
    }
    data.update(overrides)
    return ChannelMessageRecord(**data)


def test_should_upgrade_media_type_only_replaces_legacy_generic_types():
    assert should_upgrade_media_type(
        existing_media_type="document",
        incoming_media_type="sticker",
    )
    assert should_upgrade_media_type(
        existing_media_type="MessageMediaPhoto",
        incoming_media_type="photo",
    )
    assert not should_upgrade_media_type(
        existing_media_type="photo",
        incoming_media_type="sticker",
    )


def test_duplicate_repair_merges_media_metadata_and_entities():
    existing = Message(
        media_type="document",
        media_metadata={"file_name": "old.webp"},
        text_entities=[],
    )
    incoming = _incoming(
        media_type="sticker",
        media_metadata={"emoji": ":)"},
        text_entities=[{"type": "custom_emoji", "offset": 0, "length": 1}],
        grouped_id=42,
    )

    assert duplicate_needs_repair(existing_stub=existing, incoming=incoming)
    assert apply_duplicate_repair(existing_message=existing, incoming=incoming)
    assert existing.media_type == "sticker"
    assert existing.media_metadata == {
        "file_name": "old.webp",
        "emoji": ":)",
    }
    assert existing.text_entities == [{"type": "custom_emoji", "offset": 0, "length": 1}]
    assert existing.grouped_id == 42


def test_duplicate_repair_is_noop_when_payload_matches():
    existing = Message(
        media_type="photo",
        media_metadata={"width": 100},
        text_entities=[],
        grouped_id=9,
    )
    incoming = _incoming(
        media_type="photo",
        media_metadata={"width": 100},
        text_entities=[],
        grouped_id=9,
    )

    assert not duplicate_needs_repair(existing_stub=existing, incoming=incoming)
    assert not apply_duplicate_repair(existing_message=existing, incoming=incoming)
