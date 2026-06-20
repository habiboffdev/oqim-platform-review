"""Verify webhook routes append canonical events to the EventSpine."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.deps import get_settings_dep
from app.core.event_spine import MsgInbound


@pytest.mark.asyncio
async def test_webhook_telegram_publishes_msg_inbound(app_with_fake_spine, workspace_with_telegram_user):
    app, fake_spine = app_with_fake_spine
    workspace = workspace_with_telegram_user
    payload = {
        "sellerUserId": str(workspace.telegram_user_id),
        "chatId": "4101",
        "senderId": "98765",
        "senderName": "Customer",
        "messageId": 12345.0,
        "text": "salom",
        "date": 1_700_000_000,
        "isOutgoing": False,
        "telegram_update_received_at": 1_700_000_001.1,
        "telegram_state_applied_at": 1_700_000_001.15,
        "hot_event_built_at": 1_700_000_001.2,
        "outbox_enqueued_at": 1_700_000_001.3,
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/webhook/telegram",
            json=payload,
            headers={"X-Sidecar-Key": "test-sidecar-key"},
        )

    appended = [call.args[0] for call in fake_spine.append.call_args_list]
    inbound_events = [e for e in appended if isinstance(e, MsgInbound)]
    assert len(inbound_events) == 1
    assert inbound_events[0].telegram_chat_id == 4101
    assert inbound_events[0].telegram_message_id == 12345
    assert inbound_events[0].workspace_id == workspace.id
    assert inbound_events[0].telegram_update_received_at == 1_700_000_001.1
    assert inbound_events[0].telegram_state_applied_at == 1_700_000_001.15
    assert inbound_events[0].hot_event_built_at == 1_700_000_001.2
    assert inbound_events[0].outbox_enqueued_at == 1_700_000_001.3
    assert inbound_events[0].backend_webhook_received_at is not None


@pytest.mark.asyncio
async def test_webhook_telegram_can_resolve_workspace_from_hot_path_workspace_id(
    app_with_fake_spine,
    workspace_with_telegram_user,
):
    app, fake_spine = app_with_fake_spine
    workspace = workspace_with_telegram_user
    payload = {
        "sellerUserId": "",
        "workspaceId": workspace.id,
        "chatId": "4102",
        "senderId": "98766",
        "senderName": "",
        "messageId": 12346.0,
        "text": "tezkor hot path",
        "date": 1_700_000_010,
        "isOutgoing": False,
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/webhook/telegram",
            json=payload,
            headers={"X-Sidecar-Key": "test-sidecar-key"},
        )

    assert response.status_code == 200
    appended = [call.args[0] for call in fake_spine.append.call_args_list]
    inbound_events = [e for e in appended if isinstance(e, MsgInbound)]
    assert len(inbound_events) == 1
    assert inbound_events[0].workspace_id == workspace.id
    assert inbound_events[0].channel_account_id == ""


@pytest.mark.asyncio
async def test_webhook_telegram_authoritative_mode_is_append_only(
    app_with_fake_spine,
    workspace_with_telegram_user,
):
    app, fake_spine = app_with_fake_spine
    workspace = workspace_with_telegram_user
    app.dependency_overrides[get_settings_dep] = lambda: Settings(
        _env_file=None,
        SECRET_KEY="test-secret-key-for-unit-tests-only-not-production",
        SIDECAR_API_KEY="test-sidecar-key",
        DATABASE_URL="postgresql+asyncpg://localhost/test",
        EVENT_SPINE_PERSIST_MODE="authoritative",
    )
    payload = {
        "sellerUserId": str(workspace.telegram_user_id),
        "chatId": "4101",
        "senderId": "98765",
        "senderName": "Customer",
        "messageId": 12346.0,
        "text": "narxi qancha",
        "date": 1_700_000_000,
        "isOutgoing": False,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/webhook/telegram",
            json=payload,
            headers={"X-Sidecar-Key": "test-sidecar-key"},
        )

    assert response.status_code == 200
    assert response.json()["source_of_truth"] == "event_spine"
    assert response.json()["status"] == "accepted"
    fake_spine.append.assert_awaited_once()
