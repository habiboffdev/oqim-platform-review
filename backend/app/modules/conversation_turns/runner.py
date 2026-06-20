from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.consumer_names import make_consumer_name
from app.core.redis_streams import ensure_consumer_group, is_missing_consumer_group_error
from app.db.session import async_session
from app.models.conversation import Conversation
from app.models.conversation_turn_session import ConversationTurnSession
from app.models.customer import Customer
from app.models.message import Message, SenderType
from app.modules.agent_runtime_v2.dispatcher import dispatch_agent_turn
from app.modules.conversation_turns.contracts import TurnLease
from app.modules.conversation_turns.lifecycle import TurnLifecycle
from app.modules.conversation_turns.service import ConversationTurnSessionService
from app.modules.hermes_runtime.service import HermesRunService

logger = logging.getLogger("oqim_business.conversation_turn_runner")

TURN_WAKEUP_STREAM = "oqim:turn_sessions:wakeups"
TURN_WAKEUP_GROUP = "oqim-turn-runners"
TURN_WAKEUP_QUEUED_PREFIX = "oqim:turn_sessions:queued"
TURN_TELEMETRY_PREFIX = "oqim:turn_sessions:telemetry"
TURN_WAKEUP_TTL_SECONDS = 900
TURN_RUNNER_READ_BATCH_SIZE = 100
TURN_RUNNER_BLOCK_MS = 50
TURN_RUNNER_MAX_PER_WORKSPACE = 2
TURN_RUNNER_LEASE_SECONDS = 45
# A FAST_INTERACTIVE reply turn completes in seconds; anything 'running' for
# minutes is orphaned (the turn died between mark_running and complete, #418).
# Comfortably above any legitimate turn so a live run is never reclaimed.
TURN_RUNNER_RUN_TTL_SECONDS = 600
# Bounded intra-process dispatch concurrency (PRD #433). The serial loop made one
# slow turn block the next customer; the DB unique index (not serialism) preserves
# one-active-run-per-Agent-Session, so different conversations dispatch in parallel.
TURN_RUNNER_DISPATCH_CONCURRENCY = 4


def _clean_trigger_telemetry(value: dict | None) -> dict:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        try:
            cleaned[key] = float(raw)
        except (TypeError, ValueError):
            continue
    return cleaned


class ConversationTurnRunner:
    """DB-first worker that turns open ConversationTurnSession rows into replies."""

    def __init__(
        self,
        *,
        redis_url: str,
        redis: aioredis.Redis | None = None,
        db_factory: Callable[[], Any] = async_session,
        dispatch_agent_runtime: Callable[..., Any] = dispatch_agent_turn,
        delivery: Any | None = None,
        dispatch_concurrency: int = TURN_RUNNER_DISPATCH_CONCURRENCY,
        max_per_workspace: int = TURN_RUNNER_MAX_PER_WORKSPACE,
    ) -> None:
        self._redis_url = redis_url
        self._redis = redis
        self._enqueue_redis: aioredis.Redis | None = redis
        self._db_factory = db_factory
        self._dispatch_agent_runtime = dispatch_agent_runtime
        self._delivery = delivery
        self._dispatch_concurrency = max(1, dispatch_concurrency)
        self._max_per_workspace = max(1, max_per_workspace)
        self._consumer_name = make_consumer_name("conversation_turn")
        self._running = False
        self._heartbeat_callback: Callable[[], None] | None = None

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._heartbeat_callback = callback

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()

    @staticmethod
    def queued_job_key(workspace_id: int, conversation_id: int) -> str:
        return f"{TURN_WAKEUP_QUEUED_PREFIX}:{workspace_id}:{conversation_id}"

    async def _get_enqueue_redis(self) -> aioredis.Redis:
        if self._enqueue_redis is None:
            self._enqueue_redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
            )
        return self._enqueue_redis

    async def enqueue_conversation(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
    ) -> str:
        redis = await self._get_enqueue_redis()
        queued = await redis.set(
            self.queued_job_key(workspace_id, conversation_id),
            "1",
            ex=TURN_WAKEUP_TTL_SECONDS,
            nx=True,
        )
        if not queued:
            return "coalesced"
        return await redis.xadd(
            TURN_WAKEUP_STREAM,
            {
                "workspace_id": str(workspace_id),
                "conversation_id": str(conversation_id),
            },
            maxlen=10000,
            approximate=True,
        )

    async def enqueue_message(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
        message_id: int,
        trigger_telemetry: dict | None = None,
    ) -> str:
        telemetry = _clean_trigger_telemetry(trigger_telemetry)
        if telemetry:
            redis = await self._get_enqueue_redis()
            await redis.set(
                self.telemetry_key(workspace_id, conversation_id, message_id),
                json.dumps(telemetry),
                ex=TURN_WAKEUP_TTL_SECONDS,
            )
        return await self.enqueue_conversation(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        )

    @staticmethod
    def telemetry_key(workspace_id: int, conversation_id: int, message_id: int) -> str:
        return f"{TURN_TELEMETRY_PREFIX}:{workspace_id}:{conversation_id}:{message_id}"

    async def cancel_conversation(self, conversation_id: int) -> None:
        _ = conversation_id

    async def record_agent_message(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
        message_id: int,
    ) -> None:
        _ = workspace_id, conversation_id, message_id

    async def run_once(
        self,
        *,
        limit: int | None = None,
        max_per_workspace: int | None = None,
        lease_ttl_seconds: int = TURN_RUNNER_LEASE_SECONDS,
        run_ttl_seconds: int = TURN_RUNNER_RUN_TTL_SECONDS,
    ) -> int:
        limit = limit if limit is not None else self._dispatch_concurrency
        # HARD INVARIANT: never lease more turns than can DISPATCH concurrently
        # within the lease TTL. A leased-but-parked turn (waiting on the dispatch
        # semaphore) would still age toward lease_ttl_seconds; the same runner's
        # next maintenance phase would reclaim it as stale and re-lease it →
        # double-dispatch (the 2026-06-11-class failure). Clamping the lease limit
        # to the concurrency bound keeps the coupling structural, not incidental on
        # the default-value match — so an explicit run_once(limit=N>concurrency)
        # caller can never construct the parked-then-reclaimed regime.
        limit = min(limit, self._dispatch_concurrency)
        max_per_workspace = (
            max_per_workspace if max_per_workspace is not None else self._max_per_workspace
        )
        async with self._db_factory() as db:
            service = ConversationTurnSessionService(db)
            # Maintenance and leasing are ISOLATED phases: a poisoned row in
            # one must never take down the runner loop. The 2026-06-11 outage
            # turned one duplicate turn into a workspace-wide reply halt
            # because a single UniqueViolationError crash-looped run_once.
            try:
                await service.reclaim_stale_turn_leases(
                    lease_ttl_seconds=lease_ttl_seconds,
                    limit=max(limit * 2, limit),
                )
                # Truthful execution record: a turn aborted before finalization
                # must not leave its HermesRun lying as 'running' forever (#418).
                await HermesRunService(db).reclaim_stale_running_runs(
                    ttl_seconds=run_ttl_seconds,
                    limit=max(limit * 2, limit),
                )
                # ...and the turn-session it owned must not stay non-terminal:
                # the lease reclaimer only recovers 'starting', so a turn killed
                # while 'running'/'finalizing' (oqim-api restart mid-turn) would
                # otherwise swallow every later message and wedge the chat silent.
                await service.reconcile_failed_run_turns(
                    limit=max(limit * 2, limit),
                )
                await db.commit()
            except SQLAlchemyError:
                logger.exception("Turn maintenance failed; continuing to lease")
                await db.rollback()
            try:
                leases = await service.lease_ready_turns(
                    limit=limit,
                    max_per_workspace=max_per_workspace,
                )
                await db.commit()
            except SQLAlchemyError:
                logger.exception("Turn leasing failed; skipping this cycle")
                await db.rollback()
                leases = []

        return await self._dispatch_leases(leases)

    async def _dispatch_leases(self, leases: list[TurnLease]) -> int:
        """Dispatch the leased batch CONCURRENTLY, bounded by dispatch_concurrency.
        Different conversations run in parallel; the DB partial unique index
        (uq_conversation_turn_sessions_active) guarantees no two turns of the SAME
        conversation are ever in one batch, so one-active-run-per-Agent-Session holds
        without a per-session guard here. Each lease is isolated: a raising dispatch
        releases that lease and never aborts the others."""
        if not leases:
            return 0
        semaphore = asyncio.Semaphore(self._dispatch_concurrency)

        async def _one(lease: TurnLease) -> int:
            async with semaphore:
                self._beat()
                try:
                    return 1 if await self._dispatch_lease(lease) else 0
                except Exception:
                    logger.exception(
                        "Conversation turn dispatch failed: workspace=%d conv=%d turn=%d",
                        lease.workspace_id,
                        lease.conversation_id,
                        lease.turn_session_id,
                    )
                    await self._release_failed_lease(lease, reason="dispatch_error")
                    return 0

        results = await asyncio.gather(*(_one(lease) for lease in leases))
        return sum(results)

    async def start(self) -> None:
        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        self._enqueue_redis = self._redis
        await ensure_consumer_group(
            self._redis,
            stream_key=TURN_WAKEUP_STREAM,
            group_name=TURN_WAKEUP_GROUP,
        )
        self._running = True
        logger.info("ConversationTurnRunner started")
        while self._running:
            self._beat()
            try:
                processed = await self.run_once()
                hints = await self._read_wakeup_hints(block_ms=TURN_RUNNER_BLOCK_MS)
                if processed == 0 and hints == 0:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("ConversationTurnRunner loop error")
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._redis is not None:
            await self._redis.aclose()
        if self._enqueue_redis is not None and self._enqueue_redis is not self._redis:
            await self._enqueue_redis.aclose()
        logger.info("ConversationTurnRunner stopped")

    async def _read_wakeup_hints(self, *, block_ms: int) -> int:
        if self._redis is None:
            return 0
        try:
            results = await self._redis.xreadgroup(
                TURN_WAKEUP_GROUP,
                self._consumer_name,
                {TURN_WAKEUP_STREAM: ">"},
                count=TURN_RUNNER_READ_BATCH_SIZE,
                block=block_ms,
            )
        except Exception as exc:
            if not is_missing_consumer_group_error(exc):
                raise
            await ensure_consumer_group(
                self._redis,
                stream_key=TURN_WAKEUP_STREAM,
                group_name=TURN_WAKEUP_GROUP,
            )
            return 0

        count = 0
        for _stream_name, messages in results:
            for msg_id, _data in messages:
                count += 1
                await self._redis.xack(TURN_WAKEUP_STREAM, TURN_WAKEUP_GROUP, msg_id)
        return count

    async def _dispatch_lease(self, lease: TurnLease) -> bool:
        async with self._db_factory() as db:
            turn = await db.get(ConversationTurnSession, int(lease.turn_session_id))
            conversation = await db.get(Conversation, int(lease.conversation_id))
            message = await db.get(Message, int(lease.latest_customer_message_id))
            customer = (
                await db.get(Customer, int(conversation.customer_id))
                if conversation
                else None
            )
            if not turn or not conversation or not message or not customer:
                await self._complete_turn(db, turn=turn, reason="missing_dispatch_records")
                await db.commit()
                await self._finalize_wakeup_slot(lease)
                return False
            if message.sender_type != SenderType.CUSTOMER.value:
                await self._complete_turn(db, turn=turn, reason="trigger_not_customer")
                await db.commit()
                await self._finalize_wakeup_slot(lease)
                return False
            telegram_chat_id = (
                int(conversation.telegram_chat_id)
                if conversation.telegram_chat_id is not None
                else None
            )
            if telegram_chat_id is None and conversation.channel not in (
                "sandbox",
                "instagram_dm",
                "whatsapp_dm",
            ):
                await self._complete_turn(db, turn=turn, reason="missing_channel_chat_id")
                await db.commit()
                await self._finalize_wakeup_slot(lease)
                return False

            trigger_telemetry = await self._load_trigger_telemetry(lease)
            # The coalesced turn is the WHOLE burst, not just its last
            # message — "salom" + question must both reach the model
            # (live failure 2026-06-11: the dropped greeting was never seen).
            burst_messages = list(
                (
                    await db.execute(
                        select(Message)
                        .where(
                            Message.conversation_id == int(lease.conversation_id),
                            Message.sender_type == SenderType.CUSTOMER.value,
                            Message.id >= int(turn.first_customer_message_id),
                            Message.id <= int(turn.latest_customer_message_id),
                            Message.is_deleted.is_(False),
                        )
                        .order_by(Message.id.asc())
                    )
                ).scalars().all()
            )
            queued = await self._dispatch_agent_runtime(
                db=db,
                workspace_id=int(lease.workspace_id),
                telegram_chat_id=telegram_chat_id,
                customer=customer,
                conversation=conversation,
                message=message,
                burst_messages=burst_messages,
                turn_session=turn,
                media_type=getattr(message, "media_type", None),
                trigger_telemetry=trigger_telemetry,
                delivery=self._delivery,
            )
            if not queued:
                await self._complete_turn(db, turn=turn, reason="dispatch_skipped")
            await db.commit()
            await self._finalize_wakeup_slot(lease)
            return True

    async def _load_trigger_telemetry(self, lease: TurnLease) -> dict | None:
        redis = self._redis or self._enqueue_redis
        if redis is None:
            return None
        key = self.telemetry_key(
            lease.workspace_id,
            lease.conversation_id,
            lease.latest_customer_message_id,
        )
        raw = await redis.get(key)
        await redis.delete(key)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return _clean_trigger_telemetry(value) or None

    async def _finalize_wakeup_slot(self, lease: TurnLease) -> None:
        redis = self._redis or self._enqueue_redis
        if redis is None:
            return
        await redis.delete(
            self.queued_job_key(lease.workspace_id, lease.conversation_id)
        )

    async def _complete_turn(
        self,
        db: Any,
        *,
        turn: ConversationTurnSession | None,
        reason: str,
    ) -> None:
        if turn is None:
            return
        await TurnLifecycle(db).complete_pre_dispatch(turn, reason=reason)

    async def _release_failed_lease(self, lease: TurnLease, *, reason: str) -> None:
        async with self._db_factory() as db:
            turn = await db.get(ConversationTurnSession, int(lease.turn_session_id))
            if turn is not None and turn.state == "starting":
                # increments failed_dispatch_count, then quarantines on the 3rd
                # strike (#415) or reopens to 'open'; logs + flushes internally.
                await TurnLifecycle(db).release_failed(turn, reason=reason)
            await db.commit()
