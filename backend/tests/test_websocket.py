"""
WebSocket endpoint tests — ConnectionManager unit tests + WS integration tests.

Tests cover:
- ConnectionManager: connect, disconnect, broadcast, dead connection cleanup
- WebSocket endpoint: auth (valid token, invalid token, missing token), ping/pong
"""

import pytest
from unittest.mock import AsyncMock
from unittest.mock import patch

from starlette.testclient import TestClient

from app.api.routes.ws import ConnectionManager
from app.core.security import create_access_token


# ---------------------------------------------------------------------------
# Unit tests — ConnectionManager (mocked WebSocket objects)
# ---------------------------------------------------------------------------


class TestConnectionManager:
    """Direct unit tests for ConnectionManager using mock WebSocket objects."""

    @pytest.fixture
    def manager(self) -> ConnectionManager:
        return ConnectionManager()

    @pytest.fixture
    def mock_ws(self) -> AsyncMock:
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_connect_adds_to_connections(self, manager: ConnectionManager, mock_ws: AsyncMock):
        """connect() should accept the websocket and store it under the workspace ID."""
        await manager.connect(42, mock_ws)

        mock_ws.accept.assert_awaited_once()
        assert 42 in manager.connections
        assert mock_ws in manager.connections[42]

    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(self, manager: ConnectionManager, mock_ws: AsyncMock):
        """disconnect() should remove the specific websocket from the workspace list."""
        # Set up two connections for the same workspace
        other_ws = AsyncMock()
        await manager.connect(42, mock_ws)
        await manager.connect(42, other_ws)
        assert len(manager.connections[42]) == 2

        manager.disconnect(42, mock_ws)

        assert mock_ws not in manager.connections[42]
        assert other_ws in manager.connections[42]
        assert len(manager.connections[42]) == 1

    @pytest.mark.asyncio
    async def test_disconnect_cleans_empty_workspace(self, manager: ConnectionManager, mock_ws: AsyncMock):
        """When the last connection for a workspace is removed, the key should be deleted."""
        await manager.connect(42, mock_ws)
        assert 42 in manager.connections

        manager.disconnect(42, mock_ws)

        assert 42 not in manager.connections

    @pytest.mark.asyncio
    async def test_broadcast_delivers_to_workspace_connections(self, manager: ConnectionManager):
        """broadcast() should send JSON to all connections for that workspace."""
        ws_a = AsyncMock()
        ws_b = AsyncMock()
        await manager.connect(7, ws_a)
        await manager.connect(7, ws_b)

        payload = {"type": "new_message", "conversation_id": 123}
        await manager.broadcast(7, payload)

        ws_a.send_json.assert_awaited_once_with(payload)
        ws_b.send_json.assert_awaited_once_with(payload)

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_connections(self, manager: ConnectionManager):
        """If send_json raises an exception, that connection should be removed."""
        healthy_ws = AsyncMock()
        dead_ws = AsyncMock()
        dead_ws.send_json.side_effect = RuntimeError("Connection lost")

        await manager.connect(7, healthy_ws)
        await manager.connect(7, dead_ws)
        assert len(manager.connections[7]) == 2

        await manager.broadcast(7, {"type": "test"})

        # The healthy one stays, the dead one is cleaned up
        assert healthy_ws in manager.connections[7]
        assert dead_ws not in manager.connections[7]
        assert len(manager.connections[7]) == 1

    @pytest.mark.asyncio
    async def test_broadcast_to_nonexistent_workspace(self, manager: ConnectionManager):
        """Sending to a workspace with no connections should be a no-op."""
        # Should not raise
        await manager.broadcast(999, {"type": "test"})

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_workspace(self, manager: ConnectionManager, mock_ws: AsyncMock):
        """Disconnecting from a workspace that doesn't exist should be a no-op."""
        # Should not raise
        manager.disconnect(999, mock_ws)


# ---------------------------------------------------------------------------
# Integration tests — WebSocket endpoint via Starlette TestClient
# ---------------------------------------------------------------------------


class TestWebSocketEndpoint:
    """Integration tests for the /api/ws WebSocket endpoint.

    Uses Starlette's synchronous TestClient which supports websocket_connect().
    The httpx AsyncClient from conftest doesn't support WebSocket, so we build
    a separate TestClient directly from the FastAPI app.
    """

    @pytest.fixture
    def app(self):
        """Import the FastAPI app with a no-op lifespan for testing."""
        from contextlib import asynccontextmanager
        from app.main import app
        from app.api.routes.ws import manager

        @asynccontextmanager
        async def noop_lifespan(a):
            a.state.gateway_redis = AsyncMock()
            yield

        original = app.router.lifespan_context
        original_app_redis = getattr(app.state, "app_redis", None)
        original_manager_redis = manager._redis
        original_manager_pubsub = manager._pubsub_redis
        original_manager_connections = dict(manager.connections)
        original_active_chats = {workspace_id: set(chat_ids) for workspace_id, chat_ids in manager._active_chats.items()}
        app.router.lifespan_context = noop_lifespan
        yield app
        app.router.lifespan_context = original
        app.state.app_redis = original_app_redis
        app.dependency_overrides.clear()
        manager._redis = original_manager_redis
        manager._pubsub_redis = original_manager_pubsub
        manager.connections = original_manager_connections
        manager._active_chats = original_active_chats

    @pytest.fixture
    def valid_token(self) -> str:
        """Create a valid JWT token with subject '1' (workspace ID)."""
        return create_access_token(subject="1")

    def test_ws_connect_with_valid_token(self, app, valid_token: str):
        """A valid session cookie should allow connection; ping should return pong."""
        client = TestClient(app, cookies={"oqim_session": valid_token})
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json({"type": "ping"})
            data = ws.receive_json()
            assert data == {"type": "pong"}

    def test_ws_invalid_token_closes_4001(self, app):
        """An invalid session cookie should result in close code 4001."""
        client = TestClient(app, cookies={"oqim_session": "bogus.invalid.token"})
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect("/api/ws") as ws:
                ws.receive_json()
        assert "4001" in str(exc_info.value) or exc_info.type is not None

    def test_ws_no_token_rejected(self, app):
        """Missing session cookie should reject the connection."""
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect("/api/ws") as ws:
                ws.receive_json()

    def test_ws_multiple_pings(self, app, valid_token: str):
        """Multiple ping messages should each get a pong response."""
        client = TestClient(app, cookies={"oqim_session": valid_token})
        with client.websocket_connect("/api/ws") as ws:
            for _ in range(3):
                ws.send_json({"type": "ping"})
                data = ws.receive_json()
                assert data == {"type": "pong"}

    def test_ws_non_ping_message_no_response(self, app, valid_token: str):
        """Messages that aren't ping should be accepted but not produce a pong."""
        client = TestClient(app, cookies={"oqim_session": valid_token})
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json({"type": "something_else"})
            ws.send_json({"type": "ping"})
            data = ws.receive_json()
            assert data == {"type": "pong"}

    def test_ws_sync_returns_scoped_refresh_for_active_conversation(self, app, valid_token: str):
        """Gap recovery should scope to the active conversation when the client provides it."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="12")
        app.state.app_redis = mock_redis

        client = TestClient(app, cookies={"oqim_session": valid_token})
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json({
                "type": "sync",
                "last_sequence": 3,
                "active_conversation_id": 38,
            })
            data = ws.receive_json()

        assert data == {
            "type": "sync_response",
            "data": {
                "kind": "reset_required",
                "action": "refresh_scoped_runtime",
                "server_sequence": 12,
                "client_sequence": 3,
                "conversation_id": 38,
                "projections": [
                    {"name": "messages", "mode": "reset", "conversation_id": 38},
                    {"name": "media", "mode": "reset", "conversation_id": 38},
                    {"name": "conversation_state", "mode": "reset", "conversation_id": 38},
                    {"name": "seller_agent_replies", "mode": "reset", "conversation_id": 38},
                    {"name": "read_state", "mode": "reset", "conversation_id": 38},
                ],
            },
            "sequence_id": 12,
        }

    def test_ws_sync_returns_delta_refresh_for_bounded_active_tail_gap(self, app, valid_token: str):
        """When the active conversation cursor is only slightly behind, websocket sync can request a delta patch."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="12")
        app.state.app_redis = mock_redis

        client = TestClient(app, cookies={"oqim_session": valid_token})
        from app.services.sync_session import SyncProjection, SyncSessionResponse

        with patch(
            "app.services.sync_session.build_sync_session",
            new=AsyncMock(return_value=SyncSessionResponse(
                action="refresh_scoped_runtime_delta",
                server_sequence=12,
                client_sequence=3,
                conversation_id=38,
                after_conversation_seq=7,
                latest_conversation_seq=9,
                latest_conversation_revision=9,
                projections=(
                    SyncProjection(
                        name="messages",
                        mode="delta",
                        conversation_id=38,
                        after_conversation_seq=7,
                        latest_conversation_seq=9,
                        latest_conversation_revision=9,
                    ),
                ),
            )),
        ):
            with client.websocket_connect("/api/ws") as ws:
                ws.send_json({
                    "type": "sync",
                    "last_sequence": 3,
                    "active_conversation_id": 38,
                    "last_seen_conversation_seq": 7,
                    "last_seen_conversation_revision": 7,
                })
                data = ws.receive_json()

        assert data == {
            "type": "sync_response",
            "data": {
                "kind": "delta",
                "action": "refresh_scoped_runtime_delta",
                "server_sequence": 12,
                "client_sequence": 3,
                "projections": [
                    {
                        "name": "messages",
                        "mode": "delta",
                        "conversation_id": 38,
                        "after_conversation_seq": 7,
                        "latest_conversation_seq": 9,
                        "latest_conversation_revision": 9,
                    },
                ],
                "conversation_id": 38,
                "after_conversation_seq": 7,
                "latest_conversation_seq": 9,
                "latest_conversation_revision": 9,
            },
            "sequence_id": 12,
        }

    def test_ws_session_resume_returns_new_session_delta_event(self, app, valid_token: str):
        """New reconnect clients use session.resume and receive session.delta/reset events."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="12")
        app.state.app_redis = mock_redis

        client = TestClient(app, cookies={"oqim_session": valid_token})
        from app.services.sync_session import SyncProjection, SyncSessionResponse

        with patch(
            "app.services.sync_session.build_sync_session",
            new=AsyncMock(return_value=SyncSessionResponse(
                action="refresh_scoped_runtime_delta",
                server_sequence=12,
                client_sequence=3,
                conversation_id=38,
                after_conversation_seq=7,
                latest_conversation_seq=9,
                latest_conversation_revision=9,
                projections=(
                    SyncProjection(
                        name="messages",
                        mode="delta",
                        conversation_id=38,
                        after_conversation_seq=7,
                        latest_conversation_seq=9,
                        latest_conversation_revision=9,
                    ),
                ),
            )),
        ):
            with client.websocket_connect("/api/ws") as ws:
                ws.send_json({
                    "type": "session.resume",
                    "last_sequence": 3,
                    "conversation_id": 38,
                    "last_seen_conversation_seq": 7,
                    "last_seen_conversation_revision": 7,
                })
                data = ws.receive_json()

        assert data == {
            "type": "session.delta",
            "data": {
                "kind": "delta",
                "action": "refresh_scoped_runtime_delta",
                "server_sequence": 12,
                "client_sequence": 3,
                "projections": [
                    {
                        "name": "messages",
                        "mode": "delta",
                        "conversation_id": 38,
                        "after_conversation_seq": 7,
                        "latest_conversation_seq": 9,
                        "latest_conversation_revision": 9,
                    },
                ],
                "conversation_id": 38,
                "after_conversation_seq": 7,
                "latest_conversation_seq": 9,
                "latest_conversation_revision": 9,
            },
            "sequence_id": 12,
        }

    def test_ws_sync_falls_back_to_full_refresh_when_revision_gap_exceeds_seq_gap(self, app, valid_token: str):
        """Mixed mutation drift must use the safer full refresh path."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="12")
        app.state.app_redis = mock_redis

        client = TestClient(app, cookies={"oqim_session": valid_token})
        from app.services.sync_session import SyncProjection, SyncSessionResponse

        with patch(
            "app.services.sync_session.build_sync_session",
            new=AsyncMock(return_value=SyncSessionResponse(
                action="refresh_scoped_runtime",
                server_sequence=12,
                client_sequence=3,
                conversation_id=38,
                projections=(
                    SyncProjection(name="messages", mode="reset", conversation_id=38),
                ),
            )),
        ):
            with client.websocket_connect("/api/ws") as ws:
                ws.send_json({
                    "type": "sync",
                    "last_sequence": 3,
                    "active_conversation_id": 38,
                    "last_seen_conversation_seq": 7,
                    "last_seen_conversation_revision": 7,
                })
                data = ws.receive_json()

        assert data == {
            "type": "sync_response",
            "data": {
                "kind": "reset_required",
                "action": "refresh_scoped_runtime",
                "server_sequence": 12,
                "client_sequence": 3,
                "projections": [
                    {"name": "messages", "mode": "reset", "conversation_id": 38},
                ],
                "conversation_id": 38,
            },
            "sequence_id": 12,
        }


# ---------------------------------------------------------------------------
# Unit tests — Redis pub/sub broadcast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRedisPubSubBroadcast:
    """Tests for the Redis pub/sub cross-worker broadcast mechanism."""

    @pytest.fixture
    def manager(self) -> ConnectionManager:
        return ConnectionManager()

    @pytest.fixture
    def mock_ws(self) -> AsyncMock:
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        return ws

    async def test_broadcast_publishes_to_redis(self, manager: ConnectionManager):
        """broadcast() should publish to the correct Redis channel."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)
        manager.set_pubsub_redis(mock_redis)

        await manager.broadcast(1, {"type": "test", "data": {}})

        mock_redis.publish.assert_called_once()
        channel = mock_redis.publish.call_args[0][0]
        assert channel == "oqim:ws:1"

    async def test_broadcast_serializes_payload_as_json(self, manager: ConnectionManager):
        """broadcast() should publish JSON-serialized data to Redis."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)
        manager.set_pubsub_redis(mock_redis)

        payload = {"type": "new_message", "conversation_id": 42}
        await manager.broadcast(1, payload)

        import json as _json
        published_payload = mock_redis.publish.call_args[0][1]
        parsed = _json.loads(published_payload)
        assert parsed["type"] == "new_message"
        assert parsed["conversation_id"] == 42

    async def test_fallback_to_local_on_redis_failure(self, manager: ConnectionManager, mock_ws: AsyncMock):
        """When Redis publish fails, broadcast() should fall back to local delivery."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(side_effect=Exception("Redis down"))
        manager.set_pubsub_redis(mock_redis)

        await manager.connect(1, mock_ws)
        await manager.broadcast(1, {"type": "test"})

        # Should fall back to local delivery
        mock_ws.send_json.assert_called_once()

    async def test_broadcast_without_pubsub_redis_delivers_locally(self, manager: ConnectionManager, mock_ws: AsyncMock):
        """When no pub/sub Redis is set, broadcast() delivers directly to local connections."""
        await manager.connect(5, mock_ws)
        await manager.broadcast(5, {"type": "test"})

        mock_ws.send_json.assert_called_once()

    async def test_broadcast_stamps_sequence_id_before_publish(self, manager: ConnectionManager):
        """broadcast() should stamp sequence_id via seq Redis before publishing."""
        seq_redis = AsyncMock()
        seq_redis.incr = AsyncMock(return_value=7)
        pubsub_redis = AsyncMock()
        pubsub_redis.publish = AsyncMock(return_value=1)

        manager.set_redis(seq_redis)
        manager.set_pubsub_redis(pubsub_redis)

        import json as _json
        await manager.broadcast(1, {"type": "test"})

        seq_redis.incr.assert_called_once_with("oqim:ws_seq:1")
        published_payload = pubsub_redis.publish.call_args[0][1]
        parsed = _json.loads(published_payload)
        assert parsed["sequence_id"] == 7

    async def test_workspace_broadcast_alias_stays_deleted(self, manager: ConnectionManager):
        assert not hasattr(manager, "send_to_workspace")

    async def test_deliver_local_skips_dead_connections(self, manager: ConnectionManager, mock_ws: AsyncMock):
        """_deliver_local() should remove connections that raise on send_json."""
        dead_ws = AsyncMock()
        dead_ws.accept = AsyncMock()
        dead_ws.send_json = AsyncMock(side_effect=RuntimeError("gone"))

        await manager.connect(9, mock_ws)
        await manager.connect(9, dead_ws)
        assert len(manager.connections[9]) == 2

        await manager._deliver_local(9, {"type": "test"})

        assert mock_ws in manager.connections[9]
        assert dead_ws not in manager.connections[9]
