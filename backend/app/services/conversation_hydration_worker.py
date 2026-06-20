from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.api.routes.ws import manager as ws_manager
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.conversation_hydration_runtime import ConversationHydrationRuntime
from app.services.channel_conversation_sync import ChannelConversationSync
from app.services.conversation_hydration_runtime import (
    DEFAULT_LEASE_SECONDS,
    claim_due_conversation_hydration_jobs,
    conversation_needs_hydration,
    latest_local_message_for_conversation,
    mark_conversation_hydration_failed,
    mark_conversation_hydration_success,
    project_conversation_hydration_runtime,
)
from app.services.worker_lease import WorkerLease, make_worker_owner_id

logger = get_logger("services.conversation_hydration_worker")

DEFAULT_POLL_INTERVAL_SECONDS = 1.5
DEFAULT_BATCH_SIZE = 8


class ConversationHydrationWorker:
    """Supervised worker for chat-open history hydration jobs."""

    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        redis: Any | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        sync_service: ChannelConversationSync | None = None,
    ) -> None:
        self._db_factory = db_factory
        self._redis = redis
        self._poll_interval_seconds = max(float(poll_interval_seconds), 0.1)
        self._batch_size = max(1, int(batch_size))
        self._lease_seconds = max(float(lease_seconds), 1.0)
        self._sync = sync_service or ChannelConversationSync()
        self._consumer_name = make_worker_owner_id("conversation_hydration")
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None
        self._lease = (
            WorkerLease(redis, role="conversation_hydration", ttl_seconds=30)
            if redis is not None
            else None
        )

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._heartbeat_callback = callback

    async def start(self) -> None:
        self._stopping = False
        has_lease = False
        while not self._stopping:
            try:
                if self._lease is not None:
                    has_lease = (
                        await self._lease.renew()
                        if has_lease
                        else await self._lease.acquire()
                    )
                    if not has_lease:
                        self._beat()
                        await asyncio.sleep(self._poll_interval_seconds)
                        continue
                processed = await self.run_once()
                self._beat()
                if processed == 0:
                    await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                raise
            except Exception:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                has_lease = False
                logger.exception("conversation_hydration_worker.tick_failed")
                await asyncio.sleep(min(self._poll_interval_seconds * 2, 10.0))
        if has_lease and self._lease is not None:
            await self._lease.release()

    async def stop(self) -> None:
        self._stopping = True

    async def run_once(self, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(timezone.utc)
        async with self._db_factory() as session:
            jobs = await claim_due_conversation_hydration_jobs(
                session,
                lease_owner=self._consumer_name,
                limit=self._batch_size,
                lease_seconds=self._lease_seconds,
                now=current_time,
            )
            job_ids = [int(job.id) for job in jobs]
            await session.commit()

        processed = 0
        for job_id in job_ids:
            self._beat()
            async with self._db_factory() as session:
                runtime = await session.get(ConversationHydrationRuntime, job_id)
                if runtime is None:
                    continue
                conversation = await session.scalar(
                    select(Conversation).where(
                        Conversation.id == runtime.conversation_id,
                        Conversation.workspace_id == runtime.workspace_id,
                    )
                )
                if conversation is None:
                    await mark_conversation_hydration_failed(
                        session,
                        runtime=runtime,
                        error="conversation_missing",
                    )
                    await session.commit()
                    continue

                try:
                    result = await self._sync.sync_conversation(
                        session=session,
                        workspace_id=int(runtime.workspace_id),
                        conversation=conversation,
                        limit=int(runtime.requested_limit or 50),
                    )
                    latest_local_message = await latest_local_message_for_conversation(
                        session,
                        conversation_id=conversation.id,
                    )
                    still_needed = conversation_needs_hydration(
                        conversation,
                        latest_local_message=latest_local_message,
                    )
                    if (
                        still_needed
                        and result.requested == 0
                        and result.persisted == 0
                        and result.duplicates == 0
                    ):
                        await mark_conversation_hydration_failed(
                            session,
                            runtime=runtime,
                            error="history_source_returned_no_messages_while_dialog_tail_is_ahead",
                        )
                        await session.commit()
                        await self._broadcast(
                            runtime,
                            conversation_id=conversation.id,
                            messages_changed=False,
                        )
                        processed += 1
                        continue
                    await mark_conversation_hydration_success(
                        session,
                        runtime=runtime,
                        requested=result.requested,
                        persisted=result.persisted,
                        duplicates=result.duplicates,
                    )
                    await session.commit()
                    await self._broadcast(
                        runtime,
                        conversation_id=conversation.id,
                        messages_changed=bool(result.persisted or result.duplicates),
                    )
                except Exception as exc:
                    logger.warning(
                        "conversation_hydration_worker.job_failed workspace=%d conversation=%d",
                        runtime.workspace_id,
                        runtime.conversation_id,
                        exc_info=True,
                    )
                    await mark_conversation_hydration_failed(
                        session,
                        runtime=runtime,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    await session.commit()
                    await self._broadcast(
                        runtime,
                        conversation_id=runtime.conversation_id,
                        messages_changed=False,
                    )
                processed += 1
        return processed

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()

    async def _broadcast(
        self,
        runtime: ConversationHydrationRuntime,
        *,
        conversation_id: int,
        messages_changed: bool,
    ) -> None:
        try:
            await ws_manager.broadcast(
                int(runtime.workspace_id),
                {
                    "type": "conversation_hydration_updated",
                    "conversation_id": int(conversation_id),
                    "messages_changed": messages_changed,
                    "hydration": project_conversation_hydration_runtime(
                        runtime,
                        needed=runtime.state not in {"ready", "empty"},
                    ).to_payload(),
                },
            )
        except Exception:
            logger.warning(
                "conversation_hydration_worker.websocket_broadcast_failed workspace=%d conversation=%d",
                runtime.workspace_id,
                conversation_id,
                exc_info=True,
            )
