from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hermes_run import HermesRun, HermesRunEvent
from app.models.commercial_action import CommercialActionProposalRecord


class AgentControlAuditService:
    """Operator-facing audit view for Phase 4 agent side effects and retrieval."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run_audit(self, *, workspace_id: int, run_id: str) -> dict[str, Any]:
        run = await self._session.scalar(
            select(HermesRun).where(
                HermesRun.workspace_id == workspace_id,
                HermesRun.run_id == run_id,
            )
        )
        if run is None:
            raise ValueError("hermes_run_not_found")

        events = list(
            (
                await self._session.scalars(
                    select(HermesRunEvent)
                    .where(
                        HermesRunEvent.workspace_id == workspace_id,
                        HermesRunEvent.run_id == run_id,
                    )
                    .order_by(HermesRunEvent.sequence.asc(), HermesRunEvent.id.asc())
                )
            ).all()
        )
        proposals = await self._action_rows_for_run(
            workspace_id=workspace_id,
            run_id=run_id,
        )
        tool_events = [event for event in events if event.kind == "tool_called"]
        knowledge_events = [
            event for event in tool_events if str(event.tool_name or "").startswith("knowledge")
        ]

        return {
            "schema_version": "agent_control_run_audit.v1",
            "run": _run_payload(run),
            "tool_calls": [_tool_event_payload(event) for event in tool_events],
            "knowledge_operations": [
                _tool_event_payload(event) for event in knowledge_events
            ],
            "knowledge_searches": [
                _knowledge_search_payload(event)
                for event in knowledge_events
                if "search" in str(event.tool_name or "")
            ],
            "actions": [_action_payload(row) for row in proposals],
            "owner_decisions": [
                _owner_decision_payload(row)
                for row in proposals
                if _owner_decision_payload(row) is not None
            ],
            "summary": {
                "tool_call_count": len(tool_events),
                "knowledge_operation_count": len(knowledge_events),
                "knowledge_search_count": sum(
                    1 for event in knowledge_events if "search" in str(event.tool_name or "")
                ),
                "action_count": len(proposals),
                "owner_decision_count": sum(
                    1 for row in proposals if _owner_decision_payload(row) is not None
                ),
            },
        }

    async def _action_rows_for_run(
        self,
        *,
        workspace_id: int,
        run_id: str,
    ) -> list[CommercialActionProposalRecord]:
        rows = list(
            (
                await self._session.scalars(
                    select(CommercialActionProposalRecord)
                    .where(CommercialActionProposalRecord.workspace_id == workspace_id)
                    .order_by(
                        CommercialActionProposalRecord.created_at.asc(),
                        CommercialActionProposalRecord.id.asc(),
                    )
                )
            ).all()
        )
        return [row for row in rows if _row_links_run(row, run_id)]


def _run_payload(run: HermesRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "workspace_id": run.workspace_id,
        "agent_id": run.agent_id,
        "run_mode": run.run_mode,
        "state": run.state,
        "trigger_type": run.trigger_type,
        "trigger_id": run.trigger_id,
        "output_action": run.output_action,
        "output_ref": run.output_ref,
        "created_at": _iso(run.created_at),
        "completed_at": _iso(run.completed_at),
    }


def _tool_event_payload(event: HermesRunEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "sequence": event.sequence,
        "tool_name": event.tool_name,
        "tool_state": event.tool_state,
        "action_proposal_id": event.action_proposal_id,
        "payload": dict(event.payload or {}),
        "created_at": _iso(event.created_at),
    }


def _knowledge_search_payload(event: HermesRunEvent) -> dict[str, Any]:
    payload = dict(event.payload or {})
    return {
        "tool_name": event.tool_name,
        "query": payload.get("query"),
        "scope": payload.get("scope"),
        "collection_ids": list(payload.get("collection_ids") or []),
        "tags": list(payload.get("tags") or []),
        "hit_count": int(payload.get("hit_count") or 0),
        "citations": list(payload.get("citations") or []),
        "created_at": _iso(event.created_at),
    }


def _action_payload(row: CommercialActionProposalRecord) -> dict[str, Any]:
    control = _control_payload(row)
    return {
        "action_id": row.proposal_id,
        "action_kind": control.get("action_kind") or row.action_type,
        "action_type": row.action_type,
        "target_ref": control.get("target_ref"),
        "lifecycle_state": row.lifecycle_state,
        "policy_decision": control.get("policy_decision"),
        "requires_approval": row.requires_approval,
        "risk_level": row.risk_level,
        "trace_id": row.trace_id,
        "source_refs": list(row.source_refs or []),
        "proposed_payload": dict(control.get("proposed_payload") or {}),
        "approval_latency_ms": _latency_ms(row.created_at, row.updated_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _owner_decision_payload(row: CommercialActionProposalRecord) -> dict[str, Any] | None:
    payload = dict(row.payload or {})
    decision = payload.get("agent_control_approval") or payload.get("agent_control_rejection")
    execution = payload.get("agent_control_execution")
    if not isinstance(decision, dict) and not isinstance(execution, dict):
        return None
    value = decision if isinstance(decision, dict) else execution
    return {
        "action_id": row.proposal_id,
        "status": row.lifecycle_state,
        "actor_ref": value.get("actor_ref"),
        "correlation_id": value.get("correlation_id"),
        "latency_ms": _latency_ms(row.created_at, row.updated_at),
    }


def _row_links_run(row: CommercialActionProposalRecord, run_id: str) -> bool:
    if row.trace_id == run_id:
        return True
    agent_run_ref = f"agent_run:{run_id}"
    hermes_run_ref = f"hermes_run:{run_id}"
    if agent_run_ref in list(row.source_refs or []) or hermes_run_ref in list(row.source_refs or []):
        return True
    control = _control_payload(row)
    return control.get("hermes_run_id") == run_id


def _control_payload(row: CommercialActionProposalRecord) -> dict[str, Any]:
    payload = dict(row.payload or {})
    value = payload.get("agent_control")
    return dict(value) if isinstance(value, dict) else {}


def _latency_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
