from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.channel_adapter_contract import (
    ChannelCapabilities,
    ChannelConversationSummary,
    ChannelInboundMessage,
    ChannelMediaBlob,
    UnsupportedChannelCapability,
)
from app.services.channel_adapter_source import AdapterBackedChannelSource
from app.services.channel_sync_models import (
    ChannelConversationRef,
    MediaBlob,
)

pytestmark = pytest.mark.asyncio


class _FakeAdapter:
    channel = "telegram_dm"

    def __init__(self) -> None:
        self.fetch_args: dict | None = None

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(fetch_history=True, fetch_media_blob=True)

    async def list_conversations(self, **kwargs):
        return [
            ChannelConversationSummary(
                external_chat_id="thread-1",
                title="Adapter Customer",
                unread_count=3,
                top_message_id=42,
                last_message_text="Adapter salom",
                last_message_date=1_700_000_000,
                last_message_is_outgoing=False,
            )
        ]

    async def fetch_history(self, **kwargs):
        self.fetch_args = kwargs
        return [
            ChannelInboundMessage(
                workspace_id=kwargs["workspace_id"],
                channel=self.channel,
                account_id="seller-1",
                conversation_id=kwargs["conversation_id"],
                message_id="42",
                sender_id="customer-1",
                sender_name="Customer",
                text="Salom",
                sent_at=1_700_000_000,
                media_type="photo",
                media_metadata={"mime_type": "image/jpeg", "grouped_id": "9001"},
                text_entities=[{"type": "bold", "offset": 0, "length": 5}],
                reply_to_message_id="41",
            )
        ]

    async def fetch_media_blob(self, **kwargs):
        return ChannelMediaBlob(data=b"adapter-img", mime_type="image/jpeg")


class _NoDialogAdapter(_FakeAdapter):
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            fetch_history=True,
            list_conversations=False,
            fetch_media_blob=False,
        )


async def test_adapter_source_hydrates_messages_through_adapter():
    adapter = _FakeAdapter()
    source = AdapterBackedChannelSource(
        channel="telegram_dm",
        adapter=adapter,
    )

    messages = await source.fetch_messages(
        workspace_id=7,
        conversation=ChannelConversationRef(
            channel="telegram_dm",
            external_chat_id="thread-1",
        ),
        limit=25,
        after_external_message_id="40",
        before_external_message_id="50",
    )

    assert adapter.fetch_args == {
        "workspace_id": 7,
        "conversation_id": "thread-1",
        "limit": 25,
        "before_message_id": "50",
        "after_message_id": "40",
    }
    assert len(messages) == 1
    message = messages[0]
    assert message.external_message_id == "42"
    assert message.sender_external_id == "customer-1"
    assert message.sent_at == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    assert message.media_type == "photo"
    assert message.media_metadata == {"mime_type": "image/jpeg"}
    assert message.grouped_id == 9001
    assert message.text_entities == [{"type": "bold", "offset": 0, "length": 5}]
    assert message.reply_to_external_message_id == "41"


async def test_adapter_source_lists_conversations_through_adapter():
    source = AdapterBackedChannelSource(
        channel="telegram_dm",
        adapter=_FakeAdapter(),
    )
    conversations = await source.list_conversations(workspace_id=7, channel="telegram_dm")

    assert conversations[0].external_chat_id == "thread-1"
    assert conversations[0].title == "Adapter Customer"
    assert conversations[0].unread_count == 3
    assert conversations[0].last_message_date == datetime.fromtimestamp(
        1_700_000_000,
        tz=timezone.utc,
    )


async def test_adapter_source_fetches_media_through_adapter_when_supported():
    source = AdapterBackedChannelSource(
        channel="telegram_dm",
        adapter=_FakeAdapter(),
    )

    media = await source.fetch_media(
        workspace_id=7,
        conversation=ChannelConversationRef(
            channel="telegram_dm",
            external_chat_id="thread-1",
        ),
        external_message_id="42",
    )

    assert media == MediaBlob(data=b"adapter-img", mime_type="image/jpeg")


async def test_adapter_source_rejects_unsupported_dialog_and_media_fetch():
    source = AdapterBackedChannelSource(
        channel="telegram_dm",
        adapter=_NoDialogAdapter(),
    )

    with pytest.raises(UnsupportedChannelCapability):
        await source.list_conversations(workspace_id=7, channel="telegram_dm")
    with pytest.raises(UnsupportedChannelCapability):
        await source.fetch_media(
            workspace_id=7,
            conversation=ChannelConversationRef(
                channel="telegram_dm",
                external_chat_id="thread-1",
            ),
            external_message_id="42",
        )
