from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from app.core.event_spine import MediaHydrationStateChanged
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.media_runtime import MediaRuntime
from app.models.message import Message
from app.services.media_hydration_runtime import hydrate_media_runtime_job
from app.services.media_runtime import (
    DEFAULT_LEASE_SECONDS,
    DEFAULT_PENDING_GRACE_SECONDS,
    claim_due_media_hydration_jobs_for_all_workspaces,
)

logger = get_logger("services.media_hydration_worker")

DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_CLAIM_LIMIT = 25
DEFAULT_MAX_CLAIMS_PER_WORKSPACE = 2


class MediaHydrationWorker:
    """Independent worker for the durable media action plane."""

    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        lease_owner: str = "media_hydration_worker",
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        claim_limit: int = DEFAULT_CLAIM_LIMIT,
        max_claims_per_workspace: int = DEFAULT_MAX_CLAIMS_PER_WORKSPACE,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        pending_grace_seconds: float = DEFAULT_PENDING_GRACE_SECONDS,
        agent_turn_wakeup: Callable[[int, int], Awaitable[Any]] | None = None,
        media_event_append: Callable[[MediaHydrationStateChanged], Awaitable[Any]] | None = None,
    ) -> None:
        self._db_factory = db_factory
        self._lease_owner = lease_owner
        self._poll_interval_seconds = poll_interval_seconds
        self._claim_limit = claim_limit
        self._max_claims_per_workspace = max_claims_per_workspace
        self._lease_seconds = lease_seconds
        self._pending_grace_seconds = pending_grace_seconds
        self._agent_turn_wakeup = agent_turn_wakeup
        self._media_event_append = media_event_append
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._heartbeat_callback = callback

    async def start(self) -> None:
        self._stopping = False
        while not self._stopping:
            self._beat()
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("media_hydration_worker.run_failed")
                processed = 0

            if processed == 0:
                await asyncio.sleep(self._poll_interval_seconds)

    async def stop(self) -> None:
        self._stopping = True

    async def run_once(self, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(timezone.utc)
        async with self._db_factory() as session:
            jobs = await claim_due_media_hydration_jobs_for_all_workspaces(
                session,
                lease_owner=self._lease_owner,
                limit=self._claim_limit,
                max_claims_per_workspace=self._max_claims_per_workspace,
                lease_seconds=self._lease_seconds,
                pending_grace_seconds=self._pending_grace_seconds,
                now=current_time,
            )
            job_ids = [int(job.id) for job in jobs]
            started_events: list[MediaHydrationStateChanged] = []
            for job in jobs:
                event = await self._build_media_event(
                    session,
                    runtime=job,
                    event_type="media.hydration_started",
                    changed_at=current_time,
                )
                if event is not None:
                    started_events.append(event)
            await session.commit()
        for event in started_events:
            await self._append_media_event(event)

        processed = 0
        for job_id in job_ids:
            self._beat()
            result = None
            workspace_id: int | None = None
            completed_event: MediaHydrationStateChanged | None = None
            async with self._db_factory() as session:
                runtime = await session.get(MediaRuntime, job_id)
                if runtime is None:
                    continue
                workspace_id = int(runtime.workspace_id)
                result = await hydrate_media_runtime_job(
                    session,
                    workspace_id=workspace_id,
                    runtime=runtime,
                )
                completed_event = await self._build_media_event(
                    session,
                    runtime=runtime,
                    event_type=_event_type_for_runtime(runtime),
                )
                await session.commit()
                processed += 1
            if completed_event is not None:
                await self._append_media_event(completed_event)
            if (
                self._agent_turn_wakeup is not None
                and result is not None
                and result.should_wake_agent_turn
                and result.conversation_id is not None
                and workspace_id is not None
            ):
                await self._agent_turn_wakeup(
                    workspace_id,
                    int(result.conversation_id),
                )
        return processed

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()

    async def _append_media_event(self, event: MediaHydrationStateChanged) -> None:
        if self._media_event_append is None:
            return
        try:
            await self._media_event_append(event)
        except Exception:
            logger.exception(
                "media_hydration_worker.event_append_failed",
                extra={
                    "workspace_id": event.workspace_id,
                    "telegram_chat_id": event.telegram_chat_id,
                    "telegram_message_id": event.telegram_message_id,
                    "event_type": event.type,
                },
            )

    async def _build_media_event(
        self,
        session: Any,
        *,
        runtime: MediaRuntime,
        event_type: str,
        changed_at: datetime | None = None,
    ) -> MediaHydrationStateChanged | None:
        conversation = await session.get(Conversation, runtime.conversation_id)
        message = await session.get(Message, runtime.message_id)
        if conversation is None or message is None:
            return None

        telegram_chat_id = _coerce_int(
            conversation.telegram_chat_id or conversation.external_chat_id
        )
        telegram_message_id = _coerce_int(
            message.telegram_message_id or message.external_message_id
        )
        if telegram_chat_id is None or telegram_message_id is None:
            return None

        occurred_at = changed_at or runtime.updated_at or datetime.now(timezone.utc)
        occurred_ts = occurred_at.timestamp()
        event_type = _event_type_for_runtime(runtime) if event_type == "auto" else event_type
        return MediaHydrationStateChanged(
            type=event_type,
            workspace_id=int(runtime.workspace_id),
            channel=runtime.channel or "telegram_dm",
            channel_conversation_id=str(telegram_chat_id),
            channel_message_id=str(telegram_message_id),
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            hydration_status=runtime.hydration_status,
            asset_state=runtime.asset_state,
            semantic_state=runtime.semantic_state,
            action_state=runtime.action_state,
            ai_relevant=bool(runtime.ai_relevant),
            mime_type=runtime.mime_type,
            normalized_text=runtime.normalized_text,
            media_evidence=runtime.commercial_semantics,
            last_error=runtime.last_error,
            changed_at=occurred_ts,
            occurred_at=occurred_ts,
            emitted_at=datetime.now(timezone.utc).timestamp(),
            idempotency_key=(
                f"media:{runtime.workspace_id}:{runtime.id}:{event_type}:"
                f"{runtime.attempt_count}:{occurred_ts:.6f}"
            ),
        )


def _event_type_for_runtime(runtime: MediaRuntime) -> str:
    if runtime.action_state == "completed":
        return "media.hydration_completed"
    if runtime.action_state == "failed":
        return "media.hydration_failed"
    if runtime.action_state == "deferred":
        return "media.hydration_deferred"
    return "media.hydration_state_changed"


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
