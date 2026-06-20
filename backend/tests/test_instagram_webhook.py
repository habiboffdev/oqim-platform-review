"""Instagram webhook: hub.challenge verify, HMAC signature, inbound ingest."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.deps import get_settings_dep
from app.core.event_spine import MsgInbound
from tests.conftest import TEST_DB_URL

pytestmark = pytest.mark.asyncio

APP_SECRET = "test-ig-app-secret"
VERIFY_TOKEN = "test-ig-verify-token"


def _settings_with_instagram() -> Settings:
    """Settings with Instagram fields populated, used to replace get_settings_dep."""
    return Settings(
        _env_file=None,
        SECRET_KEY="test-secret-key-for-unit-tests-only-not-production",
        SIDECAR_API_KEY="test-sidecar-key",
        DATABASE_URL=TEST_DB_URL,
        EVENT_SPINE_PERSIST_MODE="shadow",
        INSTAGRAM_APP_SECRET=APP_SECRET,
        INSTAGRAM_WEBHOOK_VERIFY_TOKEN=VERIFY_TOKEN,
    )


@pytest.fixture(autouse=True)
def _instagram_settings(app_with_fake_spine):
    """Override get_settings_dep on the app so the route sees Instagram secrets."""
    app, _ = app_with_fake_spine
    app.dependency_overrides[get_settings_dep] = _settings_with_instagram


@pytest_asyncio.fixture
async def workspace_with_instagram(db_session):
    from app.models.workspace import Workspace

    ws = Workspace(
        name="IG Test WS",
        phone_number="+998901112233",
        instagram_connected=True,
        instagram_page_id="17841400000000000",
        instagram_access_token="IGAA-test-token",
    )
    db_session.add(ws)
    await db_session.flush()
    return ws


@pytest_asyncio.fixture
async def workspace_with_dual_ids(db_session):
    """A workspace whose webhook account id (entry.id) differs from user_id.

    Instagram-Login exposes two ids for one account (user_id and id) and Meta's
    docs don't say which the webhook entry.id carries — a documented, unresolved
    mismatch. instagram_page_id holds user_id; instagram_account_id holds the id.
    """
    from app.models.workspace import Workspace

    ws = Workspace(
        name="IG Dual ID WS",
        phone_number="+998905556677",
        instagram_connected=True,
        instagram_page_id="17841400000000000",  # user_id
        instagram_account_id="27100000000000000",  # id (may be the webhook entry.id)
        instagram_access_token="IGAA-test-token-2",
    )
    db_session.add(ws)
    await db_session.flush()
    return ws


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _dm_payload(entry_id: str = "17841400000000000") -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": entry_id,
                "time": 1_750_000_000,
                "messaging": [
                    {
                        "sender": {"id": "999000111"},
                        "recipient": {"id": entry_id},
                        "timestamp": 1_750_000_000_123,
                        "message": {"mid": "mid.in1", "text": "Assalomu alaykum"},
                    }
                ],
            }
        ],
    }


async def test_get_echoes_hub_challenge_with_valid_verify_token(app_with_fake_spine):
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/webhook/instagram",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "challenge-123",
            },
        )
    assert response.status_code == 200
    assert response.text == "challenge-123"


async def test_get_rejects_wrong_verify_token(app_with_fake_spine):
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/webhook/instagram",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "challenge-123",
            },
        )
    assert response.status_code == 403


async def test_post_rejects_missing_or_bad_signature(app_with_fake_spine, workspace_with_instagram):
    app, fake_spine = app_with_fake_spine
    body = json.dumps(_dm_payload()).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unsigned = await client.post(
            "/api/webhook/instagram", content=body,
            headers={"Content-Type": "application/json"},
        )
        bad = await client.post(
            "/api/webhook/instagram", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=deadbeef"},
        )
    assert unsigned.status_code == 403
    assert bad.status_code == 403
    assert not fake_spine.append.called


async def test_post_appends_channel_tagged_msg_inbound(
    app_with_fake_spine, workspace_with_instagram, db_session
):
    app, fake_spine = app_with_fake_spine
    body = json.dumps(_dm_payload()).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/webhook/instagram", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
        )
    assert response.status_code == 200
    assert response.json()["failed"] == 0
    appended = [call.args[0] for call in fake_spine.append.call_args_list]
    inbound = [e for e in appended if isinstance(e, MsgInbound)]
    assert len(inbound) == 1
    assert inbound[0].channel == "instagram_dm"
    assert inbound[0].workspace_id == workspace_with_instagram.id
    assert inbound[0].channel_conversation_id == "999000111"
    assert inbound[0].channel_message_id == "mid.in1"
    assert inbound[0].text == "Assalomu alaykum"

    # Shadow mode: the DB fallback must also have persisted the message.
    from sqlalchemy import select

    from app.models.conversation import Conversation
    from app.models.message import Message

    conversation = (
        await db_session.execute(
            select(Conversation).where(
                Conversation.workspace_id == workspace_with_instagram.id,
                Conversation.channel == "instagram_dm",
                Conversation.external_chat_id == "999000111",
            )
        )
    ).scalar_one()
    persisted = (
        (
            await db_session.execute(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.external_message_id == "mid.in1",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(persisted) == 1


async def test_post_resolves_workspace_by_secondary_account_id(
    app_with_fake_spine, workspace_with_dual_ids
):
    """Meta's webhook entry.id may be the account `id` rather than the `user_id`
    we also store. The resolver must match either, or every real DM is dropped
    with 'unknown page_id … skipped' (the live pilot failure)."""
    app, fake_spine = app_with_fake_spine
    body = json.dumps(_dm_payload(entry_id="27100000000000000")).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/webhook/instagram", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
        )
    assert response.status_code == 200
    assert response.json()["skipped"] == 0
    appended = [call.args[0] for call in fake_spine.append.call_args_list]
    inbound = [e for e in appended if isinstance(e, MsgInbound)]
    assert len(inbound) == 1
    assert inbound[0].workspace_id == workspace_with_dual_ids.id
    assert inbound[0].channel == "instagram_dm"


async def test_post_invalid_json_returns_400(app_with_fake_spine, workspace_with_instagram):
    app, fake_spine = app_with_fake_spine
    body = b"not-json"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/webhook/instagram", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
        )
    assert response.status_code == 400
    assert not fake_spine.append.called


async def test_post_infra_failure_returns_500_for_meta_retry(
    app_with_fake_spine, workspace_with_instagram
):
    import redis.exceptions

    app, fake_spine = app_with_fake_spine
    fake_spine.append.side_effect = redis.exceptions.ConnectionError("redis down")
    body = json.dumps(_dm_payload()).encode()
    # raise_app_exceptions=False so we observe the 500 Meta would see
    # (never-drop rule: infra outages must NOT be ACKed as delivered).
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/webhook/instagram", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
        )
    assert response.status_code == 500


async def test_post_poison_entry_does_not_lose_batch(app_with_fake_spine, workspace_with_instagram):
    app, fake_spine = app_with_fake_spine
    poison_entry = {
        "id": "17841400000000000",
        "time": 1_750_000_000,
        "messaging": [
            {
                "sender": "NOT-A-DICT",
                "recipient": {"id": "17841400000000000"},
                "timestamp": 1_750_000_000_123,
                "message": {"mid": "mid.poison", "text": "boom"},
            }
        ],
    }
    payload = _dm_payload()
    payload["entry"] = [poison_entry, payload["entry"][0]]
    body = json.dumps(payload).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/webhook/instagram", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
        )
    assert response.status_code == 200
    assert response.json()["failed"] == 1
    appended = [call.args[0] for call in fake_spine.append.call_args_list]
    inbound = [e for e in appended if isinstance(e, MsgInbound)]
    assert len(inbound) == 1
    assert inbound[0].channel_message_id == "mid.in1"


async def test_post_unknown_page_id_is_acked_but_not_ingested(app_with_fake_spine, workspace_with_instagram):
    app, fake_spine = app_with_fake_spine
    body = json.dumps(_dm_payload(entry_id="17840000000000999")).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/webhook/instagram", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
        )
    # Always 200 to Meta (no retry storms), but nothing appended.
    assert response.status_code == 200
    assert not fake_spine.append.called


async def test_post_routes_comment_changes_to_comment_dm_service(
    app_with_fake_spine, workspace_with_instagram
):
    from unittest.mock import AsyncMock, patch

    body = json.dumps(
        {
            "object": "instagram",
            "entry": [
                {
                    "id": "17841400000000000",
                    "time": 1_750_000_000,
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": "c-500",
                                "text": "Narxi qancha?",
                                "media": {"id": "media-9"},
                                "from": {"id": "888", "username": "vali"},
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()
    app, _ = app_with_fake_spine
    handle_mock = AsyncMock()
    with patch(
        "app.api.routes.webhook_instagram.InstagramCommentDmService"
    ) as service_cls:
        service_cls.return_value.handle_comment = handle_mock
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/webhook/instagram", content=body,
                headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
            )
    assert response.status_code == 200
    assert handle_mock.call_count == 1
    assert handle_mock.call_args.kwargs["value"]["id"] == "c-500"


async def test_post_poison_comment_does_not_lose_batch(
    app_with_fake_spine, workspace_with_instagram
):
    from unittest.mock import AsyncMock, patch

    body = json.dumps(
        {
            "object": "instagram",
            "entry": [
                {
                    "id": "17841400000000000",
                    "changes": [{"field": "comments", "value": {"id": "c-1", "media": {"id": "m"}, "from": {"id": "9"}}}],
                },
                _dm_payload()["entry"][0],
            ],
        }
    ).encode()
    app, fake_spine = app_with_fake_spine
    with patch(
        "app.api.routes.webhook_instagram.InstagramCommentDmService"
    ) as service_cls:
        service_cls.return_value.handle_comment = AsyncMock(side_effect=RuntimeError("poison"))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/webhook/instagram", content=body,
                headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
            )
    payload = response.json()
    assert response.status_code == 200
    assert payload["failed"] == 1
    # The second (valid DM) entry was still ingested.
    appended = [call.args[0] for call in fake_spine.append.call_args_list]
    assert len([e for e in appended if isinstance(e, MsgInbound)]) == 1
