from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial_action import (
    CommercialActionExecutionRecord,
    CommercialActionProposalRecord,
    CommercialDecisionTraceRecord,
)
from app.models.commercial_spine import (
    BusinessBrainFactRecord,
    BusinessBrainIndexRecord,
    BusinessBrainProjectionRecord,
    BusinessBrainUpdateRecord,
    CommercialEventRecord,
    LLMGatewayTraceRecord,
)
from app.modules.commercial_spine.contracts import (
    BusinessBrainFact,
    BusinessBrainProjection,
    BusinessBrainUpdate,
    CommercialActionProposal,
    CommercialDecisionTrace,
    CommercialEvent,
    LLMGatewayTrace,
)


@dataclass(frozen=True, slots=True)
class CommercialDebugSnapshot:
    workspace_id: int
    correlation_id: str
    events: tuple[CommercialEvent, ...]
    facts: tuple[BusinessBrainFact, ...]
    updates: tuple[BusinessBrainUpdate, ...]
    projections: tuple[BusinessBrainProjection, ...]
    action_proposals: tuple[CommercialActionProposal, ...]
    decision_traces: tuple[CommercialDecisionTrace, ...]
    llm_gateway_traces: tuple[LLMGatewayTrace, ...]

    def to_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "correlation_id": self.correlation_id,
            "events": [item.model_dump(mode="json") for item in self.events],
            "facts": [item.model_dump(mode="json") for item in self.facts],
            "updates": [item.model_dump(mode="json") for item in self.updates],
            "projections": [item.model_dump(mode="json") for item in self.projections],
            "action_proposals": [
                item.model_dump(mode="json") for item in self.action_proposals
            ],
            "decision_traces": [
                item.model_dump(mode="json") for item in self.decision_traces
            ],
            "llm_gateway_traces": [
                item.model_dump(mode="json") for item in self.llm_gateway_traces
            ],
        }


class CommercialSpineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def append_event(self, event: CommercialEvent) -> bool:
        existing = await self._get_by_workspace_key(
            CommercialEventRecord,
            workspace_id=event.workspace_id,
            key_field="idempotency_key",
            key_value=event.idempotency_key,
        )
        if existing is not None:
            return False
        self._session.add(
            CommercialEventRecord(
                event_id=event.event_id,
                workspace_id=event.workspace_id,
                source_type=event.source_type,
                source_ref=event.source_ref,
                actor_type=event.actor_type,
                correlation_id=event.correlation_id,
                idempotency_key=event.idempotency_key,
                occurred_at=event.occurred_at,
                payload=dict(event.payload),
                raw_event=event.model_dump(mode="json"),
            )
        )
        await self._session.flush()
        return True

    async def persist_fact(self, fact: BusinessBrainFact) -> bool:
        existing = await self._get_by_workspace_key(
            BusinessBrainFactRecord,
            workspace_id=fact.workspace_id,
            key_field="idempotency_key",
            key_value=fact.idempotency_key,
        )
        if existing is not None:
            return False
        existing_fact = await self._get_by_workspace_key(
            BusinessBrainFactRecord,
            workspace_id=fact.workspace_id,
            key_field="fact_id",
            key_value=fact.fact_id,
        )
        if existing_fact is not None:
            return False
        # local import: app.modules.business_brain.memory imports this module, so a
        # top-level import would be circular.
        from app.modules.business_brain.memory import SEARCHABLE_STRUCTURED_FACT_TYPES

        self._session.add(
            BusinessBrainFactRecord(
                fact_id=fact.fact_id,
                workspace_id=fact.workspace_id,
                fact_type=fact.fact_type,
                entity_ref=fact.entity_ref,
                value=dict(fact.value),
                confidence=fact.confidence,
                status=fact.status,
                risk_tier=fact.risk_tier,
                valid_from=fact.valid_from,
                valid_until=fact.valid_until,
                source_refs=list(fact.source_refs),
                supersedes_fact_id=fact.supersedes_fact_id,
                idempotency_key=fact.idempotency_key,
                raw_fact=fact.model_dump(mode="json"),
                index_state=(
                    "pending"
                    if fact.fact_type in SEARCHABLE_STRUCTURED_FACT_TYPES
                    else "skipped"
                ),
            )
        )
        await self._session.flush()
        return True

    async def persist_update(self, update: BusinessBrainUpdate) -> bool:
        # Idempotent and race-safe. Concurrent full-conversation replays regenerate
        # the same deterministic update_id, so a check-then-insert pre-check is not
        # atomic with the write: the loser of the race hit
        # uq_business_brain_updates_workspace_update with an IntegrityError. Let
        # Postgres enforce dedup against BOTH unique constraints (update_id and
        # idempotency_key) in one statement. DO NOTHING (not DO UPDATE) so a replay
        # never resets an already-applied row's applied_at/approval_state.
        statement = (
            pg_insert(BusinessBrainUpdateRecord)
            .values(
                update_id=update.update_id,
                workspace_id=update.workspace_id,
                target_ref=update.target_ref,
                proposed_value=dict(update.proposed_value),
                source=update.source,
                approval_state=update.approval_state,
                risk_tier=update.risk_tier,
                evidence_refs=list(update.evidence_refs),
                idempotency_key=update.idempotency_key,
                applied_at=update.applied_at,
                raw_update=update.model_dump(mode="json"),
            )
            .on_conflict_do_nothing()
            .returning(BusinessBrainUpdateRecord.id)
        )
        inserted_id = (await self._session.execute(statement)).scalar_one_or_none()
        return inserted_id is not None

    async def upsert_projection(self, projection: BusinessBrainProjection) -> bool:
        existing = await self._get_by_workspace_key(
            BusinessBrainProjectionRecord,
            workspace_id=projection.workspace_id,
            key_field="projection_ref",
            key_value=projection.projection_ref,
        )
        raw = projection.model_dump(mode="json")
        if existing is not None:
            existing.projection_type = projection.projection_type
            existing.entity_ref = projection.entity_ref
            existing.state = dict(projection.state)
            existing.source_refs = list(projection.source_refs)
            existing.degraded = projection.degraded
            existing.degraded_reasons = list(projection.degraded_reasons)
            existing.raw_projection = raw
            await self._session.flush()
            return False
        self._session.add(
            BusinessBrainProjectionRecord(
                projection_ref=projection.projection_ref,
                workspace_id=projection.workspace_id,
                projection_type=projection.projection_type,
                entity_ref=projection.entity_ref,
                state=dict(projection.state),
                source_refs=list(projection.source_refs),
                degraded=projection.degraded,
                degraded_reasons=list(projection.degraded_reasons),
                raw_projection=raw,
            )
        )
        await self._session.flush()
        return True

    async def get_projection(
        self,
        *,
        workspace_id: int,
        projection_ref: str,
    ) -> BusinessBrainProjection | None:
        row = await self._get_by_workspace_key(
            BusinessBrainProjectionRecord,
            workspace_id=workspace_id,
            key_field="projection_ref",
            key_value=projection_ref,
        )
        if row is None:
            return None
        return _projection_from_record(row)

    async def list_projections(
        self,
        *,
        workspace_id: int,
        entity_ref: str | None = None,
        projection_type: str | None = None,
        limit: int = 100,
    ) -> tuple[BusinessBrainProjection, ...]:
        bounded_limit = max(1, min(int(limit), 250))
        statement = select(BusinessBrainProjectionRecord).where(
            BusinessBrainProjectionRecord.workspace_id == workspace_id,
        )
        if entity_ref is not None:
            statement = statement.where(BusinessBrainProjectionRecord.entity_ref == entity_ref)
        if projection_type is not None:
            statement = statement.where(
                BusinessBrainProjectionRecord.projection_type == projection_type
            )
        rows = (
            await self._session.execute(
                statement.order_by(
                    BusinessBrainProjectionRecord.created_at.asc(),
                    BusinessBrainProjectionRecord.id.asc(),
                ).limit(bounded_limit)
            )
        ).scalars()
        return tuple(_projection_from_record(row) for row in rows)

    async def rebuild_projection_from_facts(
        self,
        *,
        workspace_id: int,
        projection_ref: str,
        projection_type: str,
        entity_ref: str,
    ) -> BusinessBrainProjection:
        rows = (
            await self._session.execute(
                select(BusinessBrainFactRecord)
                .where(
                    BusinessBrainFactRecord.workspace_id == workspace_id,
                    BusinessBrainFactRecord.entity_ref == entity_ref,
                    BusinessBrainFactRecord.status.in_(("active", "confirmed")),
                )
                .order_by(BusinessBrainFactRecord.valid_from.asc(), BusinessBrainFactRecord.id.asc())
            )
        ).scalars()
        state: dict = {}
        source_refs: list[str] = []
        for row in rows:
            state[row.fact_type] = row.value
            source_refs.append(f"fact:{row.fact_id}")
            source_refs.extend(str(ref) for ref in row.source_refs)
        projection = BusinessBrainProjection(
            projection_ref=projection_ref,
            workspace_id=workspace_id,
            projection_type=projection_type,
            entity_ref=entity_ref,
            state=state,
            source_refs=_unique(source_refs),
        )
        await self.upsert_projection(projection)
        return projection

    async def get_fact(
        self,
        *,
        workspace_id: int,
        fact_id: str,
    ) -> BusinessBrainFact | None:
        row = await self._get_by_workspace_key(
            BusinessBrainFactRecord,
            workspace_id=workspace_id,
            key_field="fact_id",
            key_value=fact_id,
        )
        if row is None:
            return None
        return _fact_from_record(row)

    async def list_facts(
        self,
        *,
        workspace_id: int,
        entity_ref: str | None = None,
        fact_type: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> tuple[BusinessBrainFact, ...]:
        bounded_limit = max(1, min(int(limit), 250))
        statement = select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.workspace_id == workspace_id,
        )
        if entity_ref is not None:
            statement = statement.where(BusinessBrainFactRecord.entity_ref == entity_ref)
        if fact_type is not None:
            statement = statement.where(BusinessBrainFactRecord.fact_type == fact_type)
        if statuses is not None:
            statement = statement.where(BusinessBrainFactRecord.status.in_(statuses))
        rows = (
            await self._session.execute(
                statement.order_by(
                    BusinessBrainFactRecord.valid_from.desc(),
                    BusinessBrainFactRecord.id.desc(),
                ).limit(bounded_limit)
            )
        ).scalars()
        return tuple(_fact_from_record(row) for row in rows)

    async def list_updates_for_fact(
        self,
        *,
        workspace_id: int,
        fact_id: str,
    ) -> tuple[BusinessBrainUpdate, ...]:
        rows = (
            await self._session.execute(
                select(BusinessBrainUpdateRecord)
                .where(
                    BusinessBrainUpdateRecord.workspace_id == workspace_id,
                    BusinessBrainUpdateRecord.target_ref == f"fact:{fact_id}",
                )
                .order_by(BusinessBrainUpdateRecord.created_at.asc(), BusinessBrainUpdateRecord.id.asc())
            )
        ).scalars()
        return tuple(_update_from_record(row) for row in rows)

    async def mark_fact_status(
        self,
        *,
        workspace_id: int,
        fact_id: str,
        status: str,
        valid_until: datetime | None = None,
    ) -> BusinessBrainFact | None:
        row = await self._get_by_workspace_key(
            BusinessBrainFactRecord,
            workspace_id=workspace_id,
            key_field="fact_id",
            key_value=fact_id,
        )
        if row is None:
            return None
        raw = dict(row.raw_fact or {})
        raw["status"] = status
        if valid_until is not None:
            raw["valid_until"] = valid_until.isoformat()
            row.valid_until = valid_until
        row.status = status
        row.raw_fact = raw
        # Only searchable facts have index records to prune; mirror the gate used by
        # persist_fact / update_fact_state so non-searchable facts stay 'skipped'.
        # local import: app.modules.business_brain.memory imports this module, so a
        # top-level import would be circular.
        from app.modules.business_brain.memory import SEARCHABLE_STRUCTURED_FACT_TYPES
        if row.fact_type in SEARCHABLE_STRUCTURED_FACT_TYPES:
            row.index_state = "pending"
        await self._session.flush()
        return _fact_from_record(row)

    async def update_fact_state(
        self,
        *,
        workspace_id: int,
        fact_id: str,
        status: str | None = None,
        value: dict[str, Any] | None = None,
        confidence: float | None = None,
        risk_tier: str | None = None,
    ) -> BusinessBrainFact | None:
        row = await self._get_by_workspace_key(
            BusinessBrainFactRecord,
            workspace_id=workspace_id,
            key_field="fact_id",
            key_value=fact_id,
        )
        if row is None:
            return None
        raw = dict(row.raw_fact or {})
        if status is not None:
            row.status = status
            raw["status"] = status
        if value is not None:
            row.value = dict(value)
            raw["value"] = dict(value)
        if confidence is not None:
            row.confidence = confidence
            raw["confidence"] = confidence
        if risk_tier is not None:
            row.risk_tier = risk_tier
            raw["risk_tier"] = risk_tier
        row.raw_fact = raw
        # Re-queue searchable facts so the BrainIndexReconciler re-embeds the
        # edited content (in-place edits otherwise leave a stale embedding).
        # local import: app.modules.business_brain.memory imports this module, so a
        # top-level import would be circular.
        from app.modules.business_brain.memory import SEARCHABLE_STRUCTURED_FACT_TYPES
        if row.fact_type in SEARCHABLE_STRUCTURED_FACT_TYPES:
            row.index_state = "pending"
        await self._session.flush()
        return _fact_from_record(row)

    async def persist_index_record(self, record: Any) -> bool:
        existing = await self._get_by_workspace_key(
            BusinessBrainIndexRecord,
            workspace_id=record.workspace_id,
            key_field="idempotency_key",
            key_value=record.idempotency_key,
        )
        raw = record.model_dump(mode="json")
        if existing is not None:
            existing.state = record.state
            existing.embedding_ref = record.embedding_ref
            existing.embedding_model = record.embedding_model
            existing.embedding_state = record.embedding_state
            existing.embedding = record.embedding
            existing.source_text = record.source_text
            existing.degraded_reason = record.degraded_reason
            existing.source_refs = list(record.source_refs)
            existing.raw_index = raw
            await self._session.flush()
            return False
        self._session.add(
            BusinessBrainIndexRecord(
                index_id=record.index_id,
                workspace_id=record.workspace_id,
                fact_id=record.fact_id,
                unit_ref=record.unit_ref,
                state=record.state,
                embedding_ref=record.embedding_ref,
                embedding_model=record.embedding_model,
                embedding_state=record.embedding_state,
                embedding=record.embedding,
                source_text=record.source_text,
                degraded_reason=record.degraded_reason,
                source_refs=list(record.source_refs),
                idempotency_key=record.idempotency_key,
                raw_index=raw,
            )
        )
        await self._session.flush()
        return True

    async def list_index_records(
        self,
        *,
        workspace_id: int,
        fact_id: str,
    ) -> tuple[Any, ...]:
        rows = (
            await self._session.execute(
                select(BusinessBrainIndexRecord)
                .where(
                    BusinessBrainIndexRecord.workspace_id == workspace_id,
                    BusinessBrainIndexRecord.fact_id == fact_id,
                )
                .order_by(BusinessBrainIndexRecord.id.asc())
            )
        ).scalars()
        return tuple(_index_from_record(row) for row in rows)

    async def list_index_records_for_facts(
        self,
        *,
        workspace_id: int,
        fact_ids: tuple[str, ...],
    ) -> dict[str, tuple[Any, ...]]:
        if not fact_ids:
            return {}
        rows = (
            await self._session.execute(
                select(BusinessBrainIndexRecord)
                .where(
                    BusinessBrainIndexRecord.workspace_id == workspace_id,
                    BusinessBrainIndexRecord.fact_id.in_(fact_ids),
                )
                .order_by(
                    BusinessBrainIndexRecord.fact_id.asc(),
                    BusinessBrainIndexRecord.id.asc(),
                )
            )
        ).scalars()
        records_by_fact: dict[str, list[Any]] = {}
        for row in rows:
            records_by_fact.setdefault(row.fact_id, []).append(_index_from_record(row))
        return {fact_id: tuple(records) for fact_id, records in records_by_fact.items()}

    async def search_index_records_vector(
        self,
        *,
        workspace_id: int,
        query_embedding: list[float],
        fact_types: tuple[str, ...] = (),
        statuses: tuple[str, ...] = ("active", "confirmed"),
        limit: int = 50,
    ) -> tuple[tuple[Any, float], ...]:
        from app.brain.embedding_service import halfvec_cosine

        bounded_limit = max(1, min(int(limit), 250))
        distance = halfvec_cosine(BusinessBrainIndexRecord.embedding, query_embedding)
        statement = (
            select(BusinessBrainIndexRecord, distance.label("distance"))
            .join(
                BusinessBrainFactRecord,
                and_(
                    BusinessBrainFactRecord.workspace_id
                    == BusinessBrainIndexRecord.workspace_id,
                    BusinessBrainFactRecord.fact_id == BusinessBrainIndexRecord.fact_id,
                ),
            )
            .where(
                BusinessBrainIndexRecord.workspace_id == workspace_id,
                BusinessBrainIndexRecord.embedding.is_not(None),
                BusinessBrainIndexRecord.embedding_state == "ready",
                BusinessBrainFactRecord.status.in_(statuses),
            )
        )
        if fact_types:
            statement = statement.where(BusinessBrainFactRecord.fact_type.in_(fact_types))
        rows = (
            await self._session.execute(
                statement.order_by(distance.asc(), BusinessBrainIndexRecord.id.asc()).limit(
                    bounded_limit
                )
            )
        ).all()
        results: list[tuple[Any, float]] = []
        for row, raw_distance in rows:
            distance_value = float(raw_distance or 0.0)
            score = max(0.0, min(1.0, 1.0 - distance_value))
            results.append((_index_from_record(row), score))
        return tuple(results)

    async def persist_action_proposal(self, proposal: CommercialActionProposal) -> bool:
        existing = await self._get_by_workspace_key(
            CommercialActionProposalRecord,
            workspace_id=proposal.workspace_id,
            key_field="idempotency_key",
            key_value=proposal.idempotency_key,
        )
        if existing is not None:
            return False
        self._session.add(
            CommercialActionProposalRecord(
                proposal_id=proposal.proposal_id,
                workspace_id=proposal.workspace_id,
                conversation_id=proposal.conversation_id,
                customer_id=proposal.customer_id,
                action_type=proposal.action_type,
                lifecycle_state=proposal.lifecycle_state,
                execution_mode=proposal.execution_mode,
                risk_level=proposal.risk_level,
                requires_approval=proposal.requires_approval,
                executor_runtime=proposal.executor_runtime,
                priority=proposal.priority,
                confidence=proposal.confidence,
                reason_code=proposal.reason_code,
                source_refs=list(proposal.source_refs),
                payload=dict(proposal.payload),
                idempotency_key=proposal.idempotency_key,
                correlation_id=proposal.correlation_id,
                trace_id=proposal.trace_id,
                raw_proposal=proposal.model_dump(mode="json"),
            )
        )
        await self._session.flush()
        return True

    async def get_action_proposal(
        self,
        *,
        workspace_id: int,
        proposal_id: str,
    ) -> CommercialActionProposal | None:
        row = await self._get_by_workspace_key(
            CommercialActionProposalRecord,
            workspace_id=workspace_id,
            key_field="proposal_id",
            key_value=proposal_id,
        )
        if row is None:
            return None
        return _proposal_from_record(row)

    async def get_action_proposal_by_idempotency_key(
        self,
        *,
        workspace_id: int,
        idempotency_key: str,
    ) -> CommercialActionProposal | None:
        row = await self._get_by_workspace_key(
            CommercialActionProposalRecord,
            workspace_id=workspace_id,
            key_field="idempotency_key",
            key_value=idempotency_key,
        )
        if row is None:
            return None
        return _proposal_from_record(row)

    async def update_action_proposal(
        self,
        proposal: CommercialActionProposal,
    ) -> bool:
        row = await self._get_by_workspace_key(
            CommercialActionProposalRecord,
            workspace_id=proposal.workspace_id,
            key_field="proposal_id",
            key_value=proposal.proposal_id,
        )
        if row is None:
            return False
        row.lifecycle_state = proposal.lifecycle_state
        row.execution_mode = proposal.execution_mode
        row.risk_level = proposal.risk_level
        row.requires_approval = proposal.requires_approval
        row.executor_runtime = proposal.executor_runtime
        row.priority = proposal.priority
        row.confidence = proposal.confidence
        row.reason_code = proposal.reason_code
        row.source_refs = list(proposal.source_refs)
        row.payload = dict(proposal.payload)
        row.correlation_id = proposal.correlation_id
        row.trace_id = proposal.trace_id
        row.raw_proposal = proposal.model_dump(mode="json")
        await self._session.flush()
        return True

    async def list_action_proposals(
        self,
        *,
        workspace_id: int,
        conversation_id: int | None = None,
        action_type: str | None = None,
        lifecycle_states: tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> tuple[CommercialActionProposal, ...]:
        bounded_limit = max(1, min(int(limit), 100))
        statement = select(CommercialActionProposalRecord).where(
            CommercialActionProposalRecord.workspace_id == workspace_id,
        )
        if conversation_id is not None:
            statement = statement.where(
                CommercialActionProposalRecord.conversation_id == conversation_id,
            )
        if action_type is not None:
            statement = statement.where(
                CommercialActionProposalRecord.action_type == action_type,
            )
        if lifecycle_states is not None:
            statement = statement.where(
                CommercialActionProposalRecord.lifecycle_state.in_(lifecycle_states),
            )
        rows = (
            await self._session.execute(
                statement.order_by(
                    CommercialActionProposalRecord.created_at.desc(),
                    CommercialActionProposalRecord.id.desc(),
                ).limit(bounded_limit)
            )
        ).scalars()
        return tuple(_proposal_from_record(row) for row in rows)

    async def get_action_execution(
        self,
        *,
        workspace_id: int,
        idempotency_key: str,
    ) -> dict | None:
        row = await self._get_by_workspace_key(
            CommercialActionExecutionRecord,
            workspace_id=workspace_id,
            key_field="idempotency_key",
            key_value=idempotency_key,
        )
        if row is None:
            return None
        return _execution_dict_from_record(row)

    async def persist_action_execution(self, execution: dict) -> bool:
        existing = await self._get_by_workspace_key(
            CommercialActionExecutionRecord,
            workspace_id=int(execution["workspace_id"]),
            key_field="idempotency_key",
            key_value=str(execution["idempotency_key"]),
        )
        if existing is not None:
            return False
        self._session.add(
            CommercialActionExecutionRecord(
                execution_id=str(execution["execution_id"]),
                workspace_id=int(execution["workspace_id"]),
                conversation_id=int(execution["conversation_id"]),
                customer_id=int(execution["customer_id"]),
                proposal_id=str(execution["proposal_id"]),
                action_type=str(execution["action_type"]),
                status=str(execution["status"]),
                reason_code=str(execution["reason_code"]),
                idempotency_key=str(execution["idempotency_key"]),
                delivery_state=execution.get("delivery_state"),
                external_message_id=execution.get("external_message_id"),
                error=execution.get("error"),
                payload=dict(execution.get("payload") or {}),
                raw_result=dict(execution),
            )
        )
        await self._session.flush()
        return True

    async def list_action_executions(
        self,
        *,
        workspace_id: int,
        proposal_id: str | None = None,
        limit: int = 50,
    ) -> tuple[dict, ...]:
        bounded_limit = max(1, min(int(limit), 100))
        statement = select(CommercialActionExecutionRecord).where(
            CommercialActionExecutionRecord.workspace_id == workspace_id,
        )
        if proposal_id is not None:
            statement = statement.where(
                CommercialActionExecutionRecord.proposal_id == proposal_id,
            )
        rows = (
            await self._session.execute(
                statement.order_by(
                    CommercialActionExecutionRecord.created_at.asc(),
                    CommercialActionExecutionRecord.id.asc(),
                ).limit(bounded_limit)
            )
        ).scalars()
        return tuple(_execution_dict_from_record(row) for row in rows)

    async def persist_decision_trace(self, trace: CommercialDecisionTrace) -> bool:
        existing = await self._get_by_workspace_key(
            CommercialDecisionTraceRecord,
            workspace_id=trace.workspace_id,
            key_field="trace_id",
            key_value=trace.trace_id,
        )
        if existing is not None:
            return False
        self._session.add(
            CommercialDecisionTraceRecord(
                trace_id=trace.trace_id,
                workspace_id=trace.workspace_id,
                conversation_id=trace.conversation_id or 0,
                customer_id=trace.customer_id or 0,
                correlation_id=trace.correlation_id,
                accepted_signal_ids=list(trace.accepted_event_ids),
                rejected_signal_ids=[],
                changed_fact_refs=list(trace.changed_fact_refs),
                changed_projection_refs=list(trace.changed_projection_refs),
                emitted_proposal_refs=list(trace.emitted_proposal_refs),
                llm_trace_ids=list(trace.llm_trace_ids),
                degraded_reasons=list(trace.degraded_reasons),
                raw_trace=trace.model_dump(mode="json"),
            )
        )
        await self._session.flush()
        return True

    async def persist_llm_trace(self, trace: LLMGatewayTrace) -> bool:
        existing = await self._get_by_workspace_key(
            LLMGatewayTraceRecord,
            workspace_id=trace.workspace_id,
            key_field="trace_id",
            key_value=trace.trace_id,
        )
        if existing is not None:
            return False
        self._session.add(
            LLMGatewayTraceRecord(
                trace_id=trace.trace_id,
                workspace_id=trace.workspace_id,
                correlation_id=trace.correlation_id,
                route_key=trace.route_key,
                workflow_name=trace.workflow_name,
                prompt_id=trace.prompt_id,
                prompt_version=trace.prompt_version,
                source_refs=list(trace.source_refs),
                status=trace.status,
                model_used=trace.model_used,
                token_usage=dict(trace.token_usage),
                latency_ms=trace.latency_ms,
                cost_estimate=trace.cost_estimate,
                fallback_used=trace.fallback_used,
                validation_errors=list(trace.validation_errors),
                raw_output_ref=trace.raw_output_ref,
                raw_request=dict(trace.raw_request),
                raw_response=dict(trace.raw_response),
            )
        )
        await self._session.flush()
        return True

    async def get_debug_snapshot(
        self,
        *,
        workspace_id: int,
        correlation_id: str,
    ) -> CommercialDebugSnapshot:
        events = tuple(
            CommercialEvent.model_validate(row.raw_event)
            for row in await self._rows_by_correlation(
                CommercialEventRecord,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )
        llm_traces = tuple(
            _llm_trace_from_record(row)
            for row in await self._rows_by_correlation(
                LLMGatewayTraceRecord,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )
        decision_traces = tuple(
            CommercialDecisionTrace.model_validate(row.raw_trace)
            for row in await self._rows_by_correlation(
                CommercialDecisionTraceRecord,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )
        proposals = tuple(
            _proposal_from_record(row)
            for row in await self._rows_by_correlation(
                CommercialActionProposalRecord,
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
        )
        facts = await self._facts_for_traces(
            workspace_id=workspace_id,
            decision_traces=decision_traces,
        )
        updates: tuple[BusinessBrainUpdate, ...] = ()
        projections: tuple[BusinessBrainProjection, ...] = ()
        return CommercialDebugSnapshot(
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            events=events,
            facts=facts,
            updates=updates,
            projections=projections,
            action_proposals=proposals,
            decision_traces=decision_traces,
            llm_gateway_traces=llm_traces,
        )

    async def _facts_for_traces(
        self,
        *,
        workspace_id: int,
        decision_traces: Iterable[CommercialDecisionTrace],
    ) -> tuple[BusinessBrainFact, ...]:
        fact_ids = [
            ref.removeprefix("fact:")
            for trace in decision_traces
            for ref in trace.changed_fact_refs
            if ref.startswith("fact:")
        ]
        if not fact_ids:
            return ()
        rows = (
            await self._session.execute(
                select(BusinessBrainFactRecord).where(
                    BusinessBrainFactRecord.workspace_id == workspace_id,
                    BusinessBrainFactRecord.fact_id.in_(fact_ids),
                )
            )
        ).scalars()
        return tuple(BusinessBrainFact.model_validate(row.raw_fact) for row in rows)

    async def _rows_by_correlation(
        self,
        model: type,
        *,
        workspace_id: int,
        correlation_id: str,
    ) -> tuple:
        rows = (
            await self._session.execute(
                select(model)
                .where(
                    model.workspace_id == workspace_id,
                    model.correlation_id == correlation_id,
                )
                .order_by(model.id.asc())
            )
        ).scalars()
        return tuple(rows)

    async def _get_by_workspace_key(
        self,
        model: type,
        *,
        workspace_id: int,
        key_field: str,
        key_value: str,
    ):
        result = await self._session.execute(
            select(model).where(
                model.workspace_id == workspace_id,
                getattr(model, key_field) == key_value,
            )
        )
        return result.scalar_one_or_none()


def _proposal_from_record(row: CommercialActionProposalRecord) -> CommercialActionProposal:
    raw = dict(row.raw_proposal or {})
    if "lifecycle_state" not in raw:
        raw["lifecycle_state"] = row.lifecycle_state
    if "correlation_id" not in raw:
        raw["correlation_id"] = row.correlation_id
    if "trace_id" not in raw:
        raw["trace_id"] = row.trace_id
    if raw.get("schema_version") != "commercial_action_proposal.v2":
        raw = {
            "schema_version": "commercial_action_proposal.v2",
            "proposal_id": row.proposal_id,
            "workspace_id": row.workspace_id,
            "conversation_id": row.conversation_id,
            "customer_id": row.customer_id,
            "action_type": row.action_type,
            "lifecycle_state": row.lifecycle_state,
            "execution_mode": row.execution_mode,
            "risk_level": row.risk_level,
            "requires_approval": row.requires_approval,
            "executor_runtime": row.executor_runtime,
            "priority": row.priority,
            "confidence": row.confidence,
            "reason_code": row.reason_code,
            "source_refs": list(row.source_refs),
            "payload": dict(row.payload),
            "idempotency_key": row.idempotency_key,
            "correlation_id": row.correlation_id,
            "trace_id": row.trace_id,
        }
    return CommercialActionProposal.model_validate(raw)


def _execution_dict_from_record(row: CommercialActionExecutionRecord) -> dict:
    raw = dict(row.raw_result or {})
    if raw.get("schema_version") == "action_runtime_execution.v1":
        return raw
    return {
        "schema_version": "action_runtime_execution.v1",
        "execution_id": row.execution_id,
        "workspace_id": row.workspace_id,
        "conversation_id": row.conversation_id,
        "customer_id": row.customer_id,
        "proposal_id": row.proposal_id,
        "action_type": row.action_type,
        "status": row.status,
        "reason_code": row.reason_code,
        "idempotency_key": row.idempotency_key,
        "attempt": int(raw.get("attempt") or 1),
        "delivery_state": row.delivery_state,
        "external_message_id": row.external_message_id,
        "payload": dict(row.payload),
        "error": row.error,
    }


def _fact_from_record(row: BusinessBrainFactRecord) -> BusinessBrainFact:
    raw = dict(row.raw_fact or {})
    if raw.get("schema_version") == "business_brain_fact.v1":
        return BusinessBrainFact.model_validate(raw)
    return BusinessBrainFact(
        fact_id=row.fact_id,
        workspace_id=row.workspace_id,
        fact_type=row.fact_type,
        entity_ref=row.entity_ref,
        value=dict(row.value),
        confidence=row.confidence,
        status=row.status,
        risk_tier=row.risk_tier,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        source_refs=list(row.source_refs),
        supersedes_fact_id=row.supersedes_fact_id,
        idempotency_key=row.idempotency_key,
    )


def _update_from_record(row: BusinessBrainUpdateRecord) -> BusinessBrainUpdate:
    raw = dict(row.raw_update or {})
    if raw.get("schema_version") == "business_brain_update.v1":
        return BusinessBrainUpdate.model_validate(raw)
    return BusinessBrainUpdate(
        update_id=row.update_id,
        workspace_id=row.workspace_id,
        target_ref=row.target_ref,
        proposed_value=dict(row.proposed_value),
        source=row.source,
        approval_state=row.approval_state,
        risk_tier=row.risk_tier,
        evidence_refs=list(row.evidence_refs),
        idempotency_key=row.idempotency_key,
        applied_at=row.applied_at,
    )


def _projection_from_record(row: BusinessBrainProjectionRecord) -> BusinessBrainProjection:
    raw = dict(row.raw_projection or {})
    if raw.get("schema_version") == "business_brain_projection.v1":
        return BusinessBrainProjection.model_validate(raw)
    return BusinessBrainProjection(
        projection_ref=row.projection_ref,
        workspace_id=row.workspace_id,
        projection_type=row.projection_type,
        entity_ref=row.entity_ref,
        state=dict(row.state),
        source_refs=list(row.source_refs),
        degraded=row.degraded,
        degraded_reasons=list(row.degraded_reasons),
    )


def _index_from_record(row: BusinessBrainIndexRecord) -> Any:
    from app.modules.business_brain.contracts import BusinessBrainIndexRecordContract

    raw = dict(row.raw_index or {})
    if raw.get("schema_version") == "business_brain_index_record.v1":
        raw.setdefault("embedding_model", row.embedding_model)
        raw.setdefault("embedding_state", row.embedding_state)
        raw.setdefault("source_text", row.source_text)
        return BusinessBrainIndexRecordContract.model_validate(raw)
    return BusinessBrainIndexRecordContract(
        index_id=row.index_id,
        workspace_id=row.workspace_id,
        fact_id=row.fact_id,
        unit_ref=row.unit_ref,
        state=row.state,
        embedding_ref=row.embedding_ref,
        embedding_model=row.embedding_model,
        embedding_state=row.embedding_state,
        embedding=row.embedding,
        source_text=row.source_text,
        degraded_reason=row.degraded_reason,
        source_refs=list(row.source_refs),
        idempotency_key=row.idempotency_key,
    )


def _llm_trace_from_record(row: LLMGatewayTraceRecord) -> LLMGatewayTrace:
    return LLMGatewayTrace(
        trace_id=row.trace_id,
        workspace_id=row.workspace_id,
        correlation_id=row.correlation_id,
        route_key=row.route_key,
        workflow_name=row.workflow_name,
        prompt_id=row.prompt_id,
        prompt_version=row.prompt_version,
        source_refs=list(row.source_refs),
        status=row.status,
        model_used=row.model_used,
        token_usage=dict(row.token_usage),
        latency_ms=row.latency_ms,
        cost_estimate=row.cost_estimate,
        fallback_used=row.fallback_used,
        validation_errors=list(row.validation_errors),
        raw_output_ref=row.raw_output_ref,
        raw_request=dict(row.raw_request),
        raw_response=dict(row.raw_response),
    )


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
