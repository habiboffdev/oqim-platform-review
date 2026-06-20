from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.commerce_catalog import CatalogMediaRecord
from app.models.commercial_spine import BusinessBrainFactRecord, CommercialEventRecord
from app.models.conversation import Conversation
from app.models.message import Message, SenderType
from app.models.tool_grant import ToolGrant
from app.models.workspace import Workspace
from app.modules.telegram_tools.contracts import (
    TELEGRAM_EDIT_MESSAGE,
    TELEGRAM_FETCH_MEDIA,
    TELEGRAM_READ_MESSAGES,
    TELEGRAM_SEND_MESSAGE,
    TELEGRAM_SEND_REACTION,
    TELEGRAM_SYNC_HISTORY,
    TELEGRAM_WATCH_CHANNEL,
)
from app.modules.telegram_tools.runtime import TelegramToolRuntime
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService
from app.services.channel_adapter_contract import (
    ChannelCapabilities,
    ChannelDeliveryStatus,
    ChannelInboundMessage,
    ChannelMediaBlob,
    ChannelMediaRef,
    ChannelSendResult,
)
from app.services.channel_conversation_sync import ConversationSyncResult
from app.services.delivery import DeliveryResult

pytestmark = pytest.mark.asyncio


@dataclass
class FakeTelegramAdapter:
    channel: str = "telegram_dm"
    edited: list[dict[str, Any]] = field(default_factory=list)
    reactions: list[dict[str, Any]] = field(default_factory=list)
    fetched_media: list[ChannelMediaRef] = field(default_factory=list)

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            send_message=True,
            edit_message=True,
            send_reaction=True,
            fetch_history=True,
            fetch_media_blob=True,
        )

    async def fetch_history(self, **kwargs: Any) -> list[ChannelInboundMessage]:
        return [
            ChannelInboundMessage(
                workspace_id=int(kwargs["workspace_id"]),
                channel="telegram_dm",
                account_id="owner",
                conversation_id=str(kwargs["conversation_id"]),
                message_id="101",
                sender_id="customer-1",
                sender_name="Ali",
                text="Salom",
                sent_at=1_710_000_000,
            )
        ]

    async def edit_message(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        message_id: str,
        text: str,
        idempotency_key: str,
    ) -> ChannelSendResult:
        self.edited.append(
            {
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "text": text,
                "idempotency_key": idempotency_key,
            }
        )
        return ChannelSendResult(
            external_message_id=message_id,
            status=ChannelDeliveryStatus(status="sent", external_message_id=message_id),
        )

    async def send_reaction(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        message_id: str,
        reaction: str,
        idempotency_key: str,
    ) -> ChannelSendResult:
        self.reactions.append(
            {
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "reaction": reaction,
                "idempotency_key": idempotency_key,
            }
        )
        return ChannelSendResult(
            external_message_id=message_id,
            status=ChannelDeliveryStatus(status="sent", external_message_id=message_id),
        )

    async def fetch_media_blob(
        self,
        *,
        workspace_id: int,
        media: ChannelMediaRef,
        thumb: bool = False,
    ) -> ChannelMediaBlob:
        _ = workspace_id, thumb
        self.fetched_media.append(media)
        return ChannelMediaBlob(data=b"image-bytes", mime_type="image/jpeg")


@dataclass
class FakeDelivery:
    calls: list[dict[str, Any]] = field(default_factory=list)
    media_calls: list[dict[str, Any]] = field(default_factory=list)

    async def deliver_message(self, conversation_id: int, text: str, **kwargs: Any) -> DeliveryResult:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "text": text,
                **kwargs,
            }
        )
        return DeliveryResult(
            success=True,
            external_message_id="9001",
            state="confirmed",
        )

    async def deliver_media(self, conversation_id: int, media, **kwargs: Any) -> DeliveryResult:
        self.media_calls.append(
            {
                "conversation_id": conversation_id,
                "media": media,
                **kwargs,
            }
        )
        return DeliveryResult(
            success=True,
            external_message_id="9002",
            state="confirmed",
        )


@dataclass
class FakeSync:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def sync_conversation(self, **kwargs: Any) -> ConversationSyncResult:
        self.calls.append(kwargs)
        return ConversationSyncResult(requested=2, persisted=1, duplicates=1)


async def _grant(
    db_session: AsyncSession,
    *,
    workspace_id: int,
    agent_id: int,
    scope: str,
) -> None:
    await ToolGrantService(db_session).grant(
        workspace_id=workspace_id,
        payload=ToolGrantInput(agent_id=agent_id, scope=scope),
    )


async def test_send_message_blocks_without_tool_grant(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    delivery = FakeDelivery()
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=FakeTelegramAdapter())

    result = await runtime.send_message(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        text="Salom",
        correlation_id="test-send-denied",
        idempotency_key="tg-tool-send-denied",
    )

    assert result.status == "blocked"
    assert result.reason_code == "missing_tool_grant"
    assert delivery.calls == []
    audit = await db_session.scalar(
        select(CommercialEventRecord).where(
            CommercialEventRecord.workspace_id == workspace.id,
            CommercialEventRecord.idempotency_key == "event:tg-tool-send-denied",
        )
    )
    assert audit is not None
    assert audit.payload["status"] == "blocked"


async def test_send_message_uses_delivery_and_records_grant_use(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    delivery = FakeDelivery()
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=FakeTelegramAdapter())

    result = await runtime.send_message(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        text="Assalomu alaykum",
        correlation_id="test-send",
        idempotency_key="tg-tool-send-1",
        delivery_delay_seconds=0.4,
        typing_indicator=False,
        online_tail_seconds=1.5,
    )

    assert result.status == "executed"
    assert result.external_message_id == "9001"
    assert result.delivery_state == "confirmed"
    assert delivery.calls[0]["client_idempotency_key"] == "tg-tool-send-1"
    assert delivery.calls[0]["message_id"] == result.message_id
    assert delivery.calls[0]["delay_override_seconds"] == 0.4
    assert delivery.calls[0]["typing_indicator"] is False
    assert delivery.calls[0]["online_tail_seconds"] == 1.5

    message = await db_session.get(Message, result.message_id)
    assert message is not None
    assert message.sender_type == SenderType.SELLER.value
    assert message.client_message_uuid == "tg-tool-send-1"
    assert message.external_message_id == "9001"

    grant = await db_session.scalar(
        select(ToolGrant).where(
            ToolGrant.workspace_id == workspace.id,
            ToolGrant.agent_id == agent.id,
            ToolGrant.scope == TELEGRAM_SEND_MESSAGE,
        )
    )
    assert grant is not None
    assert grant.use_count == 1


async def test_send_message_replies_to_local_message_ref(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    target = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="helllo",
        telegram_message_id=1440,
        external_message_id="1440",
    )
    db_session.add(target)
    await db_session.flush()

    delivery = FakeDelivery()
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=FakeTelegramAdapter())

    result = await runtime.send_message(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        text="Salom",
        correlation_id="test-send-reply-to",
        idempotency_key="tg-tool-send-reply-to",
        reply_to_message_ref=f"message:{target.id}",
    )

    assert result.status == "executed"
    assert delivery.calls[0]["reply_to_message_id"] == 1440

    message = await db_session.get(Message, result.message_id)
    assert message is not None
    assert message.reply_to_msg_id == 1440


async def test_send_reaction_reacts_to_local_message_ref(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_SEND_REACTION,
    )
    target = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="rahmat",
        telegram_message_id=1440,
        external_message_id="1440",
    )
    db_session.add(target)
    await db_session.flush()

    adapter = FakeTelegramAdapter()
    runtime = TelegramToolRuntime(db_session, adapter=adapter)

    result = await runtime.send_reaction(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        reaction="👍",
        correlation_id="test-send-reaction",
        idempotency_key="tg-tool-react-1",
        target_message_ref=f"message:{target.id}",
    )

    assert result.status == "executed"
    assert result.reason_code == "reaction_sent"
    assert result.external_message_id == "1440"
    assert result.delivery_state == "confirmed"
    assert adapter.reactions == [
        {
            "workspace_id": workspace.id,
            "conversation_id": str(conversation.external_chat_id or conversation.telegram_chat_id),
            "message_id": "1440",
            "reaction": "👍",
            "idempotency_key": "tg-tool-react-1",
        }
    ]

    grant = await db_session.scalar(
        select(ToolGrant).where(
            ToolGrant.workspace_id == workspace.id,
            ToolGrant.agent_id == agent.id,
            ToolGrant.scope == TELEGRAM_SEND_REACTION,
        )
    )
    assert grant is not None
    assert grant.use_count == 1


async def test_send_message_replays_existing_idempotency_without_duplicate_delivery(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    delivery = FakeDelivery()
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=FakeTelegramAdapter())

    first = await runtime.send_message(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        text="Birinchi",
        correlation_id="test-replay",
        idempotency_key="tg-tool-replay",
    )
    second = await runtime.send_message(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        text="Birinchi",
        correlation_id="test-replay",
        idempotency_key="tg-tool-replay",
    )

    assert first.status == "executed"
    assert second.status == "replayed"
    assert second.message_id == first.message_id
    assert len(delivery.calls) == 1


async def test_send_message_retries_existing_failed_placeholder_with_same_idempotency_key(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    failed = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.SELLER.value,
        content="Old failed text",
        client_message_uuid="tg-tool-retry-failed",
        delivery_state="failed",
    )
    db_session.add(failed)
    await db_session.flush()
    failed_id = failed.id

    delivery = FakeDelivery()
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=FakeTelegramAdapter())

    result = await runtime.send_message(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        text="Retry text",
        correlation_id="test-retry-failed",
        idempotency_key="tg-tool-retry-failed",
    )

    assert result.status == "executed"
    assert result.message_id == failed_id
    assert result.external_message_id == "9001"
    assert result.delivery_state == "confirmed"
    assert len(delivery.calls) == 1
    assert delivery.calls[0]["message_id"] == failed_id

    await db_session.refresh(failed)
    assert failed.content == "Retry text"
    assert failed.delivery_state == "confirmed"
    assert failed.external_message_id == "9001"


async def test_send_media_uses_source_media_fact_and_records_placeholder(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    media_ref = "source_media:catalog:hero:001"
    db_session.add(
        BusinessBrainFactRecord(
            workspace_id=workspace.id,
            fact_id=f"business_source_media:{media_ref}",
            fact_type="business_source_media_fact",
            entity_ref=f"workspace:source_media:{media_ref}",
            value={
                "media_ref": media_ref,
                "url": "https://cdn.example.com/catalog/hero.png",
                "media_type": "image",
                "content_type": "image/png",
            },
            confidence=0.9,
            status="active",
            risk_tier="low",
            valid_from=datetime.now(UTC),
            source_refs=["source:catalog", media_ref],
            idempotency_key="fact:source-media:hero",
            raw_fact={},
        )
    )
    await db_session.flush()

    delivery = FakeDelivery()
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=FakeTelegramAdapter())

    result = await runtime.send_media(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        media_ref=media_ref,
        caption="Mana rasmi",
        correlation_id="test-send-media",
        idempotency_key="tg-tool-media-send-1",
        delivery_delay_seconds=0,
    )

    assert result.status == "executed"
    assert result.external_message_id == "9002"
    assert result.delivery_state == "confirmed"
    assert delivery.media_calls[0]["client_idempotency_key"] == "tg-tool-media-send-1"
    assert delivery.media_calls[0]["media"].url == "https://cdn.example.com/catalog/hero.png"
    assert delivery.media_calls[0]["media"].media_type == "photo"
    assert delivery.media_calls[0]["caption"] == "Mana rasmi"
    assert delivery.media_calls[0]["delay_override_seconds"] == 0

    message = await db_session.get(Message, result.message_id)
    assert message is not None
    assert message.sender_type == SenderType.SELLER.value
    assert message.client_message_uuid == "tg-tool-media-send-1"
    assert message.external_message_id == "9002"
    assert message.media_type == "photo"
    assert message.media_url == "https://cdn.example.com/catalog/hero.png"
    assert message.media_metadata["media_ref"] == media_ref


async def test_send_media_prefers_approved_typed_catalog_media(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    db_session.add(
        CatalogMediaRecord(
            workspace_id=workspace.id,
            media_ref="catalog_media:wallet:hero",
            product_ref="catalog_product:wallet",
            media_kind="image",
            url="https://cdn.example.com/catalog/wallet.jpg",
            caption="Wallet",
            ocr_text="",
            visual_summary="",
            authority_state="approved",
            source_refs=["source:wallet"],
            source_fact_ids=["fact:wallet-media"],
            metadata_={"content_type": "image/jpeg", "file_name": "wallet.jpg"},
        )
    )
    await db_session.flush()

    delivery = FakeDelivery()
    runtime = TelegramToolRuntime(db_session, delivery=delivery, adapter=FakeTelegramAdapter())

    result = await runtime.send_media(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        media_ref="catalog_media:wallet:hero",
        caption="Mana",
        correlation_id="test-send-catalog-media",
        idempotency_key="tg-tool-catalog-media-send",
        delivery_delay_seconds=0,
    )

    assert result.status == "executed"
    sent_media = delivery.media_calls[0]["media"]
    assert sent_media.url == "https://cdn.example.com/catalog/wallet.jpg"
    assert sent_media.mime_type == "image/jpeg"
    assert sent_media.file_name == "wallet.jpg"


async def test_edit_message_refuses_customer_message_even_with_grant(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    message: Message,
) -> None:
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_EDIT_MESSAGE,
    )
    adapter = FakeTelegramAdapter()
    runtime = TelegramToolRuntime(db_session, adapter=adapter)

    result = await runtime.edit_message(
        workspace_id=workspace.id,
        agent_id=agent.id,
        local_message_id=message.id,
        text="Customer text should not be editable",
        correlation_id="test-edit-customer",
        idempotency_key="tg-tool-edit-customer",
    )

    assert result.status == "blocked"
    assert result.reason_code == "not_oqim_owned_message"
    assert adapter.edited == []


async def test_edit_message_edits_oqim_owned_message_and_records_use(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_EDIT_MESSAGE,
    )
    seller_message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.SELLER.value,
        content="Old text",
        external_message_id="901",
        delivery_state="confirmed",
    )
    db_session.add(seller_message)
    await db_session.flush()

    adapter = FakeTelegramAdapter()
    runtime = TelegramToolRuntime(db_session, adapter=adapter)

    result = await runtime.edit_message(
        workspace_id=workspace.id,
        agent_id=agent.id,
        local_message_id=seller_message.id,
        text="Yangi matn",
        correlation_id="test-edit-seller",
        idempotency_key="tg-tool-edit-seller",
    )

    assert result.status == "executed"
    assert result.reason_code == "message_edited"
    assert adapter.edited == [
        {
            "workspace_id": workspace.id,
            "conversation_id": str(conversation.telegram_chat_id),
            "message_id": "901",
            "text": "Yangi matn",
            "idempotency_key": "tg-tool-edit-seller",
        }
    ]
    await db_session.refresh(seller_message)
    assert seller_message.content == "Yangi matn"
    assert seller_message.edited_at is not None


async def test_read_fetch_sync_and_watch_use_scoped_grants(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    for scope in (
        TELEGRAM_READ_MESSAGES,
        TELEGRAM_FETCH_MEDIA,
        TELEGRAM_SYNC_HISTORY,
        TELEGRAM_WATCH_CHANNEL,
    ):
        await _grant(
            db_session,
            workspace_id=workspace.id,
            agent_id=agent.id,
            scope=scope,
        )
    remote_message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="Rasm",
        telegram_message_id=777,
        media_type="photo",
    )
    db_session.add(remote_message)
    await db_session.flush()

    adapter = FakeTelegramAdapter()
    sync = FakeSync()
    runtime = TelegramToolRuntime(db_session, adapter=adapter, sync=sync)

    read_result = await runtime.read_messages(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        correlation_id="test-read",
        idempotency_key="tg-tool-read",
    )
    media_result = await runtime.fetch_media(
        workspace_id=workspace.id,
        agent_id=agent.id,
        local_message_id=remote_message.id,
        correlation_id="test-media",
        idempotency_key="tg-tool-media",
    )
    sync_result = await runtime.sync_history(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        correlation_id="test-sync",
        idempotency_key="tg-tool-sync",
    )
    watch_result = await runtime.watch_channel(
        workspace_id=workspace.id,
        agent_id=agent.id,
        channel_ref="@catalog",
        correlation_id="test-watch",
        idempotency_key="tg-tool-watch",
    )

    assert read_result.status == "executed"
    assert read_result.messages[0].text == "Salom"
    assert media_result.payload == {"mime_type": "image/jpeg", "byte_count": 11}
    assert adapter.fetched_media[0].message_id == "777"
    assert sync_result.payload == {"requested": 2, "persisted": 1, "duplicates": 1}
    assert sync.calls[0]["conversation"].id == conversation.id
    assert watch_result.status == "executed"
    assert watch_result.trigger_id is not None


async def test_send_reaction_falls_back_to_channel_id_when_local_row_missing(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    """A 'message:<id>' ref whose local row is gone must not silently no-op:
    the tail is retried as a channel-level id (external/telegram) lookup."""
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_SEND_REACTION,
    )
    # A real message exists whose TELEGRAM id matches the dangling ref tail.
    target = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="rahmat",
        telegram_message_id=987654,
        external_message_id="987654",
    )
    db_session.add(target)
    await db_session.flush()

    adapter = FakeTelegramAdapter()
    runtime = TelegramToolRuntime(db_session, adapter=adapter)

    result = await runtime.send_reaction(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        reaction="👍",
        correlation_id="test-react-fallback",
        idempotency_key="tg-tool-react-fb-1",
        target_message_ref="message:987654",  # no local row with this PK
    )

    assert result.status == "executed"
    assert result.reason_code == "reaction_sent"
    assert adapter.reactions[0]["message_id"] == "987654"


async def test_send_reaction_unresolvable_local_ref_blocks_without_misfire(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    """A dangling 'message:<id>' ref with no matching channel id must block
    (never react to a guessed message id)."""
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_SEND_REACTION,
    )
    adapter = FakeTelegramAdapter()
    runtime = TelegramToolRuntime(db_session, adapter=adapter)

    result = await runtime.send_reaction(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        reaction="👍",
        correlation_id="test-react-dangling",
        idempotency_key="tg-tool-react-dangling-1",
        target_message_ref="message:31337",
    )

    assert result.status == "blocked"
    assert result.reason_code == "target_message_not_found"
    assert adapter.reactions == []


@dataclass
class _ReactionFailingAdapter:
    """Adapter whose send_reaction hits a transient sidecar 502 (issue #418)."""

    channel: str = "telegram_dm"

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(send_reaction=True)

    async def send_reaction(self, **_: Any) -> ChannelSendResult:
        import httpx

        request = httpx.Request("POST", "http://sidecar.local/react")
        response = httpx.Response(502, request=request)
        raise httpx.HTTPStatusError(
            "Server error '502 Bad Gateway'", request=request, response=response
        )


async def test_send_reaction_transient_sidecar_error_returns_failed_result_not_raise(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    """A cosmetic reaction that 502s must surface as a structured failed result,
    never an exception — otherwise it aborts turn finalization after the text
    bubble already delivered, and the HermesRun is left stuck running (#418)."""
    await _grant(
        db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        scope=TELEGRAM_SEND_REACTION,
    )
    target = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="998901234567",
        telegram_message_id=2440,
        external_message_id="2440",
    )
    db_session.add(target)
    await db_session.flush()

    runtime = TelegramToolRuntime(db_session, adapter=_ReactionFailingAdapter())

    result = await runtime.send_reaction(
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        reaction="👍",
        correlation_id="test-react-502",
        idempotency_key="tg-tool-react-502-1",
        target_message_ref=f"message:{target.id}",
    )

    assert result.status == "failed"
    assert result.reason_code == "reaction_delivery_failed"
    assert result.delivery_state == "failed"
    assert result.external_message_id == "2440"
    assert "502" in str(result.payload.get("error") or "")
