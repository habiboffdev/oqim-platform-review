from __future__ import annotations

from datetime import UTC

from app.modules.agent_runtime_events.contracts import (
    AgentRun,
    AgentRunEvent,
    AgentRunEventInput,
    AgentRunFeed,
    AgentRunInput,
    AgentRunState,
    AgentRunTimeline,
)
from app.modules.commercial_spine.contracts import (
    BusinessBrainProjection,
    CommercialEvent,
    utc_now,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository

AGENT_RUN_PROJECTION = "agent_run"
AGENT_RUN_EVENT_PROJECTION = "agent_run_event"


class AgentRuntimeEventService:
    def __init__(self, repository: CommercialSpineRepository) -> None:
        self._repository = repository

    async def start_run(self, payload: AgentRunInput) -> AgentRun:
        existing = await self._repository.get_projection(
            workspace_id=payload.workspace_id,
            projection_ref=_run_ref(payload.run_id),
        )
        if existing is not None:
            return AgentRun.model_validate(existing.state)

        run = AgentRun(
            **payload.model_dump(exclude={"schema_version"}),
            completed_at=None,
        )
        await self._repository.upsert_projection(_run_projection(run))
        await self._repository.append_event(
            CommercialEvent(
                event_id=f"event:{run.run_id}:started",
                workspace_id=run.workspace_id,
                source_type="agent_runtime",
                source_ref=f"agent_run:{run.run_id}",
                actor_type="agent",
                correlation_id=run.correlation_id,
                idempotency_key=f"{run.idempotency_key}:agent_run_started",
                occurred_at=run.started_at,
                payload={
                    "run_id": run.run_id,
                    "agent_id": run.agent_id,
                    "agent_kind": run.agent_kind,
                    "state": run.state,
                    "visibility": "internal",
                },
            )
        )
        return run

    async def transition_run(
        self,
        *,
        workspace_id: int,
        run_id: str,
        state: AgentRunState,
        correlation_id: str,
    ) -> AgentRun:
        projection = await self._repository.get_projection(
            workspace_id=workspace_id,
            projection_ref=_run_ref(run_id),
        )
        if projection is None:
            raise ValueError("agent_run_not_found")
        run = AgentRun.model_validate(projection.state)
        completed_at = utc_now() if state in {"completed", "failed", "cancelled"} else None
        updated = run.model_copy(
            update={
                "state": state,
                "completed_at": completed_at or run.completed_at,
                "correlation_id": correlation_id,
            }
        )
        await self._repository.upsert_projection(_run_projection(updated))
        return updated

    async def record_event(self, payload: AgentRunEventInput) -> AgentRunEvent:
        existing = await self._repository.get_projection(
            workspace_id=payload.workspace_id,
            projection_ref=_event_ref(payload.event_id),
        )
        if existing is not None:
            return AgentRunEvent.model_validate(existing.state)

        sequence = payload.sequence or await self._next_sequence(
            workspace_id=payload.workspace_id,
            run_id=payload.run_id,
        )
        event = AgentRunEvent(
            **payload.model_dump(exclude={"schema_version", "sequence"}),
            sequence=sequence,
        )
        await self._repository.upsert_projection(_event_projection(event))
        await self._repository.append_event(
            CommercialEvent(
                event_id=f"event:{event.event_id}",
                workspace_id=event.workspace_id,
                source_type="agent_runtime",
                source_ref=f"agent_run:{event.run_id}",
                actor_type="agent",
                correlation_id=event.correlation_id,
                idempotency_key=event.idempotency_key,
                occurred_at=event.created_at,
                payload={
                    "run_id": event.run_id,
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "visibility": event.visibility,
                    "owner_label": event.owner_label,
                    "tool_name": event.tool_name,
                    "tool_state": event.tool_state,
                    "action_proposal_id": event.action_proposal_id,
                },
            )
        )
        return event

    async def timeline(self, *, workspace_id: int, run_id: str) -> AgentRunTimeline:
        run_projection = await self._repository.get_projection(
            workspace_id=workspace_id,
            projection_ref=_run_ref(run_id),
        )
        event_projections = await self._repository.list_projections(
            workspace_id=workspace_id,
            entity_ref=f"agent_run:{run_id}",
            projection_type=AGENT_RUN_EVENT_PROJECTION,
            limit=250,
        )
        events = [
            AgentRunEvent.model_validate(projection.state)
            for projection in event_projections
        ]
        events.sort(key=lambda item: (item.sequence, item.created_at.astimezone(UTC)))
        return AgentRunTimeline(
            workspace_id=workspace_id,
            run_id=run_id,
            run=AgentRun.model_validate(run_projection.state)
            if run_projection is not None
            else None,
            events=events,
        )

    async def timeline_for_proposal(
        self,
        *,
        workspace_id: int,
        proposal_id: str,
    ) -> AgentRunTimeline | None:
        event_projections = await self._repository.list_projections(
            workspace_id=workspace_id,
            projection_type=AGENT_RUN_EVENT_PROJECTION,
            limit=250,
        )
        matching_events = [
            AgentRunEvent.model_validate(projection.state)
            for projection in event_projections
            if projection.state.get("action_proposal_id") == proposal_id
        ]
        if not matching_events:
            return None
        matching_events.sort(key=lambda item: (item.sequence, item.created_at.astimezone(UTC)))
        return await self.timeline(
            workspace_id=workspace_id,
            run_id=matching_events[0].run_id,
        )

    async def recent_timelines(
        self,
        *,
        workspace_id: int,
        limit: int = 5,
    ) -> AgentRunFeed:
        bounded_limit = max(1, min(int(limit), 20))
        run_projections = await self._repository.list_projections(
            workspace_id=workspace_id,
            projection_type=AGENT_RUN_PROJECTION,
            limit=250,
        )
        runs = [AgentRun.model_validate(projection.state) for projection in run_projections]
        runs.sort(key=lambda item: item.started_at.astimezone(UTC), reverse=True)
        timelines = [
            await self.timeline(workspace_id=workspace_id, run_id=run.run_id)
            for run in runs[:bounded_limit]
        ]
        return AgentRunFeed(workspace_id=workspace_id, timelines=timelines)

    async def _next_sequence(self, *, workspace_id: int, run_id: str) -> int:
        timeline = await self.timeline(workspace_id=workspace_id, run_id=run_id)
        if not timeline.events:
            return 1
        return max(event.sequence for event in timeline.events) + 1


def _run_ref(run_id: str) -> str:
    return f"agent_run:{run_id}"


def _event_ref(event_id: str) -> str:
    return f"agent_run_event:{event_id}"


def _run_projection(run: AgentRun) -> BusinessBrainProjection:
    return BusinessBrainProjection(
        projection_ref=_run_ref(run.run_id),
        workspace_id=run.workspace_id,
        projection_type=AGENT_RUN_PROJECTION,
        entity_ref=f"agent:{run.agent_id}",
        state=run.model_dump(mode="json"),
        source_refs=run.source_refs or [f"trigger:{run.trigger_ref}"],
    )


def _event_projection(event: AgentRunEvent) -> BusinessBrainProjection:
    source_refs = event.source_refs or [f"agent_run:{event.run_id}"]
    return BusinessBrainProjection(
        projection_ref=_event_ref(event.event_id),
        workspace_id=event.workspace_id,
        projection_type=AGENT_RUN_EVENT_PROJECTION,
        entity_ref=f"agent_run:{event.run_id}",
        state=event.model_dump(mode="json"),
        source_refs=source_refs,
    )
