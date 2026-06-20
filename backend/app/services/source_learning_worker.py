from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.consumer_names import make_consumer_name
from app.core.logging import get_logger
from app.db.session import async_session
from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.source_runtime import OnboardingSourceLearningRuntimeService
from app.services.worker_lease import WorkerLease

logger = get_logger("services.source_learning_worker")

DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_BATCH_SIZE = 8
DEFAULT_LEASE_SECONDS = 5 * 60
SOURCE_LEARNING_PROJECTION_TYPE = "business_source_learning"
SOURCE_LEARNING_DUE_STATUSES = {"queued", "retrying"}
SOURCE_LEARNING_STALE_STATUSES = {"learning"}


@dataclass(frozen=True, slots=True)
class SourceLearningClaim:
    workspace_id: int
    source_refs: tuple[str, ...]


class SourceLearningWorker:
    """Supervised worker for continuous Business Brain source learning."""

    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        redis: Any | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_attempts: int = 3,
        max_parallelism: int | None = None,
    ) -> None:
        self._db_factory = db_factory
        self._redis = redis
        self._poll_interval_seconds = max(float(poll_interval_seconds), 0.1)
        self._batch_size = max(1, int(batch_size))
        self._lease_seconds = max(30, int(lease_seconds))
        self._max_attempts = max(1, int(max_attempts))
        settings = get_settings()
        self._max_parallelism = max(
            1,
            min(int(max_parallelism or settings.onboarding_source_learning_concurrency), 12),
        )
        self._consumer_name = make_consumer_name("source_learning")
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None
        self._lease = (
            WorkerLease(redis, role="source_learning", ttl_seconds=30)
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
                logger.exception("source_learning_worker.tick_failed")
                await asyncio.sleep(min(self._poll_interval_seconds * 2, 10.0))
        if has_lease and self._lease is not None:
            await self._lease.release()

    async def stop(self) -> None:
        self._stopping = True

    async def run_once(self, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(UTC)
        async with self._db_factory() as session:
            claims = await claim_due_source_learning_jobs(
                session,
                lease_owner=self._consumer_name,
                limit=self._batch_size,
                lease_seconds=self._lease_seconds,
                now=current_time,
            )
            await session.commit()

        processed = 0
        for claim in claims:
            self._beat()
            async with self._db_factory() as session:
                result = await OnboardingSourceLearningRuntimeService(
                    repository=CommercialSpineRepository(session),
                    session_factory=async_session,
                    max_parallelism=self._max_parallelism,
                ).process_workspace_sources(
                    workspace_id=claim.workspace_id,
                    correlation_id=f"source-learning-worker:{claim.workspace_id}",
                    limit=len(claim.source_refs),
                    max_attempts=self._max_attempts,
                    source_refs=set(claim.source_refs),
                )
                await session.commit()
                processed += len(result.items)
        return processed

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()


async def claim_due_source_learning_jobs(
    session: AsyncSession,
    *,
    lease_owner: str,
    limit: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> list[SourceLearningClaim]:
    if limit <= 0:
        return []
    current_time = now or datetime.now(UTC)
    rows = list(
        (
            await session.scalars(
                select(BusinessBrainProjectionRecord)
                .where(BusinessBrainProjectionRecord.projection_type == SOURCE_LEARNING_PROJECTION_TYPE)
                .order_by(BusinessBrainProjectionRecord.updated_at.asc(), BusinessBrainProjectionRecord.id.asc())
                .limit(max(limit * 5, limit))
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    grouped: dict[int, list[str]] = defaultdict(list)
    claimed_count = 0
    leased_until = current_time + timedelta(seconds=max(30, int(lease_seconds)))
    for row in rows:
        if claimed_count >= limit:
            break
        state = dict(row.state or {})
        source_ref = str(state.get("source_ref") or "").strip()
        if not source_ref or not _source_learning_due(state, current_time):
            continue
        state["lease_owner"] = lease_owner
        state["leased_until"] = leased_until.isoformat()
        state["claimed_at"] = current_time.isoformat()
        row.state = state
        raw_projection = dict(row.raw_projection or {})
        raw_projection["state"] = state
        row.raw_projection = raw_projection
        session.add(row)
        grouped[int(row.workspace_id)].append(source_ref)
        claimed_count += 1
    await session.flush()
    return [
        SourceLearningClaim(workspace_id=workspace_id, source_refs=tuple(source_refs))
        for workspace_id, source_refs in grouped.items()
    ]


def _source_learning_due(state: dict[str, Any], now: datetime) -> bool:
    status = str(state.get("status") or "").strip().lower()
    if status not in SOURCE_LEARNING_DUE_STATUSES | SOURCE_LEARNING_STALE_STATUSES:
        return False
    next_attempt_at = _coerce_datetime(state.get("next_attempt_at"))
    if next_attempt_at is not None and next_attempt_at > now:
        return False
    leased_until = _coerce_datetime(state.get("leased_until"))
    if leased_until is not None and leased_until > now:
        return False
    if status in SOURCE_LEARNING_STALE_STATUSES:
        return True
    return status in SOURCE_LEARNING_DUE_STATUSES


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
