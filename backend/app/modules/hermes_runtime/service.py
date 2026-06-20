from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hermes_run import HermesRun, HermesRunEvent
from app.modules.commercial_spine.contracts import utc_now
from app.modules.hermes_runtime.contracts import (
    HermesRunEventInput,
    HermesRunEventKind,
    HermesRunEventSnapshot,
    HermesRunInput,
    HermesRunPatch,
    HermesRunSnapshot,
    HermesRunState,
)


class HermesRunService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def start_or_dedupe(self, payload: HermesRunInput) -> HermesRunSnapshot:
        existing = await self.get_by_idempotency_key(payload.idempotency_key or "")
        if existing is not None:
            return existing.model_copy(update={"deduped": True})

        run = HermesRun(
            run_id=payload.run_id,
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            agent_id=payload.agent_id,
            agent_kind=payload.agent_kind,
            lane=str(payload.lane),
            run_mode=str(payload.run_mode),
            trigger_type=payload.trigger_type,
            trigger_id=payload.trigger_id,
            event_id=payload.event_id,
            conversation_id=payload.conversation_id,
            customer_id=payload.customer_id,
            runtime_profile_snapshot_id=payload.runtime_profile_snapshot_id,
            runtime_profile_cache_key=payload.runtime_profile_cache_key,
            engine_run_id=payload.engine_run_id,
            correlation_id=payload.correlation_id,
            idempotency_key=payload.idempotency_key or "",
            state=str(payload.state),
            source_refs=payload.source_refs,
            input_summary=payload.input_summary,
            details=payload.details,
            payload=payload.model_dump(mode="json"),
            created_at=payload.created_at,
            updated_at=payload.created_at,
        )
        self._db.add(run)
        try:
            await self._db.flush()
        except IntegrityError:
            await self._db.rollback()
            existing = await self.get_by_idempotency_key(payload.idempotency_key or "")
            if existing is None:
                raise
            return existing.model_copy(update={"deduped": True})

        await self.record_event(
            HermesRunEventInput(
                run_id=run.run_id,
                workspace_id=run.workspace_id,
                kind=HermesRunEventKind.CREATED,
                visibility="internal",
                correlation_id=run.correlation_id,
                idempotency_key=f"{run.idempotency_key}:created",
            ),
            hermes_run=run,
        )
        return _snapshot(run)

    async def mark_running(self, run_id: str, *, engine_run_id: str | None = None) -> HermesRunSnapshot:
        return await self.patch(
            run_id,
            HermesRunPatch(
                state=HermesRunState.RUNNING,
                engine_run_id=engine_run_id,
                started_at=utc_now(),
            ),
            event_kind=HermesRunEventKind.STARTED,
        )

    async def patch(
        self,
        run_id: str,
        patch: HermesRunPatch,
        *,
        event_kind: HermesRunEventKind | None = None,
    ) -> HermesRunSnapshot:
        run = await self._get_run(run_id)
        updates = patch.model_dump(exclude={"schema_version"}, exclude_none=True)
        details_update = updates.pop("details", None)
        for key, value in updates.items():
            setattr(run, key, str(value) if key == "state" else value)
        if details_update is not None:
            run.details = {**(run.details or {}), **details_update}
        run.updated_at = utc_now()
        await self._db.flush()
        if event_kind is not None:
            await self.record_event(
                HermesRunEventInput(
                    run_id=run.run_id,
                    workspace_id=run.workspace_id,
                    kind=event_kind,
                    visibility="internal",
                    payload=patch.model_dump(mode="json", exclude_none=True),
                    correlation_id=run.correlation_id,
                    idempotency_key=f"{run.idempotency_key}:{event_kind}:{run.updated_at.isoformat()}",
                ),
                hermes_run=run,
            )
        return _snapshot(run)

    async def complete(self, run_id: str, patch: HermesRunPatch | None = None) -> HermesRunSnapshot:
        merged = patch or HermesRunPatch()
        merged = merged.model_copy(
            update={
                "state": HermesRunState.COMPLETED,
                "completed_at": merged.completed_at or utc_now(),
            }
        )
        return await self.patch(run_id, merged, event_kind=HermesRunEventKind.COMPLETED)

    async def fail(
        self,
        run_id: str,
        *,
        error_code: str,
        error_message: str,
        details: dict[str, Any] | None = None,
    ) -> HermesRunSnapshot:
        return await self.patch(
            run_id,
            HermesRunPatch(
                state=HermesRunState.FAILED,
                completed_at=utc_now(),
                error_code=error_code,
                error_message=error_message,
                details=details or {},
            ),
            event_kind=HermesRunEventKind.FAILED,
        )

    async def reclaim_stale_running_runs(
        self,
        *,
        ttl_seconds: int,
        limit: int,
    ) -> int:
        """Fail HermesRun rows stuck in 'running' past a TTL.

        Mirror of ``reclaim_stale_turn_leases``: a turn can be aborted between
        ``mark_running`` and ``complete`` (e.g. a post-send delivery error
        propagates out of ``dispatch_agent_turn``, #418). Without this janitor
        the central execution record lies as 'running' forever. ``skip_locked``
        keeps concurrent runners from fighting over the same rows.
        """
        cutoff = utc_now() - timedelta(seconds=max(1, int(ttl_seconds or 1)))
        rows = list(
            (
                await self._db.execute(
                    select(HermesRun)
                    .where(
                        HermesRun.state == str(HermesRunState.RUNNING),
                        HermesRun.updated_at <= cutoff,
                    )
                    .order_by(HermesRun.updated_at.asc(), HermesRun.id.asc())
                    .limit(max(1, int(limit or 1)))
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        now = utc_now()
        for run in rows:
            run.state = str(HermesRunState.FAILED)
            run.error_code = "stale_running_reclaimed"
            run.error_message = (
                f"HermesRun exceeded the {int(ttl_seconds)}s running TTL; "
                "reclaimed by the stale-run janitor."
            )
            run.completed_at = now
            run.updated_at = now
            run.details = {
                **(run.details or {}),
                "reclaimed": {
                    "schema_version": "hermes_run_reclaim.v1",
                    "reason": "stale_running_reclaimed",
                    "ttl_seconds": int(ttl_seconds),
                    "reclaimed_at": now.isoformat(),
                },
            }
            await self.record_event(
                HermesRunEventInput(
                    run_id=run.run_id,
                    workspace_id=run.workspace_id,
                    kind=HermesRunEventKind.FAILED,
                    visibility="internal",
                    payload={
                        "reason": "stale_running_reclaimed",
                        "ttl_seconds": int(ttl_seconds),
                    },
                    correlation_id=run.correlation_id,
                    idempotency_key=f"{run.idempotency_key}:reclaimed:{now.isoformat()}",
                ),
                hermes_run=run,
            )
        await self._db.flush()
        return len(rows)

    async def record_event(
        self,
        payload: HermesRunEventInput,
        *,
        hermes_run: HermesRun | None = None,
    ) -> HermesRunEventSnapshot:
        existing = await self._event_by_event_id(
            workspace_id=payload.workspace_id,
            event_id=payload.event_id or "",
        )
        if existing is not None:
            return _event_snapshot(existing)

        run = hermes_run or await self._get_run(payload.run_id)
        sequence = payload.sequence or await self._next_sequence(run.id)
        event = HermesRunEvent(
            hermes_run_id=run.id,
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            event_id=payload.event_id or "",
            sequence=sequence,
            kind=str(payload.kind),
            visibility=payload.visibility,
            owner_label=payload.owner_label,
            owner_detail=payload.owner_detail,
            tool_name=payload.tool_name,
            tool_state=payload.tool_state,
            action_proposal_id=payload.action_proposal_id,
            correlation_id=payload.correlation_id,
            idempotency_key=payload.idempotency_key or "",
            payload=payload.payload,
            created_at=payload.created_at,
            occurred_at=payload.created_at,
        )
        self._db.add(event)
        await self._db.flush()
        return _event_snapshot(event)

    async def get_by_idempotency_key(self, idempotency_key: str) -> HermesRunSnapshot | None:
        if not idempotency_key:
            return None
        result = await self._db.execute(select(HermesRun).where(HermesRun.idempotency_key == idempotency_key))
        run = result.scalar_one_or_none()
        return _snapshot(run) if run is not None else None

    async def get_by_run_id(self, run_id: str) -> HermesRunSnapshot | None:
        if not run_id:
            return None
        result = await self._db.execute(select(HermesRun).where(HermesRun.run_id == run_id))
        run = result.scalar_one_or_none()
        return _snapshot(run) if run is not None else None

    async def get_by_output_ref(self, output_ref: str) -> HermesRunSnapshot | None:
        if not output_ref:
            return None
        result = await self._db.execute(select(HermesRun).where(HermesRun.output_ref == output_ref))
        run = result.scalar_one_or_none()
        return _snapshot(run) if run is not None else None

    async def latest_for_workspace_agent(
        self,
        *,
        workspace_id: int,
        agent_id: int | None = None,
        limit: int = 5,
    ) -> list[HermesRunSnapshot]:
        query: Select[tuple[HermesRun]] = select(HermesRun).where(HermesRun.workspace_id == workspace_id)
        if agent_id is not None:
            query = query.where(HermesRun.agent_id == agent_id)
        query = query.order_by(HermesRun.created_at.desc()).limit(max(1, min(limit, 50)))
        result = await self._db.execute(query)
        return [_snapshot(run) for run in result.scalars().all()]

    async def latest_for_conversation(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
        limit: int = 5,
    ) -> list[HermesRunSnapshot]:
        query = (
            select(HermesRun)
            .where(
                HermesRun.workspace_id == workspace_id,
                HermesRun.conversation_id == conversation_id,
            )
            .order_by(HermesRun.created_at.desc(), HermesRun.id.desc())
            .limit(max(1, min(limit, 50)))
        )
        result = await self._db.execute(query)
        return [_snapshot(run) for run in result.scalars().all()]

    async def latest_for_event(
        self,
        *,
        workspace_id: int,
        event_id: str,
        limit: int = 5,
    ) -> list[HermesRunSnapshot]:
        query = (
            select(HermesRun)
            .where(
                HermesRun.workspace_id == workspace_id,
                HermesRun.event_id == event_id,
            )
            .order_by(HermesRun.created_at.desc(), HermesRun.id.desc())
            .limit(max(1, min(limit, 50)))
        )
        result = await self._db.execute(query)
        return [_snapshot(run) for run in result.scalars().all()]

    async def events_for_run(self, run_id: str) -> list[HermesRunEventSnapshot]:
        result = await self._db.execute(
            select(HermesRunEvent)
            .where(HermesRunEvent.run_id == run_id)
            .order_by(HermesRunEvent.sequence.asc(), HermesRunEvent.created_at.asc())
        )
        return [_event_snapshot(event) for event in result.scalars().all()]

    async def _get_run(self, run_id: str) -> HermesRun:
        result = await self._db.execute(select(HermesRun).where(HermesRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            raise ValueError("hermes_run_not_found")
        return run

    async def _event_by_event_id(self, *, workspace_id: int, event_id: str) -> HermesRunEvent | None:
        if not event_id:
            return None
        result = await self._db.execute(
            select(HermesRunEvent).where(
                HermesRunEvent.workspace_id == workspace_id,
                HermesRunEvent.event_id == event_id,
            )
        )
        return result.scalar_one_or_none()

    async def _next_sequence(self, hermes_run_id: int) -> int:
        result = await self._db.execute(
            select(func.max(HermesRunEvent.sequence)).where(HermesRunEvent.hermes_run_id == hermes_run_id)
        )
        current = result.scalar_one_or_none()
        return int(current or 0) + 1


def _snapshot(run: HermesRun) -> HermesRunSnapshot:
    return HermesRunSnapshot(
        id=run.id,
        run_id=run.run_id,
        workspace_id=run.workspace_id,
        tenant_id=run.tenant_id,
        agent_id=run.agent_id,
        agent_kind=run.agent_kind,
        lane=run.lane,
        run_mode=run.run_mode,
        trigger_type=run.trigger_type,
        trigger_id=run.trigger_id,
        event_id=run.event_id,
        conversation_id=run.conversation_id,
        customer_id=run.customer_id,
        runtime_profile_snapshot_id=run.runtime_profile_snapshot_id,
        runtime_profile_cache_key=run.runtime_profile_cache_key,
        engine_run_id=run.engine_run_id,
        correlation_id=run.correlation_id,
        idempotency_key=run.idempotency_key,
        state=run.state,
        source_refs=run.source_refs or [],
        input_summary=run.input_summary or "",
        started_at=run.started_at,
        completed_at=run.completed_at,
        total_latency_ms=run.total_latency_ms,
        llm_latency_ms=run.llm_latency_ms,
        llm_calls=run.llm_calls or 0,
        tokens_in=run.tokens_in or 0,
        tokens_out=run.tokens_out or 0,
        total_tokens=run.total_tokens or 0,
        confidence=run.confidence,
        warnings_count=run.warnings_count or 0,
        tool_errors_count=run.tool_errors_count or 0,
        output_action=run.output_action,
        output_ref=run.output_ref,
        error_code=run.error_code,
        error_message=run.error_message,
        details=run.details or {},
        created_at=run.created_at.astimezone(UTC),
        updated_at=run.updated_at.astimezone(UTC),
    )


def _event_snapshot(event: HermesRunEvent) -> HermesRunEventSnapshot:
    return HermesRunEventSnapshot(
        id=event.id,
        hermes_run_id=event.hermes_run_id,
        event_id=event.event_id,
        run_id=event.run_id,
        workspace_id=event.workspace_id,
        sequence=event.sequence,
        kind=event.kind,
        visibility=event.visibility,
        owner_label=event.owner_label,
        owner_detail=event.owner_detail,
        tool_name=event.tool_name,
        tool_state=event.tool_state,
        action_proposal_id=event.action_proposal_id,
        payload=event.payload or {},
        correlation_id=event.correlation_id,
        idempotency_key=event.idempotency_key,
        created_at=event.created_at.astimezone(UTC),
    )
