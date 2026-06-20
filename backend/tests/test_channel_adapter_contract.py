from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message
from app.services.channel_adapter_contract import (
    ChannelHistorySourceUnavailable,
    ChannelInboundMessage,
    ChannelMediaRef,
    ChannelOutboundMedia,
    MockInstagramAdapter,
    TelegramChannelAdapter,
    UnsupportedChannelCapability,
    get_channel_adapter,
    normalize_channel_name,
)
from app.services.channel_sync_runtime import ChannelSyncRateLimitError
from app.services.inbound_pipeline import process_inbound_message

pytestmark = pytest.mark.asyncio


def _mock_response(status_code: int = 200, json_data: Any = None):
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_data if json_data is not None else {}
    response.raise_for_status = MagicMock()
    return response


async def test_mock_instagram_declares_unsupported_media_send():
    adapter = MockInstagramAdapter(account_id="ig-business-1")

    assert adapter.capabilities().send_message is True
    assert adapter.capabilities().send_media is False

    with pytest.raises(UnsupportedChannelCapability):
        await adapter.send_media(
            workspace_id=1,
            conversation_id="ig-thread-1",
            media=None,
            idempotency_key="media-1",
        )


async def test_mock_instagram_mark_read_updates_adapter_unread_projection():
    adapter = MockInstagramAdapter(account_id="ig-business-1")
    adapter.seed_inbound(
        ChannelInboundMessage(
            workspace_id=1,
            channel="instagram_dm",
            account_id="ig-business-1",
            conversation_id="ig-thread-1",
            message_id="ig-msg-1",
            sender_id="ig-user-1",
            sender_name="Lead",
            text="one",
            sent_at=1_700_000_000,
        )
    )
    adapter.seed_inbound(
        ChannelInboundMessage(
            workspace_id=1,
            channel="instagram_dm",
            account_id="ig-business-1",
            conversation_id="ig-thread-1",
            message_id="ig-msg-2",
            sender_id="ig-user-1",
            sender_name="Lead",
            text="two",
            sent_at=1_700_000_001,
        )
    )

    before = await adapter.list_conversations(workspace_id=1)
    await adapter.mark_read(
        workspace_id=1,
        conversation_id="ig-thread-1",
        message_id="ig-msg-1",
    )
    after = await adapter.list_conversations(workspace_id=1)

    assert before[0].unread_count == 2
    assert after[0].unread_count == 1


async def test_channel_adapter_registry_normalizes_supported_channels():
    assert normalize_channel_name(" dm ") == "telegram_dm"
    assert normalize_channel_name("TELEGRAM_DM") == "telegram_dm"
    assert normalize_channel_name("instagram_dm") == "instagram_dm"

    telegram = get_channel_adapter("dm")
    instagram = get_channel_adapter("instagram_dm", account_id="ig-business-1")

    assert isinstance(telegram, TelegramChannelAdapter)
    assert telegram.channel == "telegram_dm"
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    assert isinstance(instagram, InstagramChannelAdapter)
    assert instagram.channel == "instagram_dm"

    with pytest.raises(UnsupportedChannelCapability):
        get_channel_adapter("whatsapp_dm")


@pytest.mark.parametrize(
    "adapter",
    [
        TelegramChannelAdapter(),
        MockInstagramAdapter(account_id="ig-business-1"),
    ],
)
async def test_channel_adapter_send_read_and_media_capabilities_are_explicit(adapter):
    if isinstance(adapter, TelegramChannelAdapter):
        adapter.sidecar_api_key = "test-key"
        adapter.sidecar_url = "http://sidecar.test"

    capabilities = adapter.capabilities()

    if capabilities.send_message:
        if isinstance(adapter, TelegramChannelAdapter):
            send_response = _mock_response(200, {"externalMessageId": "tg:thread-1:1"})
            adapter_post = AsyncMock(return_value=send_response)
        else:
            adapter_post = None
        if adapter_post is not None:
            with patch("httpx.AsyncClient.post", adapter_post):
                result = await adapter.send_message(
                    workspace_id=1,
                    conversation_id="thread-1",
                    text="Salom",
                    idempotency_key=f"{adapter.channel}:send:1",
                )
        else:
            result = await adapter.send_message(
                workspace_id=1,
                conversation_id="thread-1",
                text="Salom",
                idempotency_key=f"{adapter.channel}:send:1",
            )
        assert result.external_message_id
        assert result.status.status in {"sent", "delivered", "accepted"}
    else:
        with pytest.raises(UnsupportedChannelCapability):
            await adapter.send_message(
                workspace_id=1,
                conversation_id="thread-1",
                text="Salom",
                idempotency_key=f"{adapter.channel}:send:1",
            )

    if capabilities.mark_read:
        if isinstance(adapter, TelegramChannelAdapter):
            read_response = _mock_response(200, {"ok": True})
            adapter_post = AsyncMock(return_value=read_response)

            with patch("httpx.AsyncClient.post", adapter_post):
                await adapter.mark_read(
                    workspace_id=1,
                    conversation_id="thread-1",
                    message_id="1",
                )
        else:
            await adapter.mark_read(
                workspace_id=1,
                conversation_id="thread-1",
                message_id="msg-1",
            )
    else:
        with pytest.raises(UnsupportedChannelCapability):
            await adapter.mark_read(
                workspace_id=1,
                conversation_id="thread-1",
                message_id="msg-1",
            )

    if capabilities.send_media:
        media_response = _mock_response(200, {"externalMessageId": "tg:thread-1:media-1"})
        adapter_post = AsyncMock(return_value=media_response)
        if isinstance(adapter, TelegramChannelAdapter):
            with patch("httpx.AsyncClient.post", adapter_post):
                media_result = await adapter.send_media(
                    workspace_id=1,
                    conversation_id="thread-1",
                    media=ChannelOutboundMedia(
                        url="https://cdn.example.com/catalog/ring.jpg",
                        media_type="photo",
                        mime_type="image/jpeg",
                        asset_id="catalog-image-1",
                    ),
                    caption="Mana rasmi",
                    idempotency_key=f"{adapter.channel}:media:1",
                )
            _, kwargs = adapter_post.await_args
            assert kwargs["json"] == {
                "workspaceId": 1,
                "chatId": "thread-1",
                "caption": "Mana rasmi",
                "media": {
                    "url": "https://cdn.example.com/catalog/ring.jpg",
                    "mediaType": "photo",
                    "mimeType": "image/jpeg",
                    "assetId": "catalog-image-1",
                },
                "idempotencyKey": f"{adapter.channel}:media:1",
            }
        else:
            media_result = await adapter.send_media(
                workspace_id=1,
                conversation_id="thread-1",
                media=ChannelOutboundMedia(
                    url="https://cdn.example.com/catalog/ring.jpg",
                    media_type="photo",
                    asset_id="catalog-image-1",
                ),
                caption="Mana rasmi",
                idempotency_key=f"{adapter.channel}:media:1",
            )
        assert media_result.external_message_id
        assert media_result.status.status in {"sent", "delivered", "accepted"}
    else:
        media = ChannelMediaRef(
            channel=adapter.channel,
            conversation_id="thread-1",
            message_id="msg-1",
        )
        with pytest.raises(UnsupportedChannelCapability):
            await adapter.send_media(
                workspace_id=1,
                conversation_id="thread-1",
                media=media,
                caption=None,
                idempotency_key=f"{adapter.channel}:media:1",
            )


async def test_telegram_adapter_fetch_history_reads_sidecar_messages():
    adapter = TelegramChannelAdapter(sidecar_url="http://sidecar.test", sidecar_api_key="key")
    response = _mock_response(
        200,
        [
            {
                "sellerUserId": "seller-1",
                "chatId": "thread-1",
                "senderId": "customer-1",
                "senderName": "Customer",
                "messageId": "42",
                "text": "Salom",
                "date": 1_700_000_000,
                "isOutgoing": False,
                "mediaType": "photo",
                "mediaMetadata": {"mime_type": "image/jpeg"},
                "textEntities": [{"type": "bold", "offset": "0", "length": "5"}],
                "replyToMsgId": "41",
                "groupedId": "9001",
            }
        ],
    )
    adapter_get = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient.get", adapter_get):
        messages = await adapter.fetch_history(
            workspace_id=7,
            conversation_id="thread-1",
            after_message_id="40",
            limit=25,
        )

    adapter_get.assert_awaited_once()
    _, kwargs = adapter_get.await_args
    assert kwargs["params"] == {
        "workspaceId": 7,
        "chatId": "thread-1",
        "limit": 25,
        "afterId": "40",
    }
    assert kwargs["headers"]["X-Sidecar-Key"] == "key"
    assert len(messages) == 1
    message = messages[0]
    assert message.channel == "telegram_dm"
    assert message.conversation_id == "thread-1"
    assert message.message_id == "42"
    assert message.media_type == "photo"
    assert message.media_metadata == {"mime_type": "image/jpeg", "grouped_id": "9001"}
    assert message.text_entities == [{"type": "bold", "offset": 0, "length": 5}]
    assert message.reply_to_message_id == "41"


async def test_telegram_adapter_lists_dialogs_from_sidecar():
    adapter = TelegramChannelAdapter(sidecar_url="http://sidecar.test", sidecar_api_key="key")
    response = _mock_response(
        200,
        [
            {
                "chatId": "thread-1",
                "title": "Customer",
                "unreadCount": "2",
                "topMessageId": "42",
                "lastMessageText": "Salom",
                "lastMessageDate": "1700000000",
                "lastMessageIsOutgoing": False,
            }
        ],
    )
    adapter_get = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient.get", adapter_get):
        dialogs = await adapter.list_conversations(workspace_id=7, limit=25)

    adapter_get.assert_awaited_once()
    _, kwargs = adapter_get.await_args
    assert kwargs["params"] == {"workspaceId": 7, "limit": 25}
    assert kwargs["headers"]["X-Sidecar-Key"] == "key"
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert dialog.external_chat_id == "thread-1"
    assert dialog.title == "Customer"
    assert dialog.unread_count == 2
    assert dialog.top_message_id == 42
    assert dialog.last_message_text == "Salom"
    assert dialog.last_message_date == 1_700_000_000
    assert dialog.last_message_is_outgoing is False


async def test_telegram_adapter_fetches_media_blob_from_sidecar():
    adapter = TelegramChannelAdapter(sidecar_url="http://sidecar.test", sidecar_api_key="key")
    response = _mock_response(200, {})
    response.content = b"image-bytes"
    response.headers = {"content-type": "image/jpeg"}
    adapter_post = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient.post", adapter_post):
        blob = await adapter.fetch_media_blob(
            workspace_id=7,
            media=ChannelMediaRef(
                channel="telegram_dm",
                conversation_id="thread-1",
                message_id="42",
            ),
            thumb=True,
        )

    adapter_post.assert_awaited_once()
    _, kwargs = adapter_post.await_args
    assert kwargs["json"] == {
        "workspaceId": 7,
        "chatId": "thread-1",
        "messageId": "42",
        "thumb": True,
    }
    assert kwargs["headers"]["X-Sidecar-Key"] == "key"
    assert blob.data == b"image-bytes"
    assert blob.mime_type == "image/jpeg"


async def test_telegram_adapter_fetches_custom_emoji_preview_from_sidecar():
    adapter = TelegramChannelAdapter(sidecar_url="http://sidecar.test", sidecar_api_key="key")
    response = _mock_response(200, {})
    response.content = b"RIFF....WEBP"
    response.headers = {"content-type": "image/webp"}
    adapter_get = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient.get", adapter_get):
        blob = await adapter.fetch_custom_emoji_preview(
            workspace_id=7,
            document_id="123456789",
        )

    adapter_get.assert_awaited_once()
    _, kwargs = adapter_get.await_args
    assert kwargs["params"] == {"workspaceId": 7, "documentId": "123456789"}
    assert kwargs["headers"]["X-Sidecar-Key"] == "key"
    assert blob.data == b"RIFF....WEBP"
    assert blob.mime_type == "image/webp"


async def test_telegram_adapter_fetch_history_surfaces_rate_limits():
    adapter = TelegramChannelAdapter(sidecar_url="http://sidecar.test", sidecar_api_key="key")
    response = _mock_response(429, {"error": "rate_limited"})
    response.headers = {"retry-after": "12"}

    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=response)),
        pytest.raises(ChannelSyncRateLimitError) as exc,
    ):
        await adapter.fetch_history(
            workspace_id=7,
            conversation_id="thread-1",
            limit=25,
        )

    assert exc.value.retry_after_seconds == 12
    assert exc.value.channel == "telegram_dm"
    assert exc.value.operation == "messages"


async def test_telegram_adapter_send_message_surfaces_rate_limits():
    adapter = TelegramChannelAdapter(sidecar_url="http://sidecar.test", sidecar_api_key="key")
    response = _mock_response(429, {"error": "rate_limited", "retryAfter": 17})

    with (
        patch("httpx.AsyncClient.post", AsyncMock(return_value=response)),
        pytest.raises(ChannelSyncRateLimitError) as exc,
    ):
        await adapter.send_message(
            workspace_id=7,
            conversation_id="thread-1",
            text="Salom",
            idempotency_key="send-rate-limit-key",
        )

    assert exc.value.retry_after_seconds == 17
    assert exc.value.channel == "telegram_dm"
    assert exc.value.operation == "send"


async def test_telegram_adapter_send_message_forwards_reply_to_message_id():
    adapter = TelegramChannelAdapter(sidecar_url="http://sidecar.test", sidecar_api_key="key")
    response = _mock_response(200, {"externalMessageId": "889"})
    post = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient.post", post):
        await adapter.send_message(
            workspace_id=7,
            conversation_id="5924086090",
            text="Salom",
            idempotency_key="reply-send-key",
            reply_to_message_id=1440,
        )

    payload = post.await_args.kwargs["json"]
    assert payload["replyToMsgId"] == 1440


async def test_telegram_adapter_send_reaction_posts_to_sidecar():
    adapter = TelegramChannelAdapter(sidecar_url="http://sidecar.test", sidecar_api_key="key")
    response = _mock_response(200, {"externalMessageId": "1440"})
    post = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient.post", post):
        result = await adapter.send_reaction(
            workspace_id=1,
            conversation_id="thread-1",
            message_id="1440",
            reaction="👍",
            idempotency_key="telegram_dm:react:1",
        )

    assert post.await_args.kwargs["json"] == {
        "workspaceId": 1,
        "chatId": "thread-1",
        "messageId": "1440",
        "reaction": "👍",
        "idempotencyKey": "telegram_dm:react:1",
    }
    assert result.external_message_id == "1440"
    assert result.status.status == "sent"


async def test_telegram_adapter_fetch_history_surfaces_unavailable_sidecar():
    adapter = TelegramChannelAdapter(sidecar_url="http://sidecar.test", sidecar_api_key="key")
    request = httpx.Request("GET", "http://sidecar.test/messages")
    response = httpx.Response(503, request=request)

    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=response)),
        pytest.raises(ChannelHistorySourceUnavailable),
    ):
        await adapter.fetch_history(
            workspace_id=7,
            conversation_id="thread-1",
            limit=25,
        )


@pytest.mark.parametrize(
    ("adapter", "payload", "expected_channel"),
    [
        (
            TelegramChannelAdapter(),
            {
                "workspaceId": 1,
                "sellerUserId": "tg-seller-1",
                "chatId": "777001",
                "senderId": "555001",
                "senderName": "Telegram Customer",
                "messageId": "9001",
                "text": "Narxi qancha?",
                "date": 1_700_000_000,
                "isOutgoing": False,
            },
            "telegram_dm",
        ),
        (
            MockInstagramAdapter(account_id="ig-business-1"),
            {
                "workspaceId": 1,
                "conversationId": "ig-thread-1",
                "senderId": "ig-user-1",
                "senderName": "Instagram Customer",
                "messageId": "ig-msg-1",
                "text": "Narxi qancha?",
                "date": 1_700_000_000,
                "isOutgoing": False,
            },
            "instagram_dm",
        ),
    ],
)
async def test_channel_adapter_events_use_same_inbound_to_reply_path(
    db_session,
    workspace,
    event_spine,
    adapter,
    payload,
    expected_channel,
):
    payload = dict(payload)
    payload["workspaceId"] = workspace.id
    dispatcher = SimpleNamespace(enqueue_message=AsyncMock())

    events = await adapter.receive_events(payload)
    assert len(events) == 1
    event = events[0]
    assert event.channel == expected_channel
    await event_spine.append(event.to_event())

    result = await process_inbound_message(
        raw_payload=event.to_bridge_payload(),
        workspace=workspace,
        session=db_session,
        conversation_turn_runner=dispatcher,
        channel=event.channel,
    )

    assert result.status == "persisted"
    assert result.reply_generation_triggered is True
    dispatcher.enqueue_message.assert_awaited_once()

    conversation = await db_session.scalar(
        select(Conversation).where(Conversation.id == result.conversation_id)
    )
    message = await db_session.scalar(
        select(Message).where(Message.id == result.message_id)
    )
    assert conversation is not None
    assert message is not None
    assert conversation.channel == expected_channel
    assert message.channel == expected_channel

    if expected_channel == "instagram_dm":
        customer = await db_session.scalar(
            select(Customer).where(Customer.id == conversation.customer_id)
        )
        assert customer is not None
        assert customer.channel == "instagram_dm"
        assert customer.external_id == "ig-user-1"
        assert conversation.external_chat_id == "ig-thread-1"
        assert message.external_message_id == "ig-msg-1"
        assert message.telegram_message_id is None


def test_outbound_media_document_payload_skips_url_validation():
    from app.services.channel_adapter_contract import ChannelOutboundMedia

    media = ChannelOutboundMedia(
        url="vault://-1001234567890/42",
        media_type="video",
        mime_type="video/mp4",
        file_name="kozimxon.mp4",
        asset_id="intro_kozimxon",
        vault_peer="-1001234567890",
        vault_message_id=42,
    )
    payload = media.to_sidecar_payload()
    assert payload["document"] == {"vaultPeer": "-1001234567890", "vaultMessageId": 42}
    assert payload["mediaType"] == "video"
    assert payload["mimeType"] == "video/mp4"
    assert payload["fileName"] == "kozimxon.mp4"
    assert payload["assetId"] == "intro_kozimxon"
    assert "url" not in payload  # document assets carry no url


def test_outbound_media_url_payload_unchanged():
    from app.services.channel_adapter_contract import ChannelOutboundMedia

    media = ChannelOutboundMedia(url="https://cdn.example.com/x.jpg", media_type="photo")
    payload = media.to_sidecar_payload()
    assert payload == {"url": "https://cdn.example.com/x.jpg", "mediaType": "photo"}


async def test_send_media_422_vault_unavailable_raises_permanent():
    from app.services.channel_adapter_contract import (
        ChannelOutboundMedia,
        PermanentChannelSendError,
        TelegramChannelAdapter,
    )

    media = ChannelOutboundMedia(
        url="vault://-100123/42",
        media_type="video",
        vault_peer="-100123",
        vault_message_id=42,
    )

    adapter = TelegramChannelAdapter(sidecar_url="http://sidecar.test", sidecar_api_key="key")
    response = _mock_response(422, {"error": "vault_document_unavailable"})
    response.raise_for_status = MagicMock(
        side_effect=AssertionError("raise_for_status should not be reached")
    )
    post = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient.post", post), pytest.raises(PermanentChannelSendError):
        await adapter.send_media(
            workspace_id=1,
            conversation_id="123",
            media=media,
            caption="x",
            idempotency_key="k",
        )
