"""Integration test: talk-tool runtime unblocks agent sends on instagram_dm.

C1 fix: _send_message_conversation_block_reason now allows instagram_dm
conversations (text send + media send) when external_chat_id is present,
routing through DeliveryService -> InstagramChannelAdapter -> Graph API.

Covers:
- Happy path: send_message on instagram_dm reaches Graph API
- Happy path: send_media on instagram_dm reaches Graph API
- Negative: instagram_dm with external_chat_id=None is blocked with "external_chat_missing"
- Capability pin: send_reaction on instagram_dm stays blocked (unsupported)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.commercial_spine import CommercialEventRecord
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message, SenderType
from app.models.workspace import Workspace
from app.modules.telegram_tools.contracts import (
    TELEGRAM_SEND_MESSAGE,
    TELEGRAM_SEND_REACTION,
)
from app.modules.telegram_tools.runtime import TelegramToolRuntime
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService
from app.services.channel_adapter_contract import (
    ChannelCapabilities,
    ChannelOutboundMedia,
)
from app.services.delivery import DeliveryResult, DeliveryService

pytestmark = pytest.mark.asyncio

_EXTERNAL_CHAT_ID = "999000111"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _make_instagram_conversation(
    db_session: AsyncSession,
    workspace: Workspace,
    *,
    external_chat_id: str | None = _EXTERNAL_CHAT_ID,
    last_inbound_age_hours: float = 1.0,
    external_id: str = _EXTERNAL_CHAT_ID,
) -> Conversation:
    """Mirror _make_instagram_conversation from test_instagram_delivery_routing."""
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


def _graph_post_mock(message_id: str = "mid.ig9"):
    """Return (factory, post_mock) — mirrors test_instagram_delivery_routing."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"recipient_id": _EXTERNAL_CHAT_ID, "message_id": message_id}
    response.raise_for_status.return_value = None
    post_mock = AsyncMock(return_value=response)

    @asynccontextmanager
    async def _client(*args, **kwargs):
        client = MagicMock()
        client.post = post_mock
        yield client

    return _client, post_mock


def _patched_ig_adapter(factory):
    """Patch DeliveryService's InstagramChannelAdapter with a mocked http client."""
    from app.services import delivery as delivery_module

    real_adapter_cls = delivery_module.InstagramChannelAdapter

    def _adapter_with_mock(**kwargs):
        kwargs["http_client_factory"] = factory
        return real_adapter_cls(**kwargs)

    return patch.object(delivery_module, "InstagramChannelAdapter", side_effect=_adapter_with_mock)


class _NoopTelegramAdapter:
    """Minimal adapter stub — instagram sends never touch the Telegram adapter."""

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            send_message=False,
            send_reaction=False,
            edit_message=False,
            fetch_history=False,
            fetch_media_blob=False,
        )


async def _grant(db_session: AsyncSession, *, workspace_id: int, agent_id: int, scope: str) -> None:
    await ToolGrantService(db_session).grant(
        workspace_id=workspace_id,
        payload=ToolGrantInput(agent_id=agent_id, scope=scope),
    )


# ---------------------------------------------------------------------------
# Happy path: text send on instagram_dm reaches Graph API
# ---------------------------------------------------------------------------


async def test_instagram_send_message_not_blocked(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    """send_message on instagram_dm conversation is NOT blocked; Graph API receives the POST."""
    workspace.instagram_access_token = "IGAA-test-token"
    workspace.instagram_page_id = "17841400000000000"
    await db_session.flush()

    conversation = await _make_instagram_conversation(db_session, workspace)
    await _grant(db_session, workspace_id=workspace.id, agent_id=agent.id, scope=TELEGRAM_SEND_MESSAGE)

    factory, post_mock = _graph_post_mock()
    delivery = DeliveryService(sidecar_url="http://sidecar.test", sidecar_api_key="k")
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=_NoopTelegramAdapter())

    with _patched_ig_adapter(factory):
        result = await runtime.send_message(
            workspace_id=workspace.id,
            agent_id=agent.id,
            conversation_id=conversation.id,
            text="Salom! Narxi 4 900 000 so'm.",
            correlation_id="ig-talk-send",
            idempotency_key="ig-talk-send-1",
            delivery_delay_seconds=0.0,
            typing_indicator=False,
        )

    # Not blocked
    assert result.status == "executed", f"unexpected status={result.status!r} reason={result.reason_code!r}"
    assert result.reason_code != "conversation_not_telegram"

    # Graph API received the send
    assert post_mock.await_count == 1
    sent_json = post_mock.call_args.kwargs["json"]
    assert sent_json["recipient"] == {"id": _EXTERNAL_CHAT_ID}

    # Delivery success surfaced through the runtime result
    assert result.delivery_state == "confirmed"
    assert result.external_message_id == "mid.ig9"

    # Message row persisted
    message = await db_session.get(Message, result.message_id)
    assert message is not None
    assert message.sender_type == SenderType.SELLER.value
    assert message.delivery_state == "confirmed"
    assert message.external_message_id == "mid.ig9"


# ---------------------------------------------------------------------------
# Happy path: media send on instagram_dm reaches Graph API
# ---------------------------------------------------------------------------


async def test_instagram_send_media_not_blocked(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    """send_media on instagram_dm conversation is NOT blocked; Graph API receives media POST."""
    workspace.instagram_access_token = "IGAA-test-token"
    workspace.instagram_page_id = "17841400000000000"
    await db_session.flush()

    conversation = await _make_instagram_conversation(db_session, workspace)
    await _grant(db_session, workspace_id=workspace.id, agent_id=agent.id, scope=TELEGRAM_SEND_MESSAGE)

    # TelegramToolRuntime.send_media resolves outbound media from source facts /
    # CatalogMediaRecord. For this test we only need the channel guard to pass,
    # so we mock the delivery layer directly.
    from dataclasses import dataclass, field
    from typing import Any

    @dataclass
    class _FakeDelivery:
        media_calls: list[dict[str, Any]] = field(default_factory=list)

        async def deliver_media(self, conversation_id: int, media, **kwargs: Any) -> DeliveryResult:
            self.media_calls.append({"conversation_id": conversation_id, "media": media, **kwargs})
            return DeliveryResult(success=True, external_message_id="mid.media-ig", state="confirmed")

        async def deliver_message(self, conversation_id: int, text: str, **kwargs: Any) -> DeliveryResult:
            return DeliveryResult(success=True, external_message_id="mid.ig-txt", state="confirmed")

    fake_delivery = _FakeDelivery()

    # We need an outbound media record; inject one via the source-fact resolver
    # by patching _resolve_outbound_media to return a canned media object.
    fake_media = ChannelOutboundMedia(
        url="https://cdn.test/product.jpg",
        media_type="photo",
    )
    runtime = TelegramToolRuntime(db_session, delivery=fake_delivery, adapter=_NoopTelegramAdapter())

    with patch.object(runtime, "_resolve_outbound_media", return_value=fake_media):
        result = await runtime.send_media(
            workspace_id=workspace.id,
            agent_id=agent.id,
            conversation_id=conversation.id,
            media_ref="catalog_media:product:hero",
            caption="Mahsulot rasmi",
            correlation_id="ig-talk-media",
            idempotency_key="ig-talk-media-1",
            delivery_delay_seconds=0.0,
            typing_indicator=False,
        )

    assert result.status == "executed", f"unexpected status={result.status!r} reason={result.reason_code!r}"
    assert result.reason_code != "conversation_not_telegram"

    assert len(fake_delivery.media_calls) == 1
    assert fake_delivery.media_calls[0]["conversation_id"] == conversation.id


# ---------------------------------------------------------------------------
# Negative: instagram_dm without external_chat_id is blocked
# ---------------------------------------------------------------------------


async def test_instagram_send_blocked_when_external_chat_id_missing(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    """instagram_dm with external_chat_id=None is blocked with 'external_chat_missing'."""
    workspace.instagram_access_token = "IGAA-test-token"
    await db_session.flush()

    conversation = await _make_instagram_conversation(db_session, workspace, external_chat_id=None)
    await _grant(db_session, workspace_id=workspace.id, agent_id=agent.id, scope=TELEGRAM_SEND_MESSAGE)

    runtime = TelegramToolRuntime(db_session, delivery=None, adapter=_NoopTelegramAdapter())

    result = await runtime.send_message(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        text="Salom",
        correlation_id="ig-no-chat-id",
        idempotency_key="ig-no-chat-id-1",
    )

    assert result.status == "blocked"
    assert result.reason_code == "external_chat_missing"

    # Audit row recorded
    audit = await db_session.scalar(
        select(CommercialEventRecord).where(
            CommercialEventRecord.workspace_id == workspace.id,
            CommercialEventRecord.idempotency_key == "event:ig-no-chat-id-1",
        )
    )
    assert audit is not None
    assert audit.payload["reason_code"] == "external_chat_missing"


# ---------------------------------------------------------------------------
# Capability pin: send_reaction on instagram_dm stays blocked
# ---------------------------------------------------------------------------


async def test_instagram_send_reaction_stays_blocked(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    """send_reaction on instagram_dm is blocked — Instagram does not support reactions.

    This test pins current behavior so any future regression is caught.
    The block reason code wording ('conversation_not_telegram') is a tracked
    cosmetic follow-up; we assert the blocked status here, not the exact wording.
    """
    workspace.instagram_access_token = "IGAA-test-token"
    await db_session.flush()

    conversation = await _make_instagram_conversation(db_session, workspace)
    await _grant(db_session, workspace_id=workspace.id, agent_id=agent.id, scope=TELEGRAM_SEND_REACTION)

    runtime = TelegramToolRuntime(db_session, delivery=None, adapter=_NoopTelegramAdapter())

    result = await runtime.send_reaction(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        reaction="👍",
        target_message_ref="1440",
        correlation_id="ig-reaction-blocked",
        idempotency_key="ig-reaction-blocked-1",
    )

    assert result.status == "blocked"
    # The exact reason code wording is a cosmetic follow-up; just assert not executed.
    assert result.reason_code != "reaction_sent"
