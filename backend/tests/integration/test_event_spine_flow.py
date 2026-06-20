"""End-to-end smoke: webhook POST → EventSpine publish → persist consumer → diff clean.

This test validates that all wiring from Tasks 7-16 composes correctly:
- Webhook route injects the configured spine (task 8)
- Spine publishes to per-workspace stream
- Authoritative persist consumer writes matching DB row
- Diff consumer reads stream, compares to DB, emits zero divergences
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.event_spine import DivergenceKind, EventSpine

pytestmark = pytest.mark.asyncio

SIDECAR_KEY = "test-sidecar-key"
SELLER_TELEGRAM_UID = 999_888_777


@pytest_asyncio.fixture
async def _integration_setup(db_session):
    """
    Full wiring for the integration smoke test:
    - Real EventSpine backed by in-process fakeredis
    - App dependency overrides: DB session, redis, dispatcher, delivery
    - Workspace pre-created in the transaction-rolled-back session
    Returns (app, real_spine, fake_redis_instance, workspace).
    """
    from app.core.deps import get_app_redis, get_conversation_turn_runner, get_db_session, get_delivery_service
    from app.main import app as real_app
    from app.models.workspace import Workspace
    from app.services.delivery import DeliveryResult, DeliveryService

    # In-process Redis — no network, deterministic
    fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # Real EventSpine so publishes actually hit the stream
    real_spine = EventSpine(fake_r)

    mock_dispatcher = AsyncMock()
    mock_dispatcher.enqueue_message = AsyncMock()
    mock_dispatcher.record_agent_message = AsyncMock()

    mock_delivery = AsyncMock(spec=DeliveryService)
    mock_delivery.deliver_message = AsyncMock(
        return_value=DeliveryResult(success=True, external_message_id="mock_ext_999")
    )

    # Workspace discoverable by webhook resolver via telegram_user_id
    ws = Workspace(
        name="Smoke WS",
        phone_number="+998905551111",
        telegram_user_id=SELLER_TELEGRAM_UID,
        telegram_connected=True,
    )
    db_session.add(ws)
    await db_session.flush()

    # Save originals for cleanup
    original_spine = getattr(real_app.state, "event_spine", None)
    original_lifespan = real_app.router.lifespan_context

    # Inject spine + state — no lifespan needed
    real_app.state.event_spine = real_spine
    real_app.state.app_redis = fake_r
    real_app.state.conversation_turn_runner = mock_dispatcher
    real_app.state.delivery = mock_delivery

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    real_app.router.lifespan_context = noop_lifespan

    async def override_db():
        yield db_session

    real_app.dependency_overrides[get_db_session] = override_db
    real_app.dependency_overrides[get_app_redis] = lambda: fake_r
    real_app.dependency_overrides[get_conversation_turn_runner] = lambda: mock_dispatcher
    real_app.dependency_overrides[get_delivery_service] = lambda: mock_delivery

    try:
        yield real_app, real_spine, fake_r, ws
    finally:
        real_app.dependency_overrides.clear()
        real_app.router.lifespan_context = original_lifespan
        if original_spine is not None:
            real_app.state.event_spine = original_spine
        await fake_r.aclose()


async def test_webhook_to_spine_to_diff_clean_flow(_integration_setup, db_session):
    """Round-trip: webhook POST → spine publishes → DB has matching row → diff tick → no divergences."""
    from app.services.event_spine_diff_consumer import EventSpineDiffConsumer
    from app.services.event_spine_persist_consumer import EventSpinePersistConsumer

    app, spine, fake_r, ws = _integration_setup

    payload = {
        "sellerUserId": str(SELLER_TELEGRAM_UID),
        "chatId": "4101",
        "senderId": "98765",
        "senderName": "Customer",
        "messageId": 12345,
        "text": "salom",
        "date": int(datetime.now(timezone.utc).timestamp()),
        "isOutgoing": False,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/webhook/telegram",
            json=payload,
            headers={"X-Sidecar-Key": SIDECAR_KEY},
        )

    assert response.status_code in (200, 201), (
        f"Webhook failed: {response.status_code} {response.text}"
    )

    # Drain ensures EventSpine background task completes the XADD
    await spine.drain(timeout=2.0)

    # Verify the event landed in the stream
    stream_key = f"oqim:events:{ws.id}"
    stream_entries = await fake_r.xrange(stream_key, "-", "+")
    assert len(stream_entries) >= 1, "EventSpine published nothing to the stream"

    # Simple async-context-manager wrapper around the already-open session
    @asynccontextmanager
    async def _session_cm():
        yield db_session

    persist_consumer = EventSpinePersistConsumer(
        redis=fake_r,
        db_factory=_session_cm,
        workspace_ids_provider=lambda: [ws.id],
        conversation_turn_runner=app.state.conversation_turn_runner,
        mode="authoritative",
    )
    await persist_consumer._ensure_groups()
    assert await persist_consumer._run_once(block_ms=500) == 1

    consumer = EventSpineDiffConsumer(
        redis=fake_r,
        db_factory=_session_cm,
        workspace_ids_provider=lambda: [ws.id],
    )
    await consumer._ensure_groups()

    # Sleep past the 2s grace period, then run one diff tick
    await asyncio.sleep(2.2)
    await consumer._run_once(block_ms=500)

    # Assert zero divergences across all six kinds
    for kind in DivergenceKind:
        counter = await fake_r.get(f"oqim:event_spine:div:{kind.value}")
        assert counter in (None, "0"), (
            f"Unexpected divergence '{kind.value}' = {counter!r} — "
            "indicates a mismatch between the spine event and the DB row"
        )
