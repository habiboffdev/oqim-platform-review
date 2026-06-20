from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.extraction_runtime.contracts import (
    ExtractionCandidate,
    ExtractionPart,
    ExtractionRequest,
    ExtractionScope,
    ProposeCandidatesRequest,
)
from app.modules.extraction_runtime.persistence import ExtractionCandidatePersistenceService
from app.modules.extraction_runtime.profiles import default_profile_registry
from app.modules.extraction_runtime.runtime import (
    StaticCandidateProvider,
    UniversalExtractionRuntime,
)


def _request() -> ExtractionRequest:
    return ExtractionRequest(
        scope=ExtractionScope(workspace_id=7),
        source_kind="source_bundle",
        source_ref="source:test-catalog",
        parts=[
            ExtractionPart(
                kind="text",
                ref="source_unit:test:1",
                payload={"text": "Qora tufli 250000 so'm"},
            )
        ],
        profile_refs=["commerce_generic.v1"],
        target_kinds=["catalog_family"],
        correlation_id="corr-extraction-persist",
        idempotency_key="idem-extraction-persist",
    )


def _candidate(**overrides) -> ExtractionCandidate:
    payload = {
        "candidate_id": "candidate:catalog:qora-tufli",
        "workspace_id": 7,
        "owner": "commerce_core",
        "profile_ref": "commerce_generic.v1",
        "kind": "catalog_family",
        "entity_ref": "catalog_product:qora-tufli",
        "operation": "create",
        "value": {"title": "Qora tufli", "price": {"amount": 250000}},
        "confidence": 0.88,
        "risk_tier": "medium",
        "evidence_refs": ["source_unit:test:1"],
        "evidence_state": "valid",
        "requires_review": True,
        "reason_code": "catalog_candidate",
    }
    payload.update(overrides)
    return ExtractionCandidate.model_validate(payload)


@pytest.mark.asyncio
async def test_persist_extraction_result_creates_run_and_candidate_projections(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    request = _request().model_copy(
        update={"scope": ExtractionScope(workspace_id=workspace.id)}
    )
    accepted = _candidate(workspace_id=workspace.id)
    rejected = _candidate(
        candidate_id="candidate:catalog:invented",
        workspace_id=workspace.id,
        entity_ref="catalog_product:invented",
        evidence_refs=["source_unit:missing"],
    )
    runtime = UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=StaticCandidateProvider([accepted, rejected]),
    )
    extraction_result = await runtime.extract(request)
    repository = CommercialSpineRepository(db_session)
    service = ExtractionCandidatePersistenceService(repository=repository)

    persist_result = await service.persist_result(extraction_result)
    repeated = await service.persist_result(extraction_result)

    assert persist_result.accepted_count == 1
    assert persist_result.rejected_count == 1
    assert repeated.candidate_projection_refs == persist_result.candidate_projection_refs

    run_projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref=persist_result.run_projection_ref,
    )
    assert run_projection is not None
    assert run_projection.projection_type == "extraction_run"
    assert run_projection.state["accepted_count"] == 1
    assert run_projection.state["rejected_count"] == 1

    candidate_projections = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="extraction_candidate",
    )
    assert len(candidate_projections) == 2
    accepted_projection = next(
        item
        for item in candidate_projections
        if item.state["candidate"]["candidate_id"] == accepted.candidate_id
    )
    rejected_projection = next(
        item
        for item in candidate_projections
        if item.state["candidate"]["candidate_id"] == rejected.candidate_id
    )
    assert accepted_projection.state["candidate_state"] == "accepted"
    assert accepted_projection.state["lifecycle_state"] == "accepted"
    assert accepted_projection.source_refs == ["source_unit:test:1"]
    assert rejected_projection.state["candidate_state"] == "rejected"
    assert rejected_projection.degraded is True
    assert rejected_projection.degraded_reasons == ["unsupported_evidence_refs"]


@pytest.mark.asyncio
async def test_propose_candidates_moves_only_accepted_candidates_to_review_lifecycle(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    request = _request().model_copy(
        update={"scope": ExtractionScope(workspace_id=workspace.id)}
    )
    accepted = _candidate(workspace_id=workspace.id)
    rejected = _candidate(
        candidate_id="candidate:catalog:invented",
        workspace_id=workspace.id,
        entity_ref="catalog_product:invented",
        evidence_refs=["source_unit:missing"],
    )
    runtime = UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=StaticCandidateProvider([accepted, rejected]),
    )
    extraction_result = await runtime.extract(request)
    repository = CommercialSpineRepository(db_session)
    service = ExtractionCandidatePersistenceService(repository=repository)
    await service.persist_result(extraction_result)

    proposed = await service.propose_candidates(
        ProposeCandidatesRequest(
            workspace_id=workspace.id,
            run_id=extraction_result.run_id,
            candidate_ids=[
                accepted.candidate_id,
                rejected.candidate_id,
                "candidate:missing",
            ],
            correlation_id="corr-propose",
            idempotency_key="idem-propose",
        )
    )
    repeated = await service.propose_candidates(
        ProposeCandidatesRequest(
            workspace_id=workspace.id,
            run_id=extraction_result.run_id,
            candidate_ids=[accepted.candidate_id],
            correlation_id="corr-propose-repeat",
            idempotency_key="idem-propose-repeat",
        )
    )

    assert proposed.proposed_count == 1
    assert proposed.blocked_count == 2
    assert proposed.degraded_reasons == ["blocked_candidates"]
    assert repeated.proposal_refs == proposed.proposal_refs

    candidate_projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref=(
            f"extraction_candidate:{extraction_result.run_id}:{accepted.candidate_id}"
        ),
    )
    assert candidate_projection is not None
    assert candidate_projection.state["lifecycle_state"] == "proposed"
    assert candidate_projection.state["proposal_ref"] == proposed.proposal_refs[0]
    assert candidate_projection.state["propose_idempotency_key"] == "idem-propose"
