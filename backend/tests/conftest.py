"""
Test fixtures for OQIM Business backend.

Strategy:
- Uses a REAL PostgreSQL database for integration tests.
- Defaults to oqim_business_test; proof harnesses can set OQIM_TEST_DB_SUFFIX
  or OQIM_TEST_DB_NAME to isolate parallel subprocesses.
- Each test runs in a nested transaction that is ROLLED BACK after — zero data leaks.
- Route session.commit() calls create SAVEPOINTs, not real commits.
- Mocks Redis and LLM (Gemini) calls to avoid external dependencies.
- Provides authenticated client fixtures with a test workspace pre-created.
"""

import asyncio
import os
import re
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

from app.core.event_spine import EventSpine

_SAFE_TEST_DB_NAME_RE = re.compile(r"[^a-zA-Z0-9_]+")


def _sanitize_test_db_identifier(value: str | None) -> str:
    sanitized = _SAFE_TEST_DB_NAME_RE.sub("_", (value or "").strip()).strip("_").lower()
    if not sanitized:
        return "oqim_business_test"
    return sanitized[:60].strip("_") or "oqim_business_test"


def _resolve_test_db_name() -> str:
    explicit_name = os.environ.get("OQIM_TEST_DB_NAME")
    if explicit_name:
        return _sanitize_test_db_identifier(explicit_name)
    suffix = os.environ.get("OQIM_TEST_DB_SUFFIX") or os.environ.get("PYTEST_XDIST_WORKER")
    if suffix:
        return _sanitize_test_db_identifier(f"oqim_business_test_{suffix}")
    return "oqim_business_test"


TEST_DB_NAME = _resolve_test_db_name()
TEST_DB_URL = f"postgresql+asyncpg://postgres:postgres@localhost:5434/{TEST_DB_NAME}"


# Override env BEFORE importing app modules
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["REDIS_URL"] = "redis://localhost:6381/15"
os.environ["SECRET_KEY"] = "test-secret-key-for-unit-tests-only-not-production"
os.environ["APP_ENV"] = "development"
os.environ["GEMINI_API_KEY"] = "fake-test-key"
os.environ["SIDECAR_API_KEY"] = "test-sidecar-key"

from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message
from app.models.workspace import Workspace

# ---------------------------------------------------------------------------
# Database setup (once per session, synchronous-safe)
# ---------------------------------------------------------------------------

_db_initialized = False


async def _ensure_test_db():
    """Create the test database and schema (idempotent)."""
    global _db_initialized
    if _db_initialized:
        return

    admin_engine = create_async_engine(
        "postgresql+asyncpg://postgres:postgres@localhost:5434/postgres",
        isolation_level="AUTOCOMMIT",
    )
    async with admin_engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        await conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    await admin_engine.dispose()

    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    _db_initialized = True


async def _drop_test_db():
    admin_engine = create_async_engine(
        "postgresql+asyncpg://postgres:postgres@localhost:5434/postgres",
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
    finally:
        await admin_engine.dispose()


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Clean up isolated proof-harness databases after the subprocess exits."""
    del exitstatus
    if os.environ.get("OQIM_TEST_DB_DROP_AT_END") != "1":
        return
    asyncio.run(_drop_test_db())


# ---------------------------------------------------------------------------
# Per-test engine + session (avoids event-loop mismatch)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """Per-test engine — ensures connections live on the test's event loop."""
    await _ensure_test_db()
    test_engine = create_async_engine(TEST_DB_URL, echo=False)
    yield test_engine
    await test_engine.dispose()


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session with transaction rollback.

    Uses join_transaction_mode="create_savepoint" so that session.commit()
    inside route handlers creates a SAVEPOINT instead of a real commit.
    The outer transaction is always rolled back — zero data leaks.
    """
    connection = await engine.connect()
    transaction = await connection.begin()

    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()


# ---------------------------------------------------------------------------
# EventSpine + fakeredis (reusable across Tasks 11-17)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_redis():
    """In-process Redis for tests. No network; deterministic. Use for EventSpine + diff consumer tests."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def event_spine(fake_redis):
    """EventSpine backed by fakeredis. Drains on teardown so assertions are stable."""
    spine = EventSpine(fake_redis)
    yield spine
    await spine.drain(timeout=1.0)


# ---------------------------------------------------------------------------
# Mock Redis
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    """Mock Redis client that records xadd calls."""
    redis = AsyncMock()
    redis.xadd = AsyncMock(return_value="1234567890-0")
    redis.xread = AsyncMock(return_value=[])
    redis.ping = AsyncMock(return_value=True)
    redis.aclose = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.exists = AsyncMock(return_value=0)
    return redis


# ---------------------------------------------------------------------------
# Test data fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def workspace(db_session: AsyncSession) -> Workspace:
    """Create a test workspace."""
    ws = Workspace(
        phone_number="+998901234567",
        name="Test Do'kon",
        type="ecommerce",
        password_hash=hash_password("testpass123"),
        pipeline_stages=["new", "qualified", "negotiation", "won", "lost"],
    )
    db_session.add(ws)
    await db_session.flush()
    return ws


@pytest_asyncio.fixture
async def workspace_b(db_session: AsyncSession) -> Workspace:
    """Second workspace for isolation tests."""
    ws = Workspace(
        phone_number="+998907654321",
        name="Boshqa Do'kon",
        type="ecommerce",
        password_hash=hash_password("testpass456"),
    )
    db_session.add(ws)
    await db_session.flush()
    return ws


@pytest_asyncio.fixture
async def agent(db_session: AsyncSession, workspace: Workspace) -> Agent:
    """Create a default test agent."""
    ag = Agent(
        workspace_id=workspace.id,
        name="Test AI",
        is_default=True,
        persona={"role": "Sales assistant", "tone": "Friendly"},
        instructions="You are a test assistant.",
        trust_mode="disabled",
        auto_send_threshold=0.85,
        tools_config={"enabled_tools": ["knowledge_search_catalog"]},
        knowledge_config={"use_catalog": True, "use_knowledge": True},
        channel_config={"mode": "dm", "chat_ids": []},
    )
    db_session.add(ag)
    await db_session.flush()
    return ag


@pytest_asyncio.fixture
async def customer(db_session: AsyncSession, workspace: Workspace) -> Customer:
    """Create a test customer."""
    cust = Customer(
        workspace_id=workspace.id,
        display_name="Alisher Valiev",
        phone_number="+998912345678",
        language="uz",
        tags=["VIP", "wholesale"],
        lifetime_value=5_000_000,
    )
    db_session.add(cust)
    await db_session.flush()
    return cust


@pytest_asyncio.fixture
async def conversation(db_session: AsyncSession, workspace: Workspace, customer: Customer) -> Conversation:
    """Create a test conversation."""
    conv = Conversation(
        workspace_id=workspace.id,
        customer_id=customer.id,
        telegram_chat_id=123456789,
        pipeline_stage="new",
        last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(conv)
    await db_session.flush()
    return conv


@pytest_asyncio.fixture
async def message(db_session: AsyncSession, conversation: Conversation) -> Message:
    """Create a test message."""
    msg = Message(
        conversation_id=conversation.id,
        sender_type="customer",
        content="Salom! iPhone 15 bormi?",
    )
    db_session.add(msg)
    await db_session.flush()
    return msg


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def make_token(workspace_id: int) -> str:
    """Create a JWT for a workspace."""
    return create_access_token(subject=str(workspace_id))


@pytest.fixture
def auth_headers(workspace: Workspace) -> dict[str, str]:
    """Authorization headers for the test workspace."""
    return {"Authorization": f"Bearer {make_token(workspace.id)}"}


@pytest.fixture
def auth_cookies(workspace: Workspace) -> dict[str, str]:
    """Session cookies for the test workspace."""
    token = make_token(workspace.id)
    return {"oqim_session": token, "oqim_csrf": "test-csrf-token"}


@pytest.fixture
def auth_headers_b(workspace_b: Workspace) -> dict[str, str]:
    """Authorization headers for the second workspace (isolation tests)."""
    return {"Authorization": f"Bearer {make_token(workspace_b.id)}"}


# ---------------------------------------------------------------------------
# ASGI test client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    mock_redis: AsyncMock,
    event_spine: EventSpine,
) -> AsyncGenerator[AsyncClient, None]:
    """
    AsyncClient that talks to the FastAPI app with:
    - DB session override (uses our savepoint-based rollback session)
    - Redis mock (no real Redis needed)
    - No-op lifespan (no background consumer or real Redis)
    """
    from app.core.config import Settings
    from app.core.deps import (
        get_app_redis,
        get_conversation_turn_runner,
        get_db_session,
        get_delivery_service,
        get_settings_dep,
    )
    from app.services.delivery import DeliveryResult, DeliveryService

    mock_delivery = AsyncMock(spec=DeliveryService)
    mock_delivery.deliver_message = AsyncMock(
        return_value=DeliveryResult(
            success=True,
            external_message_id="mock_ext_123",
        )
    )
    mock_delivery.deliver_media = AsyncMock(
        return_value=DeliveryResult(
            success=True,
            external_message_id="mock_media_ext_123",
            state="confirmed",
        )
    )

    @asynccontextmanager
    async def noop_lifespan(app):
        app.state.app_redis = mock_redis
        app.state.conversation_turn_runner = AsyncMock()
        app.state.delivery = mock_delivery
        yield

    from app.api.routes import telegram_auth as telegram_auth_routes
    from app.main import app

    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = noop_lifespan
    original_app_redis = getattr(app.state, "app_redis", None)
    original_conversation_turn_runner = getattr(app.state, "conversation_turn_runner", None)
    original_delivery = getattr(app.state, "delivery", None)
    original_event_spine = getattr(app.state, "event_spine", None)

    async def override_db():
        yield db_session

    async def override_redis():
        return mock_redis

    mock_turn_runner = AsyncMock()
    mock_turn_runner.enqueue_message = AsyncMock()
    mock_turn_runner.record_agent_message = AsyncMock()

    app.state.app_redis = mock_redis
    app.state.conversation_turn_runner = mock_turn_runner
    app.state.delivery = mock_delivery
    app.state.event_spine = event_spine

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_app_redis] = override_redis
    app.dependency_overrides[get_conversation_turn_runner] = lambda: mock_turn_runner
    app.dependency_overrides[get_delivery_service] = lambda: mock_delivery
    app.dependency_overrides[get_settings_dep] = lambda: Settings(
        _env_file=None,
        SECRET_KEY="test-secret-key-for-unit-tests-only-not-production",
        SIDECAR_API_KEY="test-sidecar-key",
        DATABASE_URL=TEST_DB_URL,
        EVENT_SPINE_PERSIST_MODE="shadow",
    )

    async def _drain_background_tasks(*registries: set[asyncio.Task]) -> None:
        for registry in registries:
            pending = list(registry)
            if pending:
                _, still_pending = await asyncio.wait(pending, timeout=1.0)
                for task in still_pending:
                    task.cancel()
                if still_pending:
                    await asyncio.gather(*still_pending, return_exceptions=True)
            registry.clear()

    transport = ASGITransport(app=app)
    with ExitStack() as stack:
        fake_embedding = [0.0] * 3072
        stack.enter_context(
            patch(
                "app.brain.embedding_service.EmbeddingService.embed_text",
                new=AsyncMock(return_value=fake_embedding),
            )
        )
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

        await _drain_background_tasks(
            telegram_auth_routes._bg_tasks,
        )

    app.dependency_overrides.clear()
    app.router.lifespan_context = original_lifespan
    app.state.app_redis = original_app_redis
    app.state.conversation_turn_runner = original_conversation_turn_runner
    app.state.delivery = original_delivery
    if original_event_spine is not None:
        app.state.event_spine = original_event_spine
    elif hasattr(app.state, "event_spine"):
        delattr(app.state, "event_spine")


# ---------------------------------------------------------------------------
# EventSpine mock fixture for webhook publish tests (Task 8)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_with_fake_spine(db_session: AsyncSession):
    """FastAPI app with EventSpine replaced by a MagicMock for call-site assertions.

    Also wires the minimum app.state and dependency overrides so the app can
    handle requests without a real lifespan (conversation_turn_runner, redis, delivery).
    """
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    from app.core.config import Settings
    from app.core.deps import (
        get_app_redis,
        get_conversation_turn_runner,
        get_db_session,
        get_delivery_service,
        get_settings_dep,
    )
    from app.main import app as real_app
    from app.services.delivery import DeliveryResult, DeliveryService

    fake_spine = MagicMock()
    fake_spine.append = AsyncMock(return_value="1-0")
    mock_redis = AsyncMock()
    mock_turn_runner = AsyncMock()
    mock_turn_runner.enqueue_message = AsyncMock()
    mock_turn_runner.record_agent_message = AsyncMock()
    mock_delivery = AsyncMock(spec=DeliveryService)
    mock_delivery.deliver_message = AsyncMock(
        return_value=DeliveryResult(success=True, external_message_id="mock_ext_123")
    )
    mock_delivery.deliver_media = AsyncMock(
        return_value=DeliveryResult(
            success=True,
            external_message_id="mock_media_ext_123",
            state="confirmed",
        )
    )

    # Set state directly so it's available even without lifespan running.
    original_spine = getattr(real_app.state, "event_spine", None)
    original_conversation_turn_runner = getattr(real_app.state, "conversation_turn_runner", None)
    real_app.state.event_spine = fake_spine
    real_app.state.app_redis = mock_redis
    real_app.state.conversation_turn_runner = mock_turn_runner
    real_app.state.delivery = mock_delivery

    # Also replace lifespan so re-entry doesn't overwrite our mocks.
    original_lifespan = real_app.router.lifespan_context

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    real_app.router.lifespan_context = noop_lifespan

    async def override_db():
        yield db_session

    real_app.dependency_overrides[get_db_session] = override_db
    real_app.dependency_overrides[get_app_redis] = lambda: mock_redis
    real_app.dependency_overrides[get_conversation_turn_runner] = lambda: mock_turn_runner
    real_app.dependency_overrides[get_delivery_service] = lambda: mock_delivery
    real_app.dependency_overrides[get_settings_dep] = lambda: Settings(
        _env_file=None,
        SECRET_KEY="test-secret-key-for-unit-tests-only-not-production",
        SIDECAR_API_KEY="test-sidecar-key",
        DATABASE_URL=TEST_DB_URL,
        EVENT_SPINE_PERSIST_MODE="shadow",
    )

    try:
        yield real_app, fake_spine
    finally:
        real_app.dependency_overrides.clear()
        real_app.router.lifespan_context = original_lifespan
        if original_spine is not None:
            real_app.state.event_spine = original_spine
        real_app.state.conversation_turn_runner = original_conversation_turn_runner


@pytest_asyncio.fixture
async def workspace_with_telegram_user(db_session):
    """A workspace with telegram_user_id so _resolve_workspace finds it."""
    from app.models.workspace import Workspace
    ws = Workspace(
        name="Test WS",
        phone_number="+998901111222",
        telegram_user_id=999_888_777,
    )
    db_session.add(ws)
    await db_session.flush()
    return ws
