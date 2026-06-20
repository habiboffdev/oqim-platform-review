from __future__ import annotations

import uuid
from typing import Any

from app.modules.agent_control.contracts import (
    AgentControlAction,
    AgentControlActionInput,
    AgentControlDecision,
)
from app.modules.commercial_spine.contracts import CommercialActionProposal
from app.modules.commercial_spine.repository import CommercialSpineRepository


class AgentControlService:
    """Shared approval boundary for agent side effects.

    Phase 4 deliberately stores Agent Control actions in the existing
    `commercial_action_proposals` table so reply approval, tool approval, and
    knowledge promotion share the same policy/audit backing store from day one.
    """

    def __init__(self, repository: CommercialSpineRepository) -> None:
        self._repository = repository

    async def create_action(self, payload: AgentControlActionInput) -> AgentControlAction:
        action_idempotency_key = f"agent-control:{payload.idempotency_key}"
        existing = await self._repository.get_action_proposal_by_idempotency_key(
            workspace_id=payload.workspace_id,
            idempotency_key=action_idempotency_key,
        )
        if existing is not None:
            return _action_from_proposal(existing)

        action_id = f"agent_control:{uuid.uuid4().hex}"
        hermes_run_id = payload.hermes_run_id or _hermes_run_id_from_refs(payload.evidence_refs)
        policy_decision = _policy_decision(payload)
        lifecycle_state = "waiting_approval" if policy_decision == "approve" else "proposed"
        action_type = _proposal_action_type(payload.action_kind)
        source_refs = _source_refs(
            [
                *payload.evidence_refs,
                *(_agent_run_refs(hermes_run_id)),
            ],
            payload.target_ref,
        )
        proposal = CommercialActionProposal(
            proposal_id=action_id,
            workspace_id=payload.workspace_id,
            conversation_id=0,
            customer_id=0,
            action_type=action_type,
            lifecycle_state=lifecycle_state,
            execution_mode=(
                "ask_seller_confirmation"
                if policy_decision == "approve"
                else "auto_execute_if_policy_allows"
            ),
            risk_level=payload.risk_level,
            requires_approval=policy_decision == "approve",
            executor_runtime="agent_control",
            priority=_priority(payload.risk_level),
            confidence=1.0,
            reason_code=f"agent_control_{policy_decision}",
            source_refs=source_refs,
            payload={
                "agent_control": {
                    "action_id": action_id,
                    "user_id": payload.user_id,
                    "agent_id": payload.agent_id,
                    "hermes_run_id": hermes_run_id,
                    "action_kind": payload.action_kind,
                    "target_ref": payload.target_ref,
                    "policy_decision": policy_decision,
                    "proposed_payload": payload.proposed_payload,
                }
            },
            idempotency_key=action_idempotency_key,
            correlation_id=payload.correlation_id,
            trace_id=hermes_run_id,
        )
        await self._repository.persist_action_proposal(proposal)
        return _action_from_proposal(proposal)

    async def get_action(
        self,
        *,
        workspace_id: int,
        action_id: str,
    ) -> AgentControlAction | None:
        proposal = await self._repository.get_action_proposal(
            workspace_id=workspace_id,
            proposal_id=action_id,
        )
        if proposal is None:
            return None
        return _action_from_proposal(proposal)

    async def approve(
        self,
        *,
        workspace_id: int,
        action_id: str,
        actor_ref: str,
        correlation_id: str,
    ) -> AgentControlDecision:
        proposal = await self._require_proposal(
            workspace_id=workspace_id,
            action_id=action_id,
        )
        updated = proposal.model_copy(
            update={
                "lifecycle_state": "approved",
                "reason_code": "agent_control_owner_approved",
                "payload": {
                    **proposal.payload,
                    "agent_control_approval": {
                        "actor_ref": actor_ref,
                        "correlation_id": correlation_id,
                    },
                },
            }
        )
        await self._repository.update_action_proposal(updated)
        return AgentControlDecision(
            action_id=action_id,
            proposal_id=updated.proposal_id,
            status="approved",
            actor_ref=actor_ref,
            correlation_id=correlation_id,
        )

    async def mark_executed(
        self,
        *,
        workspace_id: int,
        action_id: str,
        actor_ref: str,
        correlation_id: str,
        execution_payload: dict[str, Any] | None = None,
    ) -> AgentControlDecision:
        proposal = await self._require_proposal(
            workspace_id=workspace_id,
            action_id=action_id,
        )
        updated = proposal.model_copy(
            update={
                "lifecycle_state": "executed",
                "reason_code": "agent_control_auto_executed",
                "payload": {
                    **proposal.payload,
                    "agent_control_execution": {
                        "actor_ref": actor_ref,
                        "correlation_id": correlation_id,
                        "payload": dict(execution_payload or {}),
                    },
                },
            }
        )
        await self._repository.update_action_proposal(updated)
        return AgentControlDecision(
            action_id=action_id,
            proposal_id=updated.proposal_id,
            status="executed",
            actor_ref=actor_ref,
            correlation_id=correlation_id,
        )

    async def reject(
        self,
        *,
        workspace_id: int,
        action_id: str,
        actor_ref: str,
        correlation_id: str,
    ) -> AgentControlDecision:
        proposal = await self._require_proposal(
            workspace_id=workspace_id,
            action_id=action_id,
        )
        updated = proposal.model_copy(
            update={
                "lifecycle_state": "rejected",
                "reason_code": "agent_control_owner_rejected",
                "payload": {
                    **proposal.payload,
                    "agent_control_rejection": {
                        "actor_ref": actor_ref,
                        "correlation_id": correlation_id,
                    },
                },
            }
        )
        await self._repository.update_action_proposal(updated)
        return AgentControlDecision(
            action_id=action_id,
            proposal_id=updated.proposal_id,
            status="rejected",
            actor_ref=actor_ref,
            correlation_id=correlation_id,
        )

    async def _require_proposal(
        self,
        *,
        workspace_id: int,
        action_id: str,
    ) -> CommercialActionProposal:
        proposal = await self._repository.get_action_proposal(
            workspace_id=workspace_id,
            proposal_id=action_id,
        )
        if proposal is None:
            raise ValueError("agent_control_action_not_found")
        return proposal


def _policy_decision(payload: AgentControlActionInput) -> str:
    if payload.approval_required or payload.risk_level in {"medium", "high", "critical"}:
        return "approve"
    return "execute"


def _priority(risk_level: str) -> str:
    if risk_level == "critical":
        return "urgent"
    if risk_level == "high":
        return "high"
    if risk_level == "medium":
        return "medium"
    return "low"


def _source_refs(source_refs: list[str], fallback: str) -> list[str]:
    unique = [str(ref) for ref in dict.fromkeys(source_refs) if str(ref).strip()]
    return unique or [fallback]


def _agent_run_refs(hermes_run_id: str | None) -> list[str]:
    if not hermes_run_id:
        return []
    if hermes_run_id.startswith("agent_run:"):
        return [hermes_run_id]
    return [f"agent_run:{hermes_run_id}"]


def _hermes_run_id_from_refs(source_refs: list[str]) -> str | None:
    for ref in source_refs:
        value = str(ref).strip()
        if value.startswith("agent_run:"):
            return value.removeprefix("agent_run:").strip() or None
        if value.startswith("hermes_run:"):
            return value.removeprefix("hermes_run:").strip() or None
    return None


def _proposal_action_type(action_kind: str) -> str:
    if action_kind == "reply.send":
        return "send_reply"
    if action_kind == "reply.edit":
        return "edit_reply"
    return action_kind


def _control_kind_from_action_type(action_type: str) -> str:
    if action_type == "send_reply":
        return "reply.send"
    if action_type in {"edit_reply", "edit_sent_reply"}:
        return "reply.edit"
    return action_type


def _action_from_proposal(proposal: CommercialActionProposal) -> AgentControlAction:
    control = _control_payload(proposal.payload)
    action_kind = str(control.get("action_kind") or _control_kind_from_action_type(proposal.action_type))
    return AgentControlAction(
        action_id=str(control.get("action_id") or proposal.proposal_id),
        workspace_id=proposal.workspace_id,
        user_id=str(control.get("user_id") or f"workspace:{proposal.workspace_id}"),
        agent_id=control.get("agent_id"),
        hermes_run_id=control.get("hermes_run_id"),
        action_kind=action_kind,  # type: ignore[arg-type]
        target_ref=str(control.get("target_ref") or proposal.action_type),
        proposed_payload=dict(control.get("proposed_payload") or {}),
        risk_level=proposal.risk_level,
        evidence_refs=list(proposal.source_refs),
        policy_decision=str(control.get("policy_decision") or "approve"),  # type: ignore[arg-type]
        status=_status_from_lifecycle(proposal.lifecycle_state),
        proposal_id=proposal.proposal_id,
        correlation_id=proposal.correlation_id or proposal.proposal_id,
    )


def _control_payload(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("agent_control")
    return dict(value) if isinstance(value, dict) else {}


def _status_from_lifecycle(lifecycle_state: str) -> str:
    if lifecycle_state == "approved":
        return "approved"
    if lifecycle_state == "rejected":
        return "rejected"
    if lifecycle_state == "executed":
        return "executed"
    if lifecycle_state in {"failed", "blocked"}:
        return "failed"
    if lifecycle_state == "expired":
        return "expired"
    return "pending"
