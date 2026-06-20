from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from app.models.commercial_action import CommercialActionProposalRecord
from app.modules.action_runtime.service import ActionRuntimeService
from app.modules.commercial_spine.contracts import CommercialActionProposal
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.services.delivery import DeliveryService
from app.services.worker_lease import WorkerLease

logger = logging.getLogger("oqim_business.action_runtime.worker")


class ActionRuntimeWorker:
    """Process commercial action proposals through policy and executors."""

    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        delivery: DeliveryService,
        redis: Any | None = None,
        poll_interval_seconds: float = 2.0,
        batch_size: int = 10,
    ) -> None:
        self._db_factory = db_factory
        self._delivery = delivery
        self._redis = redis
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = max(1, int(batch_size))
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None
        self._lease = (
            WorkerLease(redis, role="action_runtime_worker", ttl_seconds=30)
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
                await self.run_once()
                self._beat()
                await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                raise
            except Exception:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                has_lease = False
                logger.exception("action_runtime.worker_tick_failed")
                await asyncio.sleep(min(self._poll_interval_seconds * 2, 10.0))
        if has_lease and self._lease is not None:
            await self._lease.release()

    async def stop(self) -> None:
        self._stopping = True

    async def run_once(self, *, limit: int | None = None) -> int:
        async with self._db_factory() as session:
            service = ActionRuntimeService(
                CommercialSpineRepository(session),
                delivery=self._delivery,
            )
            rows = (
                await session.execute(
                    select(CommercialActionProposalRecord)
                    .where(
                        CommercialActionProposalRecord.lifecycle_state.in_(
                            ("proposed", "approved")
                        ),
                        ~(
                            (
                                CommercialActionProposalRecord.executor_runtime
                                == "trigger_runtime"
                            )
                            & CommercialActionProposalRecord.action_type.like("hermes.%")
                        ),
                    )
                    .order_by(
                        CommercialActionProposalRecord.created_at.asc(),
                        CommercialActionProposalRecord.id.asc(),
                    )
                    .limit(limit or self._batch_size)
                )
            ).scalars()
            proposals = (
                CommercialActionProposal.model_validate(row.raw_proposal)
                for row in rows
            )
            processed = 0
            for proposal in proposals:
                await service.process_proposal(
                    workspace_id=proposal.workspace_id,
                    proposal_id=proposal.proposal_id,
                    correlation_id=(
                        proposal.correlation_id
                        or f"action-runtime-worker:{proposal.proposal_id}"
                    ),
                )
                processed += 1
            await session.commit()
            return processed

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()
