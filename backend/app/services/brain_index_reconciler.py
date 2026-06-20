"""Self-healing brain index reconciler.

Drains business_brain_facts.index_state='pending' (set at the write chokepoint and
on supersede/in-place edit): embeds searchable facts that are currently visible,
prunes index records for facts that are no longer visible. Registered under
ConsumerSupervisor. See docs/superpowers/specs/2026-05-25-automatic-fact-indexing-design.md."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.commercial_spine import BusinessBrainFactRecord, BusinessBrainIndexRecord
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.commercial_spine.repository import CommercialSpineRepository

logger = logging.getLogger("oqim_business.brain_index_reconciler")

# Statuses the reconciler embeds. `proposed` is included deliberately: the spec
# deems indexing proposed facts safe because retrieval only returns them when
# include_proposed=True, so an index record for a proposed fact is never surfaced
# by default. Non-visible statuses (superseded/historical/etc.) fall through to the
# prune branch instead.
_VISIBLE_STATUSES = ("active", "confirmed", "proposed")


class BrainIndexReconciler:
    def __init__(
        self,
        *,
        db_factory: Callable[[], AsyncSession] | None,
        batch_size: int = 50,
        interval_seconds: float = 5.0,
    ) -> None:
        self._db_factory = db_factory
        self._batch_size = batch_size
        self._interval = interval_seconds
        self._stopping = False
        self._beat: Callable[[], None] = lambda: None

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._beat = callback or (lambda: None)

    async def start(self) -> None:
        assert self._db_factory is not None, "db_factory required to run the loop"
        self._stopping = False
        while not self._stopping:
            try:
                async with self._db_factory() as session:
                    processed = await self._reconcile_once(session)
                    # Commit lives here (not in _reconcile_once): on a commit/embed
                    # failure the generic except below opens a fresh session next tick,
                    # so the uncommitted rows revert to 'pending' and are retried.
                    await session.commit()
                self._beat()
                if processed:
                    logger.info("brain_index_reconciler.drained", extra={"processed": processed})
                if processed == 0:
                    await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("brain_index_reconciler.tick_failed", exc_info=exc)
                await asyncio.sleep(2.0)

    async def stop(self) -> None:
        self._stopping = True

    async def _reconcile_once(self, session: AsyncSession) -> int:
        rows = (
            await session.execute(
                select(BusinessBrainFactRecord)
                .where(BusinessBrainFactRecord.index_state == "pending")
                .order_by(BusinessBrainFactRecord.id.asc())
                .limit(self._batch_size)
            )
        ).scalars().all()
        if not rows:
            return 0

        memory = BusinessBrainMemoryService(repository=CommercialSpineRepository(session))
        now = utc_now()
        visible_by_workspace: dict[int, list[str]] = {}
        for row in rows:
            if row.status in _VISIBLE_STATUSES:
                visible_by_workspace.setdefault(row.workspace_id, []).append(row.fact_id)

        failed: set[tuple[int, str]] = set()
        for workspace_id, fact_ids in visible_by_workspace.items():
            try:
                result = await memory.index_structured_facts_for_search(
                    workspace_id=workspace_id, fact_ids=fact_ids
                )
            except Exception as exc:
                logger.warning(
                    "brain_index_reconciler.embed_failed",
                    extra={"workspace_id": workspace_id, "fact_ids": fact_ids},
                    exc_info=exc,
                )
                failed.update((workspace_id, fid) for fid in fact_ids)
                continue
            # A unit whose embedding came back non-"ready" (e.g. a transient provider
            # outage degraded it) is not actually searchable. Mark that fact failed so
            # it is not falsely recorded as indexed; persist_index_record upserts, so a
            # later write replaces the degraded record. Failed facts stay out of
            # the pending set to avoid spinning during a sustained outage.
            degraded_fact_ids = {
                unit.fact_id
                for unit in result.source_units
                if unit.embedding_state != "ready"
            }
            failed.update((workspace_id, fid) for fid in degraded_fact_ids)

        for row in rows:
            if (row.workspace_id, row.fact_id) in failed:
                row.index_state = "failed"
                continue
            if row.status not in _VISIBLE_STATUSES:
                await session.execute(
                    delete(BusinessBrainIndexRecord).where(
                        BusinessBrainIndexRecord.workspace_id == row.workspace_id,
                        BusinessBrainIndexRecord.fact_id == row.fact_id,
                    )
                )
            # "indexed" here means "reconciled" — the row left the pending queue,
            # whether we just embedded it (visible) or pruned its records (invisible).
            # We keep the 4-state machine (skipped/pending/indexed/failed) per spec.
            row.index_state = "indexed"
            row.indexed_at = now
        return len(rows)
