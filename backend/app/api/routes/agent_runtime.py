from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.models.agent_session import AgentSession, AgentSessionEvent
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.hermes_run import HermesRun, HermesRunEvent
from app.models.workspace import Workspace

router = APIRouter(prefix="/agent-runtime", tags=["agent-runtime"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/actions")
async def list_agent_runtime_actions(
    workspace: WorkspaceDep,
    session: SessionDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    result = await session.execute(
        select(CommercialActionProposalRecord)
        .where(CommercialActionProposalRecord.workspace_id == workspace.id)
        .order_by(
            CommercialActionProposalRecord.created_at.desc(),
            CommercialActionProposalRecord.id.desc(),
        )
        .limit(limit)
    )
    return {
        "schema_version": "agent_runtime_action_feed.v1",
        "workspace_id": workspace.id,
        "actions": [_action_payload(row) for row in result.scalars().all()],
    }


@router.get("/runs/{run_id}")
async def inspect_agent_runtime_run(
    run_id: str,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    run = await session.scalar(
        select(HermesRun).where(
            HermesRun.workspace_id == workspace.id,
            HermesRun.run_id == run_id,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="hermes_run_not_found",
        )
    events = list(
        (
            await session.execute(
                select(HermesRunEvent)
                .where(
                    HermesRunEvent.workspace_id == workspace.id,
                    HermesRunEvent.run_id == run.run_id,
                )
                .order_by(
                    HermesRunEvent.sequence.asc(),
                    HermesRunEvent.created_at.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    actions = list(
        (
            await session.execute(
                select(CommercialActionProposalRecord)
                .where(
                    CommercialActionProposalRecord.workspace_id == workspace.id,
                    or_(
                        CommercialActionProposalRecord.trace_id == run.run_id,
                        CommercialActionProposalRecord.correlation_id
                        == run.correlation_id,
                    ),
                )
                .order_by(
                    CommercialActionProposalRecord.created_at.desc(),
                    CommercialActionProposalRecord.id.desc(),
                )
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return {
        "schema_version": "agent_runtime_run_trace.v1",
        "workspace_id": workspace.id,
        "run": _run_payload(run),
        "events": [_event_payload(event) for event in events],
        "actions": [_action_payload(action) for action in actions],
    }


@router.get("/sessions/{agent_session_id}")
async def inspect_agent_runtime_session(
    agent_session_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    agent_session = await session.scalar(
        select(AgentSession).where(
            AgentSession.workspace_id == workspace.id,
            AgentSession.id == agent_session_id,
        )
    )
    if agent_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="agent_session_not_found",
        )
    events = list(
        (
            await session.execute(
                select(AgentSessionEvent)
                .where(AgentSessionEvent.agent_session_id == agent_session.id)
                .order_by(
                    AgentSessionEvent.sequence.asc(),
                    AgentSessionEvent.created_at.asc(),
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "schema_version": "agent_runtime_session_trace.v1",
        "workspace_id": workspace.id,
        "session": _session_payload(agent_session),
        "events": [_session_event_payload(event) for event in events],
    }


def _action_payload(row: CommercialActionProposalRecord) -> dict[str, Any]:
    payload = dict(row.payload or {})
    agent_action = dict(payload.get("agent_control") or {})
    return {
        "schema_version": "agent_runtime_action_ref.v1",
        "proposal_id": row.proposal_id,
        "action_type": row.action_type,
        "lifecycle_state": row.lifecycle_state,
        "execution_mode": row.execution_mode,
        "risk_level": row.risk_level,
        "requires_approval": row.requires_approval,
        "confidence": row.confidence,
        "reason_code": row.reason_code,
        "conversation_id": row.conversation_id,
        "customer_id": row.customer_id,
        "trace_id": row.trace_id,
        "correlation_id": row.correlation_id,
        "source_refs": list(row.source_refs or []),
        "agent_action": agent_action,
        "payload": payload,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _run_payload(run: HermesRun) -> dict[str, Any]:
    return {
        "schema_version": "agent_runtime_hermes_run_ref.v1",
        "run_id": run.run_id,
        "agent_id": run.agent_id,
        "agent_kind": run.agent_kind,
        "lane": run.lane,
        "run_mode": run.run_mode,
        "trigger_type": run.trigger_type,
        "trigger_id": run.trigger_id,
        "conversation_id": run.conversation_id,
        "customer_id": run.customer_id,
        "state": run.state,
        "tokens_in": run.tokens_in or 0,
        "tokens_out": run.tokens_out or 0,
        "total_tokens": run.total_tokens or 0,
        "llm_calls": run.llm_calls or 0,
        "total_latency_ms": run.total_latency_ms,
        "llm_latency_ms": run.llm_latency_ms,
        "output_action": run.output_action,
        "output_ref": run.output_ref,
        "source_refs": list(run.source_refs or []),
        "details": dict(run.details or {}),
        "created_at": _iso(run.created_at),
        "updated_at": _iso(run.updated_at),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
    }


def _event_payload(event: HermesRunEvent) -> dict[str, Any]:
    return {
        "schema_version": "agent_runtime_hermes_run_event_ref.v1",
        "event_id": event.event_id,
        "run_id": event.run_id,
        "sequence": event.sequence,
        "kind": event.kind,
        "visibility": event.visibility,
        "owner_label": event.owner_label,
        "owner_detail": event.owner_detail,
        "tool_name": event.tool_name,
        "tool_state": event.tool_state,
        "action_proposal_id": event.action_proposal_id,
        "correlation_id": event.correlation_id,
        "payload": dict(event.payload or {}),
        "created_at": _iso(event.created_at),
    }


def _session_payload(agent_session: AgentSession) -> dict[str, Any]:
    return {
        "schema_version": "agent_runtime_session_ref.v1",
        "agent_session_id": agent_session.id,
        "conversation_id": agent_session.conversation_id,
        "customer_id": agent_session.customer_id,
        "agent_id": agent_session.agent_id,
        "channel": agent_session.channel,
        "session_key": agent_session.session_key,
        "hermes_session_id": agent_session.hermes_session_id,
        "state": agent_session.state,
        "summary": agent_session.summary,
        "event_count": agent_session.event_count,
        "last_customer_event_id": agent_session.last_customer_event_id,
        "last_agent_event_id": agent_session.last_agent_event_id,
        "created_at": _iso(agent_session.created_at),
        "updated_at": _iso(agent_session.updated_at),
    }


def _session_event_payload(event: AgentSessionEvent) -> dict[str, Any]:
    return {
        "schema_version": "agent_runtime_session_event_ref.v1",
        "event_id": event.id,
        "sequence": event.sequence,
        "event_type": event.event_type,
        "direction": event.direction,
        "message_id": event.message_id,
        "hermes_run_id": event.hermes_run_id,
        "text": event.text,
        "payload": dict(event.payload or {}),
        "idempotency_key": event.idempotency_key,
        "created_at": _iso(event.created_at),
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()

