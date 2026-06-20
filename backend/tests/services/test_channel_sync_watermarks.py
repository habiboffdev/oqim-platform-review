from __future__ import annotations

from datetime import datetime, timezone

from app.services.channel_sync_models import ChannelMessageRecord
from app.services.channel_sync_watermarks import (
    get_sync_watermark,
    mark_boundary_complete,
    update_sync_watermark,
)


def _record(message_id: str) -> ChannelMessageRecord:
    return ChannelMessageRecord(
        external_message_id=message_id,
        sender_external_id="customer",
        text="salom",
        sent_at=datetime.now(timezone.utc),
        is_outgoing=False,
    )


async def test_update_sync_watermark_sorts_numeric_external_ids(conversation):
    update_sync_watermark(
        conversation=conversation,
        messages=[_record("12"), _record("10"), _record("11")],
        limit=10,
        after_external_message_id=None,
        before_external_message_id=None,
    )

    watermark = get_sync_watermark(conversation)
    assert watermark.oldest_external_message_id == "10"
    assert watermark.latest_external_message_id == "12"
    assert watermark.oldest_complete is True
    assert watermark.latest_complete is True


async def test_mark_boundary_complete_preserves_more_extreme_watermark(conversation):
    update_sync_watermark(
        conversation=conversation,
        messages=[_record("10"), _record("20")],
        limit=50,
        after_external_message_id=None,
        before_external_message_id=None,
    )

    mark_boundary_complete(
        conversation=conversation,
        boundary="oldest",
        external_message_id="15",
    )
    mark_boundary_complete(
        conversation=conversation,
        boundary="latest",
        external_message_id="18",
    )

    watermark = get_sync_watermark(conversation)
    assert watermark.oldest_external_message_id == "10"
    assert watermark.latest_external_message_id == "20"
    assert watermark.oldest_complete is True
    assert watermark.latest_complete is True
