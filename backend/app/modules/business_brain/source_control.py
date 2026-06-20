from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.commercial_spine.contracts import (
    BusinessBrainFact,
    BusinessBrainUpdate,
    CommercialEvent,
    utc_now,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository

SourceControlAction = Literal["archive", "pause", "resume"]


class SourceControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: int = Field(gt=0)
    source_ref: str = Field(min_length=1)
    action: SourceControlAction
    actor_ref: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class SourceControlResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["source_intake_control_result.v1"] = (
        "source_intake_control_result.v1"
    )
    source_ref: str
    action: SourceControlAction
    fact: BusinessBrainFact
    event_created: bool
    update_created: bool


class SourceControlService:
    """Applies owner controls to existing source facts.

    Source Intake remains the source plane; this service does not create a
    separate source system. It only records owner control intent, updates the
    owning `business_source_fact`, and leaves learning/extraction to the
    existing runtime.
    """

    def __init__(self, *, repository: CommercialSpineRepository) -> None:
        self._repository = repository

    async def apply(self, request: SourceControlRequest) -> SourceControlResult:
        fact = await self._find_source_fact(
            workspace_id=request.workspace_id,
            source_ref=request.source_ref,
        )
        if fact is None:
            raise ValueError("source not found")

        now = utc_now()
        update = BusinessBrainUpdate(
            update_id=f"source-control:{request.workspace_id}:{_digest(request.source_ref, request.action)}",
            workspace_id=request.workspace_id,
            target_ref=f"fact:{fact.fact_id}",
            proposed_value={
                "action": request.action,
                "source_ref": request.source_ref,
                "owner_controlled": True,
            },
            source="manual",
            approval_state="confirmed",
            risk_tier="low",
            evidence_refs=[request.source_ref, fact.fact_id],
            idempotency_key=request.idempotency_key,
            applied_at=now,
            actor_type="owner",
            actor_ref=request.actor_ref,
            correlation_id=request.correlation_id,
        )
        update_created = await self._repository.persist_update(update)
        event_created = await self._repository.append_event(
            CommercialEvent(
                event_id=f"source-control-event:{request.workspace_id}:{_digest(request.source_ref, request.action)}",
                workspace_id=request.workspace_id,
                source_type="business_brain.source_intake",
                source_ref=request.source_ref,
                actor_type="owner",
                correlation_id=request.correlation_id,
                idempotency_key=f"{request.idempotency_key}:event",
                occurred_at=now,
                payload={
                    "action": request.action,
                    "source_ref": request.source_ref,
                    "fact_id": fact.fact_id,
                },
            )
        )

        if request.action == "archive":
            changed = await self._repository.mark_fact_status(
                workspace_id=request.workspace_id,
                fact_id=fact.fact_id,
                status="historical",
                valid_until=now,
            )
        else:
            value = _source_value_with_watch_state(
                fact.value,
                watch_state="paused" if request.action == "pause" else "live",
            )
            changed = await self._repository.update_fact_state(
                workspace_id=request.workspace_id,
                fact_id=fact.fact_id,
                status="active",
                value=value,
            )
        if changed is None:
            raise ValueError("source not found")
        return SourceControlResult(
            source_ref=request.source_ref,
            action=request.action,
            fact=changed,
            event_created=event_created,
            update_created=update_created,
        )

    async def _find_source_fact(
        self,
        *,
        workspace_id: int,
        source_ref: str,
    ) -> BusinessBrainFact | None:
        facts = await self._repository.list_facts(
            workspace_id=workspace_id,
            fact_type="business_source_fact",
            statuses=(
                "active",
                "confirmed",
                "proposed",
                "conflicted",
                "degraded",
                "expired",
                "historical",
                "superseded",
                "rejected",
            ),
            limit=250,
        )
        for fact in facts:
            if _source_ref_for_fact(fact) == source_ref:
                return fact
            refs = {str(ref) for ref in fact.source_refs}
            refs.add(str(fact.entity_ref).removeprefix("workspace:source:"))
            if source_ref in refs:
                return fact
        return None


def _source_value_with_watch_state(value: dict[str, Any], *, watch_state: str) -> dict[str, Any]:
    next_value = dict(value or {})
    processing = dict(next_value.get("processing") or {})
    processing["watch_state"] = watch_state
    processing["owner_controlled"] = True
    next_value["processing"] = processing
    return next_value


def _source_ref_for_fact(fact: BusinessBrainFact) -> str:
    for ref in fact.source_refs:
        text = str(ref)
        if text.startswith(("onboarding:source:", "brain:source:", "telegram:channel:")):
            return text
    if fact.entity_ref.startswith("workspace:source:"):
        return fact.entity_ref.removeprefix("workspace:source:")
    return fact.fact_id


def _digest(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:16]
