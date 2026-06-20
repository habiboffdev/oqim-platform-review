from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.async_tasks import spawn_guarded_task
from app.core.logging import get_logger
from app.core.security import verify_token

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = get_logger("api.ws")

router = APIRouter()

MAX_WS_CONNECTIONS = 5


def _sync_session_event_type(kind: str) -> str:
    if kind == "delta":
        return "session.delta"
    if kind == "noop":
        return "session.noop"
    return "session.reset_required"


class ConnectionManager:
    """Manages active WebSocket connections per workspace.

    Uses Redis pub/sub so broadcast() works across multiple uvicorn workers:
    - broadcast() stamps seq_id, then publishes to `oqim:ws:{workspace_id}`
    - Each worker's subscriber loop calls _deliver_local() for messages it receives
    - Falls back to direct local delivery if Redis is unavailable
    """

    def __init__(self):
        self.connections: dict[int, list[WebSocket]] = {}
        self._active_chats: dict[int, set[int]] = {}
        self._redis: aioredis.Redis | None = None
        self._pubsub_redis: aioredis.Redis | None = None
        self._subscriber_task: asyncio.Task | None = None

    def set_redis(self, redis_client: aioredis.Redis):
        """Inject Redis client for sequence tracking."""
        self._redis = redis_client

    def set_pubsub_redis(self, redis_client: aioredis.Redis):
        """Inject a separate Redis client for pub/sub (must use decode_responses=False)."""
        self._pubsub_redis = redis_client

    async def start_subscriber(self):
        """Start the background task that listens to the Redis pub/sub channel."""
        if self._pubsub_redis is None:
            logger.warning("start_subscriber called but no pub/sub Redis client set — skipping")
            return
        self._subscriber_task = spawn_guarded_task(
            self._subscribe_loop(),
            logger=logger,
            name="ws-pubsub-subscriber",
        )
        logger.info("WebSocket pub/sub subscriber started")

    async def stop_subscriber(self):
        """Cancel the subscriber background task gracefully."""
        if self._subscriber_task and not self._subscriber_task.done():
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass
        self._subscriber_task = None
        logger.info("WebSocket pub/sub subscriber stopped")

    async def _subscribe_loop(self):
        """Background loop: subscribe to oqim:ws:* and deliver locally on each message.

        Reconnects automatically on Redis errors with 2s backoff.
        """
        while True:
            pubsub = self._pubsub_redis.pubsub()
            try:
                await pubsub.psubscribe("oqim:ws:*")
                async for message in pubsub.listen():
                    if message["type"] != "pmessage":
                        continue
                    channel = message["channel"]
                    if isinstance(channel, bytes):
                        channel = channel.decode()
                    workspace_id = int(channel.split(":")[-1])
                    raw = message["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    data = json.loads(raw)
                    await self._deliver_local(workspace_id, data)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("WebSocket pub/sub subscriber crashed, reconnecting in 2s")
            finally:
                try:
                    await pubsub.aclose()
                except Exception:
                    pass  # cleanup — OK to swallow
            await asyncio.sleep(2)

    async def connect(self, workspace_id: int, ws: WebSocket):
        await ws.accept()
        self.connections.setdefault(workspace_id, []).append(ws)
        logger.info("WebSocket connected: workspace=%d", workspace_id)

    def disconnect(self, workspace_id: int, ws: WebSocket):
        if workspace_id in self.connections:
            self.connections[workspace_id] = [
                w for w in self.connections[workspace_id] if w != ws
            ]
            if not self.connections[workspace_id]:
                del self.connections[workspace_id]
                self._active_chats.pop(workspace_id, None)
        logger.info("WebSocket disconnected: workspace=%d", workspace_id)

    def set_active_chat(self, workspace_id: int, conversation_id: int | None):
        if workspace_id not in self._active_chats:
            self._active_chats[workspace_id] = set()
        if conversation_id is None:
            self._active_chats[workspace_id].clear()
        else:
            self._active_chats[workspace_id].add(conversation_id)

    def remove_active_chat(self, workspace_id: int, conversation_id: int):
        if workspace_id in self._active_chats:
            self._active_chats[workspace_id].discard(conversation_id)

    def is_chat_active(self, workspace_id: int, conversation_id: int) -> bool:
        return conversation_id in self._active_chats.get(workspace_id, set())

    async def _deliver_local(self, workspace_id: int, data: dict):
        """Send data to all connections on THIS worker for the given workspace."""
        dead = []
        for ws in self.connections.get(workspace_id, []):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(workspace_id, ws)

    async def broadcast(self, workspace_id: int, data: dict):
        """Stamp seq_id, publish to Redis channel so ALL workers can deliver locally.

        Falls back to direct local delivery if Redis pub/sub is unavailable.
        """
        # Stamp monotonic sequence_id via Redis INCR (best-effort)
        if self._redis:
            try:
                seq = await self._redis.incr(f"oqim:ws_seq:{workspace_id}")
                data["sequence_id"] = seq
            except Exception:
                logger.debug("Suppressed error", exc_info=True)

        if self._pubsub_redis:
            try:
                await self._pubsub_redis.publish(
                    f"oqim:ws:{workspace_id}", json.dumps(data)
                )
                return
            except Exception:
                logger.warning(
                    "broadcast: Redis publish failed for workspace=%d, falling back to local",
                    workspace_id,
                )

        # Fallback: deliver directly to this worker's local connections
        await self._deliver_local(workspace_id, data)


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    token = ws.cookies.get("oqim_session")
    if not token:
        await ws.close(code=4001)
        return

    sub = verify_token(token)
    if not sub:
        await ws.close(code=4001)
        return

    workspace_id = int(sub)

    # Inject Redis if available (lazy — set once from app state)
    if manager._redis is None:
        redis_client = getattr(ws.app.state, "app_redis", None)
        if redis_client is not None:
            manager.set_redis(redis_client)

    # Enforce connection limit
    existing = manager.connections.get(workspace_id, [])
    if len(existing) >= MAX_WS_CONNECTIONS:
        oldest = existing[0]
        try:
            await oldest.close(code=4002)
        except Exception:
            pass  # cleanup — OK to swallow
        manager.disconnect(workspace_id, oldest)

    await manager.connect(workspace_id, ws)
    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
            elif msg_type == "chat_opened":
                conv_id = data.get("conversation_id")
                if conv_id is not None:
                    manager.set_active_chat(workspace_id, conv_id)
            elif msg_type == "chat_closed":
                conv_id = data.get("conversation_id")
                if conv_id is not None:
                    manager.remove_active_chat(workspace_id, conv_id)
            elif msg_type in {"sync", "session.resume"}:
                last_seq = data.get("last_sequence", 0)
                active_conversation_id = data.get("active_conversation_id", data.get("conversation_id"))
                last_seen_conversation_seq = data.get("last_seen_conversation_seq")
                last_seen_conversation_revision = data.get("last_seen_conversation_revision")
                if manager._redis:
                    try:
                        from app.db.session import async_session as session_factory
                        from app.services.sync_session import build_sync_session

                        current_seq = int(
                            await manager._redis.get(f"oqim:ws_seq:{workspace_id}") or 0
                        )
                        if current_seq >= int(last_seq or 0):
                            async with session_factory() as session:
                                response = await build_sync_session(
                                    session=session,
                                    workspace_id=workspace_id,
                                    server_sequence=current_seq,
                                    client_sequence=int(last_seq or 0),
                                    active_conversation_id=(
                                        int(active_conversation_id)
                                        if active_conversation_id is not None
                                        else None
                                    ),
                                    last_seen_conversation_seq=(
                                        last_seen_conversation_seq
                                        if isinstance(last_seen_conversation_seq, int)
                                        else None
                                    ),
                                    last_seen_conversation_revision=(
                                        last_seen_conversation_revision
                                        if isinstance(last_seen_conversation_revision, int)
                                        else None
                                    ),
                                )
                            payload = response.to_websocket_data()
                            await ws.send_json({
                                "type": (
                                    "sync_response"
                                    if msg_type == "sync"
                                    else _sync_session_event_type(response.kind)
                                ),
                                "data": payload,
                                "sequence_id": current_seq,
                            })
                    except Exception:
                        logger.warning("sync handler: Redis error (non-fatal)")
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("WebSocket error for workspace %d", workspace_id, exc_info=True)
    finally:
        manager.disconnect(workspace_id, ws)
