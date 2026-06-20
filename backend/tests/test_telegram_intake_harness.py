from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.deps import get_settings_dep
from app.main import app
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message
from app.models.workspace import Workspace
from app.services.event_spine_persist_consumer import EventSpinePersistConsumer
from tests.conftest import TEST_DB_URL, make_token


pytestmark = pytest.mark.asyncio

WEBHOOK_URL = "/api/webhook/telegram"
SIDECAR_HEADERS = {"X-Sidecar-Key": "test-sidecar-key"}


@asynccontextmanager
async def _session_context(session: AsyncSession):
    yield session


def _gramjs_payload(**overrides) -> dict:
    payload = {
        "sellerUserId": "999888777",
        "chatId": "777001",
        "senderId": "555001",
        "senderName": "Aris",
        "messageId": 9001,
        "text": "Assalomu alaykum, shu mahsulot narxi qancha?",
        "date": 1_776_000_000,
        "isOutgoing": False,
        "mediaType": None,
        "mediaMetadata": None,
        "replyToMsgId": None,
    }
    payload.update(overrides)
    return payload


def _force_authoritative_event_spine() -> None:
    app.dependency_overrides[get_settings_dep] = lambda: Settings(
        _env_file=None,
        SECRET_KEY="test-secret-key-for-unit-tests-only-not-production",
        SIDECAR_API_KEY="test-sidecar-key",
        DATABASE_URL=TEST_DB_URL,
        EVENT_SPINE_PERSIST_MODE="authoritative",
    )


def _auth_headers(workspace_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(workspace_id)}"}


async def _drain_event_spine(
    *,
    redis,
    session: AsyncSession,
    workspace_id: int,
) -> int:
    app.state.conversation_turn_runner.enqueue_message.reset_mock()
    consumer = EventSpinePersistConsumer(
        redis=redis,
        db_factory=lambda: _session_context(session),
        workspace_ids_provider=lambda: [workspace_id],
        conversation_turn_runner=app.state.conversation_turn_runner,
        mode="authoritative",
        background_side_effects=False,
    )
    await consumer.observe_workspace(workspace_id)
    return await consumer._run_once(block_ms=1)


async def test_authoritative_gramjs_webhook_reaches_projection_api_and_turn_runner(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis,
    workspace_with_telegram_user: Workspace,
):
    """Golden path: GramJS event -> EventSpine -> projections -> read API -> turn runner."""
    _force_authoritative_event_spine()

    response = await client.post(
        WEBHOOK_URL,
        json=_gramjs_payload(),
        headers=SIDECAR_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["source_of_truth"] == "event_spine"
    assert response.json()["stream_id"]
    assert await db_session.scalar(select(func.count(Conversation.id))) == 0

    processed = await _drain_event_spine(
        redis=fake_redis,
        session=db_session,
        workspace_id=workspace_with_telegram_user.id,
    )
    assert processed == 1

    conversation = await db_session.scalar(
        select(Conversation).where(
            Conversation.workspace_id == workspace_with_telegram_user.id,
            Conversation.telegram_chat_id == 777001,
        )
    )
    assert conversation is not None
    assert conversation.channel == "telegram_dm"
    assert conversation.message_sequence == 1
    assert conversation.message_revision == 1

    customer = await db_session.get(Customer, conversation.customer_id)
    assert customer is not None
    assert customer.telegram_id == 555001
    assert customer.display_name == "Aris"

    message = await db_session.scalar(
        select(Message).where(
            Message.conversation_id == conversation.id,
            Message.telegram_message_id == 9001,
        )
    )
    assert message is not None
    assert message.sender_type == "customer"
    assert message.channel == "telegram_dm"
    assert message.content == "Assalomu alaykum, shu mahsulot narxi qancha?"
    assert message.conversation_seq == 1

    app.state.conversation_turn_runner.enqueue_message.assert_awaited_once()
    turn_kwargs = app.state.conversation_turn_runner.enqueue_message.await_args.kwargs
    assert turn_kwargs["workspace_id"] == workspace_with_telegram_user.id
    assert turn_kwargs["conversation_id"] == conversation.id
    assert turn_kwargs["customer_id"] == customer.id
    assert turn_kwargs["message_id"] == message.id
    assert turn_kwargs["telegram_chat_id"] == 777001

    headers = _auth_headers(workspace_with_telegram_user.id)
    list_response = await client.get("/api/conversations", headers=headers)
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["items"][0]["id"] == conversation.id
    assert list_payload["items"][0]["last_message_text"] == message.content
    assert list_payload["items"][0]["unread_count"] == 1

    detail_response = await client.get(f"/api/conversations/{conversation.id}", headers=headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["tail"]["latest_message_text"] == message.content

    messages_response = await client.get(
        f"/api/conversations/{conversation.id}/messages",
        headers=headers,
    )
    assert messages_response.status_code == 200
    messages_payload = messages_response.json()
    assert messages_payload["items"][0]["content"] == message.content
    assert messages_payload["items"][0]["telegram_message_id"] == 9001
    assert messages_payload["tail"]["latest_message_text"] == message.content


async def test_authoritative_telegram_intake_is_idempotent_across_route_retries(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis,
    workspace_with_telegram_user: Workspace,
):
    _force_authoritative_event_spine()

    payload = _gramjs_payload(messageId=9002, text="Rangi oqidan bormi?")
    first_response = await client.post(WEBHOOK_URL, json=payload, headers=SIDECAR_HEADERS)
    retry_response = await client.post(WEBHOOK_URL, json=payload, headers=SIDECAR_HEADERS)

    assert first_response.status_code == 200
    assert retry_response.status_code == 200
    assert first_response.json()["source_of_truth"] == "event_spine"
    assert retry_response.json()["source_of_truth"] == "event_spine"

    processed = await _drain_event_spine(
        redis=fake_redis,
        session=db_session,
        workspace_id=workspace_with_telegram_user.id,
    )
    assert processed == 1

    messages_count = await db_session.scalar(
        select(func.count(Message.id)).join(Conversation).where(
            Conversation.workspace_id == workspace_with_telegram_user.id,
            Conversation.telegram_chat_id == 777001,
            Message.telegram_message_id == 9002,
        )
    )
    assert messages_count == 1
    app.state.conversation_turn_runner.enqueue_message.assert_awaited_once()


async def test_authoritative_historical_backfill_persists_without_turn_wakeup(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis,
    workspace_with_telegram_user: Workspace,
):
    _force_authoritative_event_spine()

    response = await client.post(
        WEBHOOK_URL,
        json=_gramjs_payload(
            messageId=9003,
            text="O'tgan haftadagi suhbatdan xabar",
            isHistorical=True,
            source="history",
        ),
        headers=SIDECAR_HEADERS,
    )

    assert response.status_code == 200
    processed = await _drain_event_spine(
        redis=fake_redis,
        session=db_session,
        workspace_id=workspace_with_telegram_user.id,
    )
    assert processed == 1

    message = await db_session.scalar(
        select(Message).join(Conversation).where(
            Conversation.workspace_id == workspace_with_telegram_user.id,
            Message.telegram_message_id == 9003,
        )
    )
    assert message is not None
    assert message.content == "O'tgan haftadagi suhbatdan xabar"
    app.state.conversation_turn_runner.enqueue_message.assert_not_awaited()


async def test_botfather_and_control_bot_peers_never_become_customers(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis,
    workspace_with_telegram_user: Workspace,
):
    """Bots are never customers (live incident: agent<->control-bot loop).

    The sidecar's hot-path bot filter only works on cached entities; the
    persist consumer is the authoritative backstop for system peers
    (BotFather, Telegram service) and the workspace's own control bot.
    """
    _force_authoritative_event_spine()
    workspace_with_telegram_user.control_bot_user_id = 8912415758
    await db_session.flush()

    for chat_id, message_id in (("93372553", 9101), ("8912415758", 9102)):
        response = await client.post(
            WEBHOOK_URL,
            json=_gramjs_payload(
                chatId=chat_id,
                senderId=chat_id,
                senderName="Some Bot",
                messageId=message_id,
                text="Raqam topilmadi. Biznes Telegram raqamingizni tekshirib...",
            ),
            headers=SIDECAR_HEADERS,
        )
        assert response.status_code == 200

    processed = await _drain_event_spine(
        redis=fake_redis,
        session=db_session,
        workspace_id=workspace_with_telegram_user.id,
    )
    assert processed == 2  # both events consumed (acked), neither projected

    assert await db_session.scalar(select(func.count(Conversation.id))) == 0
    assert await db_session.scalar(select(func.count(Message.id))) == 0
    assert await db_session.scalar(select(func.count(Customer.id))) == 0
