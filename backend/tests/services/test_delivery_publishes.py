"""Verify DeliveryService publishes MsgSent + DeliveryConfirmed events."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.event_spine import DeliveryConfirmed, MsgMediaSent, MsgSent
from app.services.channel_adapter_contract import ChannelOutboundMedia
from app.services.delivery import DeliveryService


@pytest.mark.asyncio
async def test_send_with_retry_publishes_msg_sent_and_confirmed():
    fake_spine = MagicMock()
    service = DeliveryService(
        sidecar_url="http://fake",
        sidecar_api_key="k",
        event_spine=fake_spine,
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"externalMessageId": "tg:4101:8901"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.delivery.httpx.AsyncClient", return_value=mock_client):
        result = await service._send_with_retry(
            chat_id="4101", text="Salom!", workspace_id=7,
            conversation_id=99, action_record_id=42,
        )

    assert result.success is True
    assert result.state == "confirmed"
    assert mock_client.post.await_args.kwargs["json"]["idempotencyKey"]
    calls = fake_spine.publish.call_args_list
    sent_events = [c.args[0] for c in calls if isinstance(c.args[0], MsgSent)]
    confirmed_events = [c.args[0] for c in calls if isinstance(c.args[0], DeliveryConfirmed)]
    assert len(sent_events) == 1
    assert sent_events[0].conversation_id == 99
    assert sent_events[0].action_record_id == 42
    assert sent_events[0].channel_conversation_id == "4101"
    assert len(confirmed_events) == 1
    assert confirmed_events[0].external_message_id == "tg:4101:8901"
    assert confirmed_events[0].idempotency_key != sent_events[0].idempotency_key
    assert confirmed_events[0].causation_id == sent_events[0].idempotency_key
    assert confirmed_events[0].channel_conversation_id == "4101"


@pytest.mark.asyncio
async def test_send_with_retry_publishes_sent_but_not_confirmed_on_failure():
    fake_spine = MagicMock()
    service = DeliveryService(
        sidecar_url="http://fake",
        sidecar_api_key="k",
        event_spine=fake_spine,
    )

    import httpx
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.RequestError("boom"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.delivery.httpx.AsyncClient", return_value=mock_client):
        result = await service._send_with_retry(
            chat_id="4101", text="Salom!", workspace_id=7,
            conversation_id=99, action_record_id=42,
        )

    assert result.success is False
    calls = fake_spine.publish.call_args_list
    sent_events = [c.args[0] for c in calls if isinstance(c.args[0], MsgSent)]
    confirmed_events = [c.args[0] for c in calls if isinstance(c.args[0], DeliveryConfirmed)]
    assert len(sent_events) == 1  # published BEFORE retry loop
    assert len(confirmed_events) == 0  # never confirmed


@pytest.mark.asyncio
async def test_send_timeout_returns_unknown_state():
    fake_spine = MagicMock()
    service = DeliveryService(
        sidecar_url="http://fake",
        sidecar_api_key="k",
        event_spine=fake_spine,
    )

    import httpx
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("slow"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.delivery.httpx.AsyncClient", return_value=mock_client):
        result = await service._send_with_retry(
            chat_id="4101",
            text="Salom!",
            workspace_id=7,
            conversation_id=99,
            client_idempotency_key="send-timeout-uuid",
        )

    assert result.success is False
    assert result.state == "unknown"
    assert mock_client.post.await_args.kwargs["json"]["idempotencyKey"] == "send-timeout-uuid"


@pytest.mark.asyncio
async def test_manual_send_confirm_uses_client_idempotency_key():
    fake_spine = MagicMock()
    service = DeliveryService(
        sidecar_url="http://fake",
        sidecar_api_key="k",
        event_spine=fake_spine,
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"externalMessageId": "tg:4101:8901"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.delivery.httpx.AsyncClient", return_value=mock_client):
        result = await service._send_with_retry(
            chat_id="4101",
            text="Salom!",
            workspace_id=7,
            conversation_id=99,
            client_idempotency_key="send-uuid-frontend",
        )

    assert result.success is True
    calls = fake_spine.publish.call_args_list
    sent_event = next(c.args[0] for c in calls if isinstance(c.args[0], MsgSent))
    confirmed_event = next(c.args[0] for c in calls if isinstance(c.args[0], DeliveryConfirmed))
    assert sent_event.action_record_id is None
    assert confirmed_event.action_record_id is None
    assert sent_event.idempotency_key == "send-uuid-frontend"
    assert confirmed_event.idempotency_key == "delivery:send-uuid-frontend"
    assert confirmed_event.causation_id == "send-uuid-frontend"
    assert mock_client.post.await_args.kwargs["json"]["idempotencyKey"] == "send-uuid-frontend"


@pytest.mark.asyncio
async def test_media_send_with_retry_publishes_media_sent_and_confirmed():
    fake_spine = MagicMock()
    service = DeliveryService(
        sidecar_url="http://fake",
        sidecar_api_key="k",
        event_spine=fake_spine,
    )

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"externalMessageId": "914"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.delivery.httpx.AsyncClient", return_value=mock_client):
        result = await service._send_media_with_retry(
            chat_id="4101",
            media=ChannelOutboundMedia(
                url="https://cdn.example.com/catalog/ring.jpg",
                media_type="photo",
                asset_id="asset-ring-1",
            ),
            workspace_id=7,
            conversation_id=99,
            caption="Mana rasmi",
            client_idempotency_key="media-send-uuid",
        )

    assert result.success is True
    calls = fake_spine.publish.call_args_list
    media_events = [c.args[0] for c in calls if isinstance(c.args[0], MsgMediaSent)]
    confirmed_events = [c.args[0] for c in calls if isinstance(c.args[0], DeliveryConfirmed)]
    assert len(media_events) == 1
    assert media_events[0].conversation_id == 99
    assert media_events[0].caption == "Mana rasmi"
    assert media_events[0].media_type == "photo"
    assert media_events[0].media_url == "https://cdn.example.com/catalog/ring.jpg"
    assert media_events[0].media_asset_id == "asset-ring-1"
    assert len(confirmed_events) == 1
    assert confirmed_events[0].external_message_id == "914"
    assert confirmed_events[0].causation_id == "media-send-uuid"
