from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.trigger import Trigger
from app.modules.hermes_runtime.contracts import (
    HermesRunInput,
    HermesRunLane,
    HermesRunMode,
    HermesRunSnapshot,
)
from app.modules.hermes_runtime.service import HermesRunService
from app.services.worker_lease import WorkerLease

WORKER_LEASE_ROLE = "trigger_run_router"
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_BATCH_SIZE = 25
HERMES_ACTION_PREFIX = "hermes."


class TriggerRunRouter:
    """Route approved generic trigger proposals into durable HermesRun rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._runs = HermesRunService(session)

    async def route_pending(self, *, limit: int = DEFAULT_BATCH_SIZE) -> list[HermesRunSnapshot]:
        rows = (
            await self._session.scalars(
                select(CommercialActionProposalRecord)
                .where(
                    CommercialActionProposalRecord.executor_runtime == "trigger_runtime",
                    CommercialActionProposalRecord.action_type.like(
                        f"{HERMES_ACTION_PREFIX}%"
                    ),
                    CommercialActionProposalRecord.lifecycle_state == "approved",
                )
                .order_by(
                    CommercialActionProposalRecord.created_at.asc(),
                    CommercialActionProposalRecord.id.asc(),
                )
                .limit(max(1, int(limit)))
            )
        ).all()
        routed: list[HermesRunSnapshot] = []
        for proposal in rows:
            run = await self.route_proposal(proposal)
            if run is not None:
                routed.append(run)
        return routed

    async def route_proposal(
        self,
        proposal: CommercialActionProposalRecord,
    ) -> HermesRunSnapshot | None:
        if not _is_hermes_trigger_proposal(proposal):
            return None

        existing_run_id = _existing_run_id(proposal)
        if existing_run_id and proposal.lifecycle_state == "executed":
            return await self._runs.get_by_run_id(existing_run_id)

        trigger = await _load_trigger(
            self._session,
            workspace_id=proposal.workspace_id,
            source_refs=list(proposal.source_refs or []),
        )
        if trigger is None:
            proposal.lifecycle_state = "blocked"
            proposal.reason_code = "trigger_missing"
            proposal.payload = {
                **_proposal_payload(proposal),
                "trigger_run_router": {"state": "blocked", "reason_code": "trigger_missing"},
            }
            await self._session.flush()
            return None

        agent = await _load_agent(
            self._session,
            workspace_id=proposal.workspace_id,
            agent_id=trigger.owner_agent_id,
        )
        event_payload = _proposal_payload(proposal)
        run_input = _hermes_run_input(
            proposal=proposal,
            trigger=trigger,
            agent=agent,
            event_payload=event_payload,
        )
        run = await self._runs.start_or_dedupe(run_input)
        proposal.lifecycle_state = "executed"
        proposal.reason_code = "hermes_run_queued"
        proposal.payload = {
            **event_payload,
            "hermes_run_id": run.run_id,
            "hermes_run_deduped": run.deduped,
        }
        proposal.raw_proposal = {
            **(proposal.raw_proposal or {}),
            "lifecycle_state": proposal.lifecycle_state,
            "reason_code": proposal.reason_code,
            "payload": proposal.payload,
        }
        await self._session.flush()
        return run


class TriggerRunRouterWorker:
    """Supervised worker for generic TriggerDefinition -> HermesRun routing."""

    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        redis: Any | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._db_factory = db_factory
        self._redis = redis
        self._poll_interval_seconds = max(float(poll_interval_seconds), 0.1)
        self._batch_size = max(1, int(batch_size))
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None
        self._lease = (
            WorkerLease(redis, role=WORKER_LEASE_ROLE, ttl_seconds=30)
            if redis is not None
            else None
        )

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._heartbeat_callback = callback

    async def start(self) -> None:
        self._stopping = False
        has_lease = False
        while not self._stopping:
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
        if has_lease and self._lease is not None:
            await self._lease.release()

    async def stop(self) -> None:
        self._stopping = True

    async def run_once(self, *, limit: int | None = None) -> int:
        async with self._db_factory() as session:
            routed = await TriggerRunRouter(session).route_pending(
                limit=limit or self._batch_size
            )
            await session.commit()
            return len(routed)

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()


def _is_hermes_trigger_proposal(proposal: CommercialActionProposalRecord) -> bool:
    return (
        proposal.executor_runtime == "trigger_runtime"
        and str(proposal.action_type).startswith(HERMES_ACTION_PREFIX)
    )


def _existing_run_id(proposal: CommercialActionProposalRecord) -> str | None:
    payload = _proposal_payload(proposal)
    run_id = payload.get("hermes_run_id")
    return str(run_id) if isinstance(run_id, str) and run_id.strip() else None


async def _load_trigger(
    session: AsyncSession,
    *,
    workspace_id: int,
    source_refs: list[str],
) -> Trigger | None:
    trigger_id = _trigger_id_from_refs(source_refs)
    if trigger_id is None:
        return None
    return await session.scalar(
        select(Trigger).where(
            Trigger.workspace_id == workspace_id,
            Trigger.id == trigger_id,
        )
    )


async def _load_agent(
    session: AsyncSession,
    *,
    workspace_id: int,
    agent_id: int,
) -> Agent | None:
    return await session.scalar(
        select(Agent).where(
            Agent.workspace_id == workspace_id,
            Agent.id == agent_id,
        )
    )


def _hermes_run_input(
    *,
    proposal: CommercialActionProposalRecord,
    trigger: Trigger,
    agent: Agent | None,
    event_payload: dict[str, Any],
) -> HermesRunInput:
    phase3 = _phase3_scope(trigger)
    return HermesRunInput(
        workspace_id=proposal.workspace_id,
        tenant_id=proposal.workspace_id,
        agent_id=trigger.owner_agent_id,
        agent_kind=(agent.agent_type if agent is not None else "agent") or "agent",
        lane=_lane_from_phase3(phase3),
        run_mode=_run_mode_from_action_type(proposal.action_type),
        trigger_type="generic_trigger",
        trigger_id=proposal.proposal_id,
        event_id=_event_id(event_payload),
        conversation_id=_positive_id(proposal.conversation_id),
        customer_id=_positive_id(proposal.customer_id),
        correlation_id=proposal.correlation_id or proposal.proposal_id,
        source_refs=_unique(
            [
                *[str(ref) for ref in proposal.source_refs or []],
                f"action_proposal:{proposal.proposal_id}",
                f"trigger:{trigger.id}",
            ]
        ),
        input_summary=f"{proposal.action_type} from {trigger.event_source}",
        details={
            "trigger": {
                "trigger_id": trigger.id,
                "event_source": trigger.event_source,
                "permission_mode": trigger.permission_mode,
                "phase3": phase3,
            },
            "event_payload": event_payload,
            "action_proposal": {
                "proposal_id": proposal.proposal_id,
                "action_type": proposal.action_type,
                "execution_mode": proposal.execution_mode,
                "priority": proposal.priority,
            },
            "trigger_run_mode": _trigger_run_mode(proposal.action_type),
        },
    )


def _run_mode_from_action_type(action_type: str) -> HermesRunMode:
    mode = _trigger_run_mode(action_type)
    mapping = {
        "reply": HermesRunMode.REPLY,
        "draft": HermesRunMode.REPLY,
        "silent": HermesRunMode.REPLY,
        "owner_only": HermesRunMode.PERSONAL,
        "broadcast": HermesRunMode.BROADCAST,
        "scanner": HermesRunMode.SCAN,
        "scan": HermesRunMode.SCAN,
        "learning": HermesRunMode.LEARNING,
        "enterprise_qa": HermesRunMode.ENTERPRISE_QA,
    }
    return mapping.get(mode, HermesRunMode.REPLY)


def _trigger_run_mode(action_type: str) -> str:
    value = str(action_type)
    if not value.startswith(HERMES_ACTION_PREFIX):
        return "reply"
    return value.removeprefix(HERMES_ACTION_PREFIX)


def _lane_from_phase3(phase3: dict[str, Any]) -> HermesRunLane:
    lane = str(phase3.get("lane") or HermesRunLane.FAST_INTERACTIVE.value)
    try:
        return HermesRunLane(lane)
    except ValueError:
        return HermesRunLane.FAST_INTERACTIVE


def _phase3_scope(trigger: Trigger) -> dict[str, Any]:
    matching_scope = trigger.matching_scope if isinstance(trigger.matching_scope, dict) else {}
    phase3 = matching_scope.get("phase3")
    return dict(phase3) if isinstance(phase3, dict) else {}


def _proposal_payload(proposal: CommercialActionProposalRecord) -> dict[str, Any]:
    return dict(proposal.payload) if isinstance(proposal.payload, dict) else {}


def _event_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("event_id") or payload.get("event_ref")
    return str(value) if isinstance(value, str) and value.strip() else None


def _positive_id(value: int | None) -> int | None:
    return int(value) if value and value > 0 else None


def _trigger_id_from_refs(source_refs: list[str]) -> int | None:
    for ref in source_refs:
        value = str(ref)
        if not value.startswith("trigger:"):
            continue
        try:
            return int(value.removeprefix("trigger:"))
        except ValueError:
            return None
    return None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
