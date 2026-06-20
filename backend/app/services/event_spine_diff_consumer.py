"""Event Spine Diff Consumer (RFC #202 Phase 1 — observability-only).

Reads all per-workspace event streams, compares each event against the
current DB state, increments divergence counters and logs. No DB writes.
"""

from __future__ import annotations

import asyncio
import logging
import time as time_module
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import TypeAdapter

from app.core.consumer_names import make_consumer_name
from app.core.event_spine import (
    DeliveryConfirmed,
    DivergenceKind,
    Event,
    MsgDeleted,
    MsgEdited,
    MsgInbound,
    MsgSent,
)
from sqlalchemy import select

from app.core.redis_streams import reclaim_stale_pending_entries
from app.models.conversation import Conversation
from app.models.event_spine_record import EventSpineRecord
from app.models.message import Message

logger = logging.getLogger("oqim_business.event_spine_diff_consumer")

GROUP_NAME = "diff"
STREAM_KEY_PREFIX = "oqim:events:"
COUNTER_PREFIX = "oqim:event_spine:div:"
GRACE_PERIOD_SECONDS = 2.0
BLOCK_MS = 1000
READ_COUNT = 100
COMPARATOR_TIMEOUT_SECONDS = 1.0
WORKSPACE_LIST_REFRESH_SECONDS = 60.0
SEND_CONFIRM_TIMEOUT_SECONDS = 30.0
SEND_PAIR_KEY_PREFIX = "oqim:event_spine:pair:send:"
DB_SCAN_WINDOW_SECONDS = 600.0  # Scan messages created within last 10 min
DB_SCAN_INTERVAL_SECONDS = 600.0  # Run scan every 10 min
DB_ORPHAN_REPORTED_TTL_SECONDS = 60 * 60 * 24


_event_adapter: TypeAdapter[Event] = TypeAdapter(Event)


class EventSpineDiffConsumer:
    """Supervised consumer: reads EventSpine streams, emits divergence metrics."""

    def __init__(
        self,
        *,
        redis: Any,
        db_factory: Callable[[], Any],
        workspace_ids_provider: Callable[[], list[int]] | None = None,
    ) -> None:
        self._redis = redis
        self._db_factory = db_factory
        self._workspace_ids_provider = workspace_ids_provider or self._scan_workspace_ids
        self._consumer_name = make_consumer_name("event_spine_diff")
        self._running = False
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None
        self._workspace_ids_cache: list[int] = []
        self._cache_refreshed_at: float = 0.0

    # --- Supervisor protocol ------------------------------------------------

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._heartbeat_callback = callback

    async def start(self) -> None:
        self._running = True
        self._stopping = False
        await self._ensure_groups()
        last_sweep_at = 0.0
        last_db_scan_at = 0.0
        while not self._stopping:
            try:
                await self._reclaim_stale()
                count = await self._run_once(block_ms=BLOCK_MS)

                now = time_module.time()
                if now - last_sweep_at > 10.0:
                    await self._sweep_send_confirm_pairs()
                    last_sweep_at = now
                if now - last_db_scan_at > DB_SCAN_INTERVAL_SECONDS:
                    await self._scan_db_for_orphans()
                    last_db_scan_at = now

                self._beat()
                if count == 0:
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("event_spine.diff_consumer_tick_failed", exc_info=exc)
                await asyncio.sleep(2.0)

    async def stop(self) -> None:
        self._stopping = True
        self._running = False

    # --- Internals ----------------------------------------------------------

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()

    async def _scan_workspace_ids(self) -> list[int]:
        """Default provider: SCAN oqim:events:* for active workspace streams."""
        ids: set[int] = set()
        async for key in self._redis.scan_iter(match=f"{STREAM_KEY_PREFIX}*"):
            try:
                ws_id = int(key.rsplit(":", 1)[-1])
                ids.add(ws_id)
            except ValueError:
                continue
        return sorted(ids)

    async def _workspace_ids(self) -> list[int]:
        if time_module.monotonic() - self._cache_refreshed_at > WORKSPACE_LIST_REFRESH_SECONDS:
            provider = self._workspace_ids_provider
            result = provider()
            if asyncio.iscoroutine(result):
                result = await result
            self._workspace_ids_cache = list(result)
            self._cache_refreshed_at = time_module.monotonic()
        return self._workspace_ids_cache

    async def _ensure_groups(self) -> None:
        for ws_id in await self._workspace_ids():
            key = f"{STREAM_KEY_PREFIX}{ws_id}"
            try:
                await self._redis.xgroup_create(key, GROUP_NAME, id="0", mkstream=True)
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    logger.warning("xgroup_create failed for %s: %s", key, exc)

    async def _reclaim_stale(self) -> None:
        for ws_id in await self._workspace_ids():
            key = f"{STREAM_KEY_PREFIX}{ws_id}"
            await reclaim_stale_pending_entries(
                self._redis,
                stream_key=key,
                group_name=GROUP_NAME,
                consumer_name=self._consumer_name,
                count=READ_COUNT,
            )

    async def _run_once(self, block_ms: int = BLOCK_MS) -> int:
        """Single XREADGROUP across all workspace streams. Return processed count."""
        ws_ids = await self._workspace_ids()
        if not ws_ids:
            return 0
        streams = {f"{STREAM_KEY_PREFIX}{ws_id}": ">" for ws_id in ws_ids}
        try:
            response = await self._redis.xreadgroup(
                GROUP_NAME, self._consumer_name, streams,
                count=READ_COUNT, block=block_ms,
            )
        except Exception as exc:
            if "NOGROUP" not in str(exc):
                raise
            logger.warning("event_spine.diff_consumer_missing_group_recovered", exc_info=exc)
            self._cache_refreshed_at = 0.0
            await self._ensure_groups()
            response = await self._redis.xreadgroup(
                GROUP_NAME, self._consumer_name, streams,
                count=READ_COUNT, block=block_ms,
            )
        processed = 0
        for stream_key, entries in response:
            for stream_id, fields in entries:
                try:
                    await asyncio.wait_for(
                        self._dispatch(stream_key, stream_id, fields),
                        timeout=COMPARATOR_TIMEOUT_SECONDS,
                    )
                    await self._redis.xack(stream_key, GROUP_NAME, stream_id)
                    processed += 1
                except asyncio.TimeoutError:
                    logger.warning(
                        "event_spine.comparator_timeout",
                        extra={"stream_key": stream_key, "stream_id": stream_id},
                    )
                    # Not ACKed — stays in PEL, reclaimed later.
                except Exception as exc:
                    logger.error(
                        "event_spine.comparator_error",
                        extra={"stream_key": stream_key, "stream_id": stream_id},
                        exc_info=exc,
                    )
                    # Not ACKed.
        return processed

    async def _dispatch(self, stream_key: str, stream_id: str, fields: dict) -> None:
        try:
            event = _event_adapter.validate_json(fields["payload"])
        except Exception as exc:
            logger.warning(
                "event_spine.event_malformed",
                extra={"stream_key": stream_key, "stream_id": stream_id, "error": str(exc)},
            )
            await self._redis.xack(stream_key, GROUP_NAME, stream_id)
            return

        # Dedup race detection: SETEX with 1s TTL per idempotency_key.
        # Duplicate within window (SETNX fails) = DEDUP_RACED — additive to
        # the normal comparator downstream.
        dedup_key = f"oqim:event_spine:dedup:{event.workspace_id}:{event.idempotency_key}"
        is_new = await self._redis.set(dedup_key, "1", ex=1, nx=True)
        if not is_new:
            await self._record_divergence(DivergenceKind.DEDUP_RACED, event)
        # Fall through to regular comparator logic below.

        # Grace period: wait until event is at least GRACE_PERIOD_SECONDS old.
        age = time_module.time() - event.emitted_at
        if age < GRACE_PERIOD_SECONDS:
            await asyncio.sleep(GRACE_PERIOD_SECONDS - age)

        session_cm = self._db_factory() if self._db_factory else None
        if session_cm is None:
            return
        async with session_cm as session:
            kind = await self._compare(event, session)
            if kind is not None:
                await self._record_divergence(kind, event)

    async def _compare(self, event: Any, session: Any) -> DivergenceKind | None:
        if isinstance(event, MsgInbound):
            return await self._compare_inbound(event, session)
        if isinstance(event, MsgEdited):
            return await self._compare_edited(event, session)
        if isinstance(event, MsgDeleted):
            return await self._compare_deleted(event, session)
        if isinstance(event, MsgSent):
            return await self._compare_sent(event)
        if isinstance(event, DeliveryConfirmed):
            return await self._compare_confirmed(event)
        return None

    async def _compare_sent(self, event: MsgSent) -> DivergenceKind | None:
        """Record MsgSent in a Redis hash for later pairing. Also stamp an
        action_record:{id} key so DeliveryConfirmed can find it quickly.
        """
        pair_key = f"{SEND_PAIR_KEY_PREFIX}{event.workspace_id}"
        await self._redis.hset(pair_key, event.idempotency_key, str(event.emitted_at))
        await self._redis.expire(pair_key, int(SEND_CONFIRM_TIMEOUT_SECONDS * 2))
        if event.action_record_id is not None:
            match_key = f"oqim:event_spine:pair:action_record:{event.workspace_id}:{event.action_record_id}"
            await self._redis.set(
                match_key, event.idempotency_key,
                ex=int(SEND_CONFIRM_TIMEOUT_SECONDS * 2),
            )
        return None

    async def _compare_confirmed(self, event: DeliveryConfirmed) -> DivergenceKind | None:
        """If action_record_id matches a tracked MsgSent, clear it. Else CONFIRM_NO_SEND."""
        pair_key = f"{SEND_PAIR_KEY_PREFIX}{event.workspace_id}"
        send_key = event.causation_id or event.idempotency_key
        if await self._redis.hdel(pair_key, send_key):
            if event.action_record_id is not None:
                match_key = f"oqim:event_spine:pair:action_record:{event.workspace_id}:{event.action_record_id}"
                await self._redis.delete(match_key)
            return None

        if event.action_record_id is not None:
            match_key = f"oqim:event_spine:pair:action_record:{event.workspace_id}:{event.action_record_id}"
            matched = await self._redis.get(match_key)
            if matched:
                await self._redis.delete(match_key)
                await self._redis.hdel(pair_key, matched)
                return None
        return DivergenceKind.CONFIRM_NO_SEND

    async def _sweep_send_confirm_pairs(self) -> None:
        """Iterate outstanding MsgSent entries; any older than 30s → SEND_NO_CONFIRM.

        Called periodically from the main loop (Task 15 integration) and
        explicitly in tests for determinism.
        """
        now = time_module.time()
        for ws_id in await self._workspace_ids():
            pair_key = f"{SEND_PAIR_KEY_PREFIX}{ws_id}"
            all_entries = await self._redis.hgetall(pair_key)
            for idempotency_key, emitted_at_str in all_entries.items():
                try:
                    emitted_at = float(emitted_at_str)
                except ValueError:
                    continue
                if now - emitted_at > SEND_CONFIRM_TIMEOUT_SECONDS:
                    await self._redis.hdel(pair_key, idempotency_key)
                    fake_event = _FakeEventForCounter(
                        workspace_id=ws_id,
                        type="msg.sent",
                        correlation_id=None,
                        idempotency_key=idempotency_key,
                    )
                    await self._record_divergence(DivergenceKind.SEND_NO_CONFIRM, fake_event)

    async def _compare_inbound(self, event: MsgInbound, session: Any) -> DivergenceKind | None:
        """Look for matching DB row. Missing → EVENT_NO_DB. Content mismatch → TEXT_MISMATCH."""
        stmt = (
            select(Message.id, Message.content)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.workspace_id == event.workspace_id,
                Conversation.telegram_chat_id == event.telegram_chat_id,
                Message.telegram_message_id == event.telegram_message_id,
            )
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return DivergenceKind.EVENT_NO_DB
        if event.text and row.content != event.text:
            return DivergenceKind.TEXT_MISMATCH
        return None

    async def _compare_edited(self, event: MsgEdited, session: Any) -> DivergenceKind | None:
        stmt = (
            select(Message.id, Message.content)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.workspace_id == event.workspace_id,
                Conversation.telegram_chat_id == event.telegram_chat_id,
                Message.telegram_message_id == event.telegram_message_id,
            )
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return DivergenceKind.EVENT_NO_DB
        if row.content != event.new_text:
            return DivergenceKind.TEXT_MISMATCH
        return None

    async def _compare_deleted(self, event: MsgDeleted, session: Any) -> DivergenceKind | None:
        """Deleted messages should exist as soft-deleted rows after DB processing."""
        stmt = (
            select(Message.id, Message.is_deleted)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.workspace_id == event.workspace_id,
                Conversation.telegram_chat_id == event.telegram_chat_id,
                Message.telegram_message_id.in_(event.telegram_message_ids),
            )
        )
        rows = (await session.execute(stmt)).all()
        if not rows:
            return DivergenceKind.EVENT_NO_DB
        if any(not is_deleted for _message_id, is_deleted in rows):
            return DivergenceKind.EVENT_NO_DB
        return None

    async def _scan_db_for_orphans(self) -> int:
        """Scan recent Message rows and flag rows with no durable spine append.

        The hot-path dedupe key used by the comparator is intentionally short
        lived, so it cannot prove whether a DB row had a canonical event. Use
        the 7-day append marker created by EventSpine.append instead, and count
        each orphan once so the metric reports distinct drift instead of scanner
        loops.
        """
        if self._db_factory is None:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=DB_SCAN_WINDOW_SECONDS)
        found = 0
        async with self._db_factory() as session:
            for ws_id in await self._workspace_ids():
                stmt = (
                    select(
                        Conversation.telegram_chat_id,
                        Message.telegram_message_id,
                    )
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(
                        Conversation.workspace_id == ws_id,
                        Message.created_at >= cutoff,
                        Message.telegram_message_id.is_not(None),
                        Message.sender_type == "customer",
                    )
                    .limit(1000)
                )
                rows = (await session.execute(stmt)).all()
                for chat_id, msg_id in rows:
                    idempotency_key = f"tg:{chat_id}:{msg_id}"
                    append_key = f"oqim:event_spine:appended:{ws_id}:{idempotency_key}"
                    exists = await self._redis.exists(append_key)
                    if exists:
                        continue
                    archived = await session.scalar(
                        select(EventSpineRecord.id).where(
                            EventSpineRecord.workspace_id == ws_id,
                            EventSpineRecord.idempotency_key == idempotency_key,
                        )
                    )
                    if archived is not None:
                        continue
                    reported_key = (
                        "oqim:event_spine:db_orphan_reported:"
                        f"{ws_id}:{idempotency_key}"
                    )
                    first_report = await self._redis.set(
                        reported_key,
                        "1",
                        ex=DB_ORPHAN_REPORTED_TTL_SECONDS,
                        nx=True,
                    )
                    if not first_report:
                        continue
                    fake = _FakeEventForCounter(
                        workspace_id=ws_id,
                        type="db_scan",
                        correlation_id=None,
                        idempotency_key=idempotency_key,
                    )
                    await self._record_divergence(DivergenceKind.DB_NO_EVENT, fake)
                    found += 1
        return found

    async def _record_divergence(self, kind: DivergenceKind, event: Any) -> None:
        pipe = self._redis.pipeline()
        pipe.incr(f"{COUNTER_PREFIX}{kind.value}")
        pipe.incr(f"{COUNTER_PREFIX}{kind.value}:{event.workspace_id}")
        await pipe.execute()
        logger.warning(
            "event_spine.divergence",
            extra={
                "kind": kind.value,
                "event_type": event.type,
                "workspace_id": event.workspace_id,
                "correlation_id": event.correlation_id,
                "idempotency_key": event.idempotency_key,
            },
        )


class _FakeEventForCounter:
    """Minimal event-shaped object for _record_divergence when we only have
    the idempotency_key (e.g., during timeout sweeps). Keeps the counter
    recording path uniform."""

    def __init__(self, *, workspace_id: int, type: str, correlation_id: str | None,
                 idempotency_key: str) -> None:
        self.workspace_id = workspace_id
        self.type = type
        self.correlation_id = correlation_id
        self.idempotency_key = idempotency_key
