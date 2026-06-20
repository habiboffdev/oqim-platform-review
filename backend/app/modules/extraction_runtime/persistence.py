from __future__ import annotations

from app.modules.commercial_spine.contracts import BusinessBrainProjection, utc_now
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.extraction_runtime.contracts import (
    ExtractionCandidate,
    ExtractionModel,
    ExtractionResult,
    NonEmptyString,
    ProposeCandidatesRequest,
    ProposeCandidatesResult,
    RejectedExtractionCandidate,
)


class PersistExtractionResultResult(ExtractionModel):
    schema_version: str = "persist_extraction_result_result.v1"
    workspace_id: int
    run_id: NonEmptyString
    run_projection_ref: NonEmptyString
    candidate_projection_refs: list[NonEmptyString]
    accepted_count: int
    rejected_count: int


class ExtractionCandidatePersistenceService:
    """Durable review handoff for Universal Extraction Runtime output.

    The service stores candidates as projection rows, not final truth. Owner
    systems still decide whether a candidate becomes Business Brain memory, OQIM
    Intelligence state, an Action proposal, or a rejected review item.
    """

    def __init__(self, *, repository: CommercialSpineRepository) -> None:
        self._repository = repository

    async def persist_result(
        self,
        result: ExtractionResult,
    ) -> PersistExtractionResultResult:
        candidate_refs: list[str] = []
        for candidate in result.accepted_candidates:
            projection = _accepted_candidate_projection(result, candidate)
            await self._repository.upsert_projection(projection)
            candidate_refs.append(projection.projection_ref)
        for candidate in result.rejected_candidates:
            projection = _rejected_candidate_projection(result, candidate)
            await self._repository.upsert_projection(projection)
            candidate_refs.append(projection.projection_ref)

        run_projection = _run_projection(result, candidate_refs)
        await self._repository.upsert_projection(run_projection)
        return PersistExtractionResultResult(
            workspace_id=result.workspace_id,
            run_id=result.run_id,
            run_projection_ref=run_projection.projection_ref,
            candidate_projection_refs=candidate_refs,
            accepted_count=len(result.accepted_candidates),
            rejected_count=len(result.rejected_candidates),
        )

    async def propose_candidates(
        self,
        request: ProposeCandidatesRequest,
    ) -> ProposeCandidatesResult:
        proposal_refs: list[str] = []
        blocked_count = 0
        for candidate_id in request.candidate_ids:
            projection_ref = _candidate_projection_ref(
                request.run_id,
                str(candidate_id),
            )
            projection = await self._repository.get_projection(
                workspace_id=request.workspace_id,
                projection_ref=projection_ref,
            )
            if projection is None or projection.state.get("candidate_state") != "accepted":
                blocked_count += 1
                continue

            proposal_ref = str(
                projection.state.get("proposal_ref")
                or _proposal_ref(request.run_id, str(candidate_id))
            )
            if projection.state.get("lifecycle_state") == "proposed":
                proposal_refs.append(proposal_ref)
                continue

            state = dict(projection.state)
            state.update(
                {
                    "lifecycle_state": "proposed",
                    "proposal_ref": proposal_ref,
                    "proposed_at": utc_now().isoformat(),
                    "propose_correlation_id": request.correlation_id,
                    "propose_idempotency_key": request.idempotency_key,
                }
            )
            await self._repository.upsert_projection(
                BusinessBrainProjection(
                    projection_ref=projection.projection_ref,
                    workspace_id=projection.workspace_id,
                    projection_type=projection.projection_type,
                    entity_ref=projection.entity_ref,
                    state=state,
                    source_refs=list(projection.source_refs),
                    degraded=projection.degraded,
                    degraded_reasons=list(projection.degraded_reasons),
                )
            )
            proposal_refs.append(proposal_ref)

        return ProposeCandidatesResult(
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            proposed_count=len(proposal_refs),
            blocked_count=blocked_count,
            proposal_refs=proposal_refs,
            degraded_reasons=["blocked_candidates"] if blocked_count else [],
        )


def _run_projection(
    result: ExtractionResult,
    candidate_refs: list[str],
) -> BusinessBrainProjection:
    return BusinessBrainProjection(
        projection_ref=_run_projection_ref(result.run_id),
        workspace_id=result.workspace_id,
        projection_type="extraction_run",
        entity_ref=result.source_ref,
        state={
            "run_id": result.run_id,
            "gateway_status": result.status,
            "source_ref": result.source_ref,
            "profile_refs": list(result.profile_refs),
            "accepted_count": len(result.accepted_candidates),
            "rejected_count": len(result.rejected_candidates),
            "candidate_projection_refs": list(candidate_refs),
            "evidence_summary": dict(result.evidence_summary),
            "correlation_id": result.correlation_id,
            "idempotency_key": result.idempotency_key,
        },
        source_refs=list(result.source_refs),
        degraded=result.status != "ok" or bool(result.degraded_reasons),
        degraded_reasons=list(result.degraded_reasons),
    )


def _accepted_candidate_projection(
    result: ExtractionResult,
    candidate: ExtractionCandidate,
) -> BusinessBrainProjection:
    return BusinessBrainProjection(
        projection_ref=_candidate_projection_ref(result.run_id, candidate.candidate_id),
        workspace_id=result.workspace_id,
        projection_type="extraction_candidate",
        entity_ref=candidate.entity_ref,
        state={
            "run_id": result.run_id,
            "candidate_state": "accepted",
            "lifecycle_state": "accepted",
            "candidate": candidate.model_dump(mode="json"),
            "correlation_id": result.correlation_id,
            "idempotency_key": result.idempotency_key,
        },
        source_refs=list(candidate.evidence_refs),
        degraded=bool(candidate.degraded_reasons),
        degraded_reasons=list(candidate.degraded_reasons),
    )


def _rejected_candidate_projection(
    result: ExtractionResult,
    candidate: RejectedExtractionCandidate,
) -> BusinessBrainProjection:
    return BusinessBrainProjection(
        projection_ref=_candidate_projection_ref(result.run_id, candidate.candidate_id),
        workspace_id=result.workspace_id,
        projection_type="extraction_candidate",
        entity_ref=result.source_ref,
        state={
            "run_id": result.run_id,
            "candidate_state": "rejected",
            "lifecycle_state": "rejected",
            "candidate": candidate.model_dump(mode="json"),
            "correlation_id": result.correlation_id,
            "idempotency_key": result.idempotency_key,
        },
        source_refs=list(result.source_refs),
        degraded=True,
        degraded_reasons=[candidate.reason],
    )


def _run_projection_ref(run_id: str) -> str:
    return f"extraction_run:{run_id}"


def _candidate_projection_ref(run_id: str, candidate_id: str) -> str:
    return f"extraction_candidate:{run_id}:{candidate_id}"


def _proposal_ref(run_id: str, candidate_id: str) -> str:
    return f"extraction_proposal:{run_id}:{candidate_id}"
