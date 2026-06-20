"""Instagram channel adapter + config tests."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import get_settings

pytestmark = pytest.mark.asyncio


async def test_settings_expose_instagram_config_defaults():
    settings = get_settings()
    assert settings.instagram_app_id == ""
    assert settings.instagram_app_secret == ""
    assert settings.instagram_webhook_verify_token == ""
    assert settings.instagram_graph_base == "https://graph.instagram.com"
    assert settings.instagram_redirect_uri.endswith("/api/instagram/auth/callback")


def _mock_http_factory(response_json: dict, status_code: int = 200):
    """Return (factory, post_mock) — factory mimics httpx.AsyncClient context manager."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = response_json
    response.raise_for_status.return_value = None
    post_mock = AsyncMock(return_value=response)

    @asynccontextmanager
    async def _client(*args, **kwargs):
        client = MagicMock()
        client.post = post_mock
        client.get = AsyncMock(return_value=response)
        yield client

    return _client, post_mock


async def test_factory_returns_real_instagram_adapter():
    from app.services.channel_adapter_contract import get_channel_adapter
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    adapter = get_channel_adapter("instagram_dm", account_id="17841400000000000")
    assert isinstance(adapter, InstagramChannelAdapter)
    assert adapter.channel == "instagram_dm"


async def test_receive_events_parses_inbound_dm():
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    adapter = InstagramChannelAdapter(account_id="17841400000000000")
    payload = {
        "workspaceId": 7,
        "entry": {
            "id": "17841400000000000",
            "time": 1_750_000_000,
            "messaging": [
                {
                    "sender": {"id": "1234567890"},
                    "recipient": {"id": "17841400000000000"},
                    "timestamp": 1_750_000_000_123,
                    "message": {"mid": "mid.abc123", "text": "Narxi qancha?"},
                }
            ],
        },
    }
    messages = await adapter.receive_events(payload)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.workspace_id == 7
    assert msg.channel == "instagram_dm"
    assert msg.conversation_id == "1234567890"  # counterpart IGSID
    assert msg.message_id == "mid.abc123"
    assert msg.text == "Narxi qancha?"
    assert msg.is_outgoing is False
    assert msg.sent_at == pytest.approx(1_750_000_000.123)


async def test_receive_events_marks_echo_as_outgoing_and_keeps_counterpart_thread():
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    adapter = InstagramChannelAdapter(account_id="17841400000000000")
    payload = {
        "workspaceId": 7,
        "entry": {
            "id": "17841400000000000",
            "messaging": [
                {
                    "sender": {"id": "17841400000000000"},
                    "recipient": {"id": "1234567890"},
                    "timestamp": 1_750_000_001_000,
                    "message": {"mid": "mid.echo1", "text": "Salom!", "is_echo": True},
                }
            ],
        },
    }
    messages = await adapter.receive_events(payload)
    assert messages[0].is_outgoing is True
    assert messages[0].conversation_id == "1234567890"


async def test_receive_events_parses_image_attachment():
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    adapter = InstagramChannelAdapter(account_id="17841400000000000")
    payload = {
        "workspaceId": 7,
        "entry": {
            "id": "17841400000000000",
            "messaging": [
                {
                    "sender": {"id": "555"},
                    "recipient": {"id": "17841400000000000"},
                    "timestamp": 1_750_000_002_000,
                    "message": {
                        "mid": "mid.img1",
                        "attachments": [
                            {"type": "image", "payload": {"url": "https://cdn.example/img.jpg"}}
                        ],
                    },
                }
            ],
        },
    }
    msg = (await adapter.receive_events(payload))[0]
    assert msg.media_type == "photo"
    assert msg.media_metadata == {
        "instagram_attachment_type": "image",
        "url": "https://cdn.example/img.jpg",
    }


async def test_send_message_posts_to_graph_and_returns_result():
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    factory, post_mock = _mock_http_factory({"recipient_id": "555", "message_id": "mid.out1"})
    adapter = InstagramChannelAdapter(
        account_id="17841400000000000",
        access_token="IGAA-test-token",
        http_client_factory=factory,
    )
    result = await adapter.send_message(
        workspace_id=7,
        conversation_id="555",
        text="Salom!",
        idempotency_key="ig:555:1",
    )
    assert result.external_message_id == "mid.out1"
    assert result.status.status == "sent"
    call = post_mock.call_args
    assert call.args[0] == "https://graph.instagram.com/v23.0/me/messages"
    assert call.kwargs["json"] == {"recipient": {"id": "555"}, "message": {"text": "Salom!"}}
    assert call.kwargs["headers"]["Authorization"] == "Bearer IGAA-test-token"


async def test_send_private_reply_targets_comment_id():
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    factory, post_mock = _mock_http_factory({"recipient_id": "555", "message_id": "mid.pr1"})
    adapter = InstagramChannelAdapter(
        account_id="17841400000000000",
        access_token="IGAA-test-token",
        http_client_factory=factory,
    )
    result = await adapter.send_private_reply(
        workspace_id=7,
        comment_id="1798xxxcomment",
        text="Salom! DMda yozdim.",
        idempotency_key="igpr:1798xxxcomment",
    )
    assert result.external_message_id == "mid.pr1"
    assert post_mock.call_args.kwargs["json"]["recipient"] == {"comment_id": "1798xxxcomment"}


async def test_send_without_token_raises_unsupported():
    from app.services.channel_adapter_contract import UnsupportedChannelCapability
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    adapter = InstagramChannelAdapter(account_id="17841400000000000")
    with pytest.raises(UnsupportedChannelCapability):
        await adapter.send_message(
            workspace_id=7, conversation_id="555", text="x", idempotency_key="k"
        )


async def test_capabilities_are_honest():
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    caps = InstagramChannelAdapter(account_id="x").capabilities()
    assert caps.send_message is True
    assert caps.send_media is True
    assert caps.mark_read is False
    assert caps.fetch_history is False
    assert caps.fetch_media_blob is False
    assert caps.typing_indicator is False


async def test_send_media_posts_attachment_then_caption():
    from app.services.channel_adapter_contract import ChannelOutboundMedia
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    factory, post_mock = _mock_http_factory({"recipient_id": "555", "message_id": "mid.m1"})
    adapter = InstagramChannelAdapter(
        account_id="17841400000000000",
        access_token="IGAA-test-token",
        http_client_factory=factory,
    )
    result = await adapter.send_media(
        workspace_id=7,
        conversation_id="555",
        media=ChannelOutboundMedia(url="https://cdn.example/p.jpg", media_type="photo"),
        caption="Mana rasm",
        idempotency_key="ig:555:m1",
    )
    assert result.external_message_id == "mid.m1"
    assert post_mock.call_count == 2
    first, second = post_mock.call_args_list
    assert (
        first.kwargs["json"]["message"]["attachment"]["payload"]["url"]
        == "https://cdn.example/p.jpg"
    )
    assert second.kwargs["json"]["message"] == {"text": "Mana rasm"}


async def test_send_media_caption_failure_does_not_raise():
    import httpx as _httpx

    from app.services.channel_adapter_contract import ChannelOutboundMedia
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    ok = MagicMock()
    ok.status_code = 200
    ok.json.return_value = {"message_id": "mid.m2"}
    ok.raise_for_status.return_value = None

    bad = MagicMock()
    bad.status_code = 500
    bad.raise_for_status.side_effect = _httpx.HTTPStatusError(
        "500", request=MagicMock(), response=bad
    )
    post_mock = AsyncMock(side_effect=[ok, bad])

    @asynccontextmanager
    async def factory(*args, **kwargs):
        client = MagicMock()
        client.post = post_mock
        yield client

    adapter = InstagramChannelAdapter(
        account_id="x", access_token="t", http_client_factory=factory
    )
    result = await adapter.send_media(
        workspace_id=7,
        conversation_id="555",
        media=ChannelOutboundMedia(url="https://cdn.example/p.jpg", media_type="photo"),
        caption="Mana rasm",
        idempotency_key="ig:555:m2",
    )
    assert result.external_message_id == "mid.m2"  # media result survives caption failure
    assert post_mock.call_count == 2


async def test_send_rate_limited_raises_channel_sync_rate_limit():
    from app.services.channel_sync_runtime import ChannelSyncRateLimitError
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    factory, _ = _mock_http_factory({"error": "rate"}, status_code=429)
    adapter = InstagramChannelAdapter(
        account_id="x", access_token="t", http_client_factory=factory
    )
    with pytest.raises(ChannelSyncRateLimitError):
        await adapter.send_message(
            workspace_id=7, conversation_id="555", text="x", idempotency_key="k"
        )


async def test_receive_events_skips_read_receipts_and_missing_mid():
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    adapter = InstagramChannelAdapter(account_id="17841400000000000")
    payload = {
        "workspaceId": 7,
        "entry": {
            "id": "17841400000000000",
            "messaging": [
                {"sender": {"id": "1"}, "recipient": {"id": "2"}, "read": {"mid": "x"}},
                {"sender": {"id": "1"}, "recipient": {"id": "2"}, "message": {}},
            ],
        },
    }
    assert await adapter.receive_events(payload) == []


async def test_mark_read_raises_unsupported():
    from app.services.channel_adapter_contract import UnsupportedChannelCapability
    from app.services.instagram_channel_adapter import InstagramChannelAdapter

    adapter = InstagramChannelAdapter(account_id="x")
    with pytest.raises(UnsupportedChannelCapability):
        await adapter.mark_read(workspace_id=7, conversation_id="555", message_id="1")
