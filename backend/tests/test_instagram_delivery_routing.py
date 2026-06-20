"""DeliveryService routes instagram_dm sends through the Graph adapter
and enforces the 24h reply window honestly."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message, SenderType
from app.services.channel_adapter_contract import ChannelOutboundMedia
from app.services.delivery import DeliveryService

pytestmark = pytest.mark.asyncio


async def _make_instagram_conversation(
    db_session,
    workspace,
    *,
    last_inbound_age_hours: float,
    external_id: str = "999000111",
    external_chat_id: str | None = "999000111",
):
    customer = Customer(
        workspace_id=workspace.id,
        external_id=external_id,
        channel="instagram_dm",
        display_name="IG Customer",
    )
    db_session.add(customer)
    await db_session.flush()
    conversation = Conversation(
        workspace_id=workspace.id,
        customer_id=customer.id,
        channel="instagram_dm",
        external_chat_id=external_chat_id,
        telegram_chat_id=None,
    )
    db_session.add(conversation)
    await db_session.flush()
    message = Message(
        conversation_id=conversation.id,
        channel="instagram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="Narxi qancha?",
        created_at=datetime.now(UTC) - timedelta(hours=last_inbound_age_hours),
    )
    db_session.add(message)
    await db_session.flush()
    return conversation


def _graph_post_mock(message_id: str = "mid.out9"):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"recipient_id": "999000111", "message_id": message_id}
    response.raise_for_status.return_value = None
    post_mock = AsyncMock(return_value=response)

    @asynccontextmanager
    async def _client(*args, **kwargs):
        client = MagicMock()
        client.post = post_mock
        yield client

    return _client, post_mock


def _patched_ig_adapter(factory):
    """Patch DeliveryService's InstagramChannelAdapter so its httpx client is mocked."""
    from app.services import delivery as delivery_module

    real_adapter_cls = delivery_module.InstagramChannelAdapter

    def _adapter_with_mock(**kwargs):
        kwargs["http_client_factory"] = factory
        return real_adapter_cls(**kwargs)

    return patch.object(
        delivery_module, "InstagramChannelAdapter", side_effect=_adapter_with_mock
    )


async def test_instagram_send_routes_through_graph_adapter(db_session, workspace):
    workspace.instagram_access_token = "IGAA-test-token"
    workspace.instagram_page_id = "17841400000000000"
    await db_session.flush()
    conversation = await _make_instagram_conversation(db_session, workspace, last_inbound_age_hours=1)

    factory, post_mock = _graph_post_mock()
    service = DeliveryService(sidecar_url="http://sidecar.test", sidecar_api_key="k")
    # Patch the adapter class used by DeliveryService so its httpx client is mocked.
    from app.services import delivery as delivery_module

    real_adapter_cls = delivery_module.InstagramChannelAdapter

    def _adapter_with_mock(**kwargs):
        kwargs["http_client_factory"] = factory
        return real_adapter_cls(**kwargs)

    with patch.object(delivery_module, "InstagramChannelAdapter", side_effect=_adapter_with_mock):
        result = await service.deliver_message(
            conversation.id,
            "Salom! Narxi 4 900 000 so'm.",
            db=db_session,
            workspace_id=workspace.id,
            delay_override_seconds=0.0,
            typing_indicator=False,
        )

    assert result.success is True
    assert result.external_message_id == "mid.out9"
    sent_json = post_mock.call_args.kwargs["json"]
    assert sent_json["recipient"] == {"id": "999000111"}


async def test_instagram_send_blocked_when_window_closed(db_session, workspace):
    workspace.instagram_access_token = "IGAA-test-token"
    await db_session.flush()
    conversation = await _make_instagram_conversation(db_session, workspace, last_inbound_age_hours=30)

    service = DeliveryService(sidecar_url="http://sidecar.test", sidecar_api_key="k")
    result = await service.deliver_message(
        conversation.id,
        "Kech bo'lsa ham javob.",
        db=db_session,
        workspace_id=workspace.id,
        delay_override_seconds=0.0,
        typing_indicator=False,
    )

    assert result.success is False
    assert result.error == "instagram_window_closed"
    assert result.state == "failed"
    # Honest owner card queued (never a silent drop) — #413 machinery.
    projection = (
        await db_session.execute(
            select(BusinessBrainProjectionRecord).where(
                BusinessBrainProjectionRecord.workspace_id == workspace.id,
                BusinessBrainProjectionRecord.projection_type == "owner_notification",
            )
        )
    ).scalars().first()
    assert projection is not None
    assert projection.state["status"] == "queued"


async def test_instagram_send_fails_clean_without_token(db_session, workspace):
    workspace.instagram_access_token = None
    await db_session.flush()
    conversation = await _make_instagram_conversation(db_session, workspace, last_inbound_age_hours=1)

    service = DeliveryService(sidecar_url="http://sidecar.test", sidecar_api_key="k")
    result = await service.deliver_message(
        conversation.id,
        "Salom!",
        db=db_session,
        workspace_id=workspace.id,
        delay_override_seconds=0.0,
        typing_indicator=False,
    )
    assert result.success is False
    assert result.error == "instagram_not_connected"


async def test_instagram_media_send_routes_and_window_gates(db_session, workspace):
    workspace.instagram_access_token = "IGAA-test-token"
    workspace.instagram_page_id = "17841400000000000"
    await db_session.flush()
    conversation = await _make_instagram_conversation(
        db_session, workspace, last_inbound_age_hours=1
    )

    factory, post_mock = _graph_post_mock(message_id="mid.media1")
    service = DeliveryService(sidecar_url="http://sidecar.test", sidecar_api_key="k")
    media = ChannelOutboundMedia(url="https://cdn.test/p.jpg", media_type="photo")

    with _patched_ig_adapter(factory):
        result = await service.deliver_media(
            conversation.id,
            media,
            db=db_session,
            workspace_id=workspace.id,
            delay_override_seconds=0.0,
            typing_indicator=False,
        )

    assert result.success is True
    assert result.external_message_id == "mid.media1"
    sent_json = post_mock.call_args_list[0].kwargs["json"]
    assert sent_json["recipient"] == {"id": "999000111"}
    assert sent_json["message"]["attachment"]["payload"]["url"] == "https://cdn.test/p.jpg"

    # Window-closed media send is honestly blocked, same as text sends.
    stale_conversation = await _make_instagram_conversation(
        db_session, workspace, last_inbound_age_hours=30,
        external_id="999000222", external_chat_id="999000222",
    )
    blocked = await service.deliver_media(
        stale_conversation.id,
        media,
        db=db_session,
        workspace_id=workspace.id,
        delay_override_seconds=0.0,
        typing_indicator=False,
    )
    assert blocked.success is False
    assert blocked.error == "instagram_window_closed"
    assert blocked.state == "failed"


async def test_instagram_send_skips_sidecar_side_calls(db_session, workspace):
    workspace.instagram_access_token = "IGAA-test-token"
    workspace.instagram_page_id = "17841400000000000"
    await db_session.flush()
    conversation = await _make_instagram_conversation(
        db_session, workspace, last_inbound_age_hours=1
    )

    factory, _post_mock = _graph_post_mock()
    service = DeliveryService(sidecar_url="http://sidecar.test", sidecar_api_key="k")
    typing_mock = AsyncMock()
    read_mock = AsyncMock()

    with (
        _patched_ig_adapter(factory),
        patch.object(DeliveryService, "_send_typing", new=typing_mock),
        patch.object(DeliveryService, "_mark_read", new=read_mock),
    ):
        result = await service.deliver_message(
            conversation.id,
            "Salom!",
            db=db_session,
            workspace_id=workspace.id,
            delay_override_seconds=0.0,
            typing_indicator=True,
        )

    assert result.success is True
    assert typing_mock.await_count == 0
    assert read_mock.await_count == 0


async def test_window_closed_owner_card_is_idempotent_per_hour(db_session, workspace):
    workspace.instagram_access_token = "IGAA-test-token"
    await db_session.flush()
    conversation = await _make_instagram_conversation(
        db_session, workspace, last_inbound_age_hours=30
    )

    service = DeliveryService(sidecar_url="http://sidecar.test", sidecar_api_key="k")
    for _ in range(2):
        result = await service.deliver_message(
            conversation.id,
            "Kech bo'lsa ham javob.",
            db=db_session,
            workspace_id=workspace.id,
            delay_override_seconds=0.0,
            typing_indicator=False,
        )
        assert result.error == "instagram_window_closed"

    projections = (
        await db_session.execute(
            select(BusinessBrainProjectionRecord).where(
                BusinessBrainProjectionRecord.workspace_id == workspace.id,
                BusinessBrainProjectionRecord.projection_type == "owner_notification",
            )
        )
    ).scalars().all()
    assert len(projections) == 1
    assert projections[0].state["bot_payload"]["customer_label"] == "IG Customer"


async def test_instagram_send_without_external_chat_id_fails_clean(db_session, workspace):
    workspace.instagram_access_token = "IGAA-test-token"
    await db_session.flush()
    conversation = await _make_instagram_conversation(
        db_session, workspace, last_inbound_age_hours=1, external_chat_id=None
    )

    service = DeliveryService(sidecar_url="http://sidecar.test", sidecar_api_key="k")
    result = await service.deliver_message(
        conversation.id,
        "Salom!",
        db=db_session,
        workspace_id=workspace.id,
        delay_override_seconds=0.0,
        typing_indicator=False,
    )
    assert result.success is False
    assert result.error == "no_external_chat_id"
    assert result.state == "failed"
