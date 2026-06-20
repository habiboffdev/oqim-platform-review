"""Trigger matcher — fans an event out into ActionProposals.

The matcher is the executor's deterministic core: given a `TriggerEvent`, it
loads all active workspace-scoped triggers matching the event_source, runs
the lightweight scope-predicate, gates external tools on ToolGrant/internal
capabilities on the owning agent config, and writes one
CommercialActionProposalRecord per match. The flow is intentionally pure
SQL + Python so it can be invoked from any event source (channel webhook,
scheduled tick, owner BI command) and so its workspace-isolation behaviour
is testable without spinning up a worker loop.

Cross-workspace fan-out is structurally impossible: every query in here
filters by `workspace_id` and proposal rows carry the source trigger's
workspace as the canonical scope.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.agent import Agent
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.trigger import Trigger
from app.modules.tool_catalog import is_external_tool_scope
from app.modules.tool_grants.service import ToolGrantService


@dataclass
class TriggerEvent:
    """Input event to the matcher.

    `conversation_id` and `customer_id` default to 0 for system-scoped events
    (schedule, source_added). For chat events they should be set to the real
    IDs so downstream renderers can join.
    """

    workspace_id: int
    event_source: str
    payload: dict[str, Any] = field(default_factory=dict)
    conversation_id: int = 0
    customer_id: int = 0
    correlation_id: str | None = None


@dataclass
class MatchedTrigger:
    trigger_id: int
    proposal_id: str
    action_proposal_type: str
    permission_mode: str


# Keys in matching_scope that are control directives, not match predicates.
_CONTROL_SCOPE_KEYS = frozenset({"phase3", "required_tool_scope"})


def _scope_matches(matching_scope: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Simple equality predicate. Empty scope matches anything. Control keys
    (e.g. `required_tool_scope`) are excluded from the match comparison —
    they are evaluated separately by the matcher gate.
    """

    if not matching_scope:
        return True
    for key, expected in matching_scope.items():
        if key in _CONTROL_SCOPE_KEYS:
            continue
        if payload.get(key) != expected:
            return False
    return True


def _proposal_id(workspace_id: int, trigger_id: int, event_source: str, payload: dict[str, Any]) -> str:
    """Workspace-scoped deterministic proposal id (prevents duplicate
    proposals when the same event re-fires).
    """

    digest = hashlib.sha256(
        f"{workspace_id}:{trigger_id}:{event_source}:{sorted(payload.items())}".encode()
    ).hexdigest()[:32]
    return f"trigger:{trigger_id}:{digest}"


# Mapping from permission_mode → (execution_mode, requires_approval).
PERMISSION_TO_EXECUTION = {
    "ask_always": ("proposal", True),
    "auto_approve": ("automated", False),
    "full_access": ("automated", False),
}


class TriggerMatcher:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._grants = ToolGrantService(session)

    async def fan_out(self, event: TriggerEvent) -> list[MatchedTrigger]:
        """Find all matching active triggers and write proposals atomically."""

        stmt = select(Trigger).where(
            Trigger.workspace_id == event.workspace_id,
            Trigger.event_source == event.event_source,
            Trigger.active.is_(True),
        )
        candidates = (await self._session.scalars(stmt)).all()

        matched: list[MatchedTrigger] = []
        for trigger in candidates:
            if not _scope_matches(trigger.matching_scope, event.payload):
                continue

            scope = (trigger.matching_scope or {}).get("required_tool_scope")
            if scope:
                allowed, blocked_status = await self._required_scope_allowed(
                    workspace_id=event.workspace_id,
                    trigger=trigger,
                    scope=str(scope),
                )
                if not allowed:
                    trigger.last_run_status = blocked_status
                    trigger.last_run_at = utc_now()
                    trigger.run_count = trigger.run_count + 1
                    continue

            proposal_id = _proposal_id(
                event.workspace_id, trigger.id, event.event_source, event.payload
            )
            execution_mode, requires_approval = PERMISSION_TO_EXECUTION.get(
                trigger.permission_mode, ("proposal", True)
            )

            existing = await self._session.scalar(
                select(CommercialActionProposalRecord).where(
                    CommercialActionProposalRecord.workspace_id == event.workspace_id,
                    CommercialActionProposalRecord.proposal_id == proposal_id,
                )
            )
            if existing is not None:
                # Idempotent: same event re-fired before the proposal closed.
                matched.append(
                    MatchedTrigger(
                        trigger_id=trigger.id,
                        proposal_id=proposal_id,
                        action_proposal_type=trigger.action_proposal_type,
                        permission_mode=trigger.permission_mode,
                    )
                )
                continue

            record = CommercialActionProposalRecord(
                proposal_id=proposal_id,
                workspace_id=event.workspace_id,
                conversation_id=event.conversation_id,
                customer_id=event.customer_id,
                action_type=trigger.action_proposal_type,
                lifecycle_state="waiting_approval" if requires_approval else "approved",
                execution_mode=execution_mode,
                risk_level="medium",
                requires_approval=requires_approval,
                executor_runtime="trigger_runtime",
                priority="normal",
                confidence=0.6,
                reason_code=f"trigger:{trigger.event_source}",
                source_refs=[f"trigger:{trigger.id}"],
                payload=event.payload,
                idempotency_key=proposal_id,
                correlation_id=event.correlation_id,
                trace_id=f"trigger:{trigger.id}:{event.event_source}",
                raw_proposal={"matching_scope": trigger.matching_scope, "event_payload": event.payload},
            )
            self._session.add(record)
            try:
                await self._session.flush()
            except IntegrityError:
                # Concurrent emitter wrote the same proposal; treat as idempotent.
                await self._session.rollback()
                continue

            trigger.last_run_status = "proposal_created"
            trigger.last_run_at = utc_now()
            trigger.run_count = trigger.run_count + 1
            await self._session.flush()

            matched.append(
                MatchedTrigger(
                    trigger_id=trigger.id,
                    proposal_id=proposal_id,
                    action_proposal_type=trigger.action_proposal_type,
                    permission_mode=trigger.permission_mode,
                )
            )
        return matched

    async def _required_scope_allowed(
        self,
        *,
        workspace_id: int,
        trigger: Trigger,
        scope: str,
    ) -> tuple[bool, str]:
        if is_external_tool_scope(scope):
            allowed = await self._grants.check_grant(
                workspace_id=workspace_id,
                agent_id=trigger.owner_agent_id,
                scope=scope,
            )
            return allowed, "blocked_no_grant"

        agent = await self._session.scalar(
            select(Agent).where(
                Agent.workspace_id == workspace_id,
                Agent.id == trigger.owner_agent_id,
            )
        )
        if agent is None or not agent.is_active:
            return False, "blocked_no_agent"

        tools_config = dict(agent.tools_config or {})
        raw_scopes = tools_config.get("tool_scopes")
        if not isinstance(raw_scopes, list):
            return False, "blocked_missing_capability"
        configured_scopes = {str(item).strip() for item in raw_scopes if str(item).strip()}
        return scope in configured_scopes, "blocked_missing_capability"
