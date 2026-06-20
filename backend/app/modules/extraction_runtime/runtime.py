from __future__ import annotations

import asyncio
from typing import Protocol

from app.modules.extraction_runtime.contracts import (
    ExtractionCandidate,
    ExtractionEvidenceSet,
    ExtractionRequest,
    ExtractionResult,
    RejectedExtractionCandidate,
)
from app.modules.extraction_runtime.profiles import (
    ExtractionProfile,
    ExtractionProfileRegistry,
    default_profile_registry,
)


class CandidateProvider(Protocol):
    async def extract_candidates(
        self,
        *,
        request: ExtractionRequest,
        profiles: list[ExtractionProfile],
    ) -> list[ExtractionCandidate]:
        ...


class StaticCandidateProvider:
    def __init__(self, candidates: list[ExtractionCandidate]) -> None:
        self._candidates = candidates

    async def extract_candidates(self, **_: object) -> list[ExtractionCandidate]:
        return list(self._candidates)


class UniversalExtractionRuntime:
    """Schema and evidence gate for messy-to-semantic extraction candidates.

    This runtime is intentionally bounded. It validates profile ownership and
    evidence refs, then returns candidates or rejection records. Owner systems
    still decide final truth and side effects.
    """

    def __init__(
        self,
        *,
        profile_registry: ExtractionProfileRegistry | None = None,
        candidate_provider: CandidateProvider | None = None,
        provider_timeout_seconds: float = 20.0,
    ) -> None:
        if provider_timeout_seconds <= 0:
            raise ValueError("provider_timeout_seconds must be positive")
        self._profile_registry = profile_registry or default_profile_registry()
        self._candidate_provider = candidate_provider or StaticCandidateProvider([])
        self._provider_timeout_seconds = provider_timeout_seconds

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        profiles = self._profile_registry.resolve(request.profile_refs)
        evidence_set = ExtractionEvidenceSet.from_request(request)
        try:
            candidates = await asyncio.wait_for(
                self._candidate_provider.extract_candidates(
                    request=request,
                    profiles=profiles,
                ),
                timeout=self._provider_timeout_seconds,
            )
        except TimeoutError:
            return _result(
                request=request,
                profiles=profiles,
                evidence_set=evidence_set,
                accepted=[],
                rejected=[],
                degraded_reasons=["provider_timeout"],
                status="degraded",
            )
        except Exception:
            return _result(
                request=request,
                profiles=profiles,
                evidence_set=evidence_set,
                accepted=[],
                rejected=[],
                degraded_reasons=["provider_error"],
                status="degraded",
            )

        profile_by_ref = {profile.profile_ref: profile for profile in profiles}

        accepted: list[ExtractionCandidate] = []
        rejected: list[RejectedExtractionCandidate] = []
        for candidate in candidates:
            if candidate.workspace_id != request.scope.workspace_id:
                rejected.append(
                    _rejected(
                        candidate,
                        reason="workspace_mismatch",
                        validation_errors=[
                            f"workspace_mismatch:{candidate.workspace_id}"
                        ],
                    )
                )
                continue

            profile = profile_by_ref.get(candidate.profile_ref)
            if profile is None:
                rejected.append(
                    _rejected(
                        candidate,
                        reason="profile_not_requested",
                        validation_errors=[f"profile_not_requested:{candidate.profile_ref}"],
                    )
                )
                continue

            profile_errors = _profile_contract_errors(candidate, profile)
            if profile_errors:
                rejected.append(
                    _rejected(
                        candidate,
                        reason="profile_contract_violation",
                        validation_errors=profile_errors,
                    )
                )
                continue

            if not candidate.evidence_refs:
                rejected.append(
                    _rejected(
                        candidate,
                        reason="missing_evidence_refs",
                        validation_errors=["evidence_refs_required"],
                    )
                )
                continue

            unsupported = evidence_set.unsupported_refs(candidate.evidence_refs)
            if unsupported:
                rejected.append(
                    _rejected(
                        candidate,
                        reason="unsupported_evidence_refs",
                        unsupported_refs=unsupported,
                    )
                )
                continue

            accepted.append(candidate)

        degraded_reasons = _unique([item.reason for item in rejected])
        status = "ok"
        if degraded_reasons and accepted:
            status = "degraded"
        elif degraded_reasons:
            status = "degraded"

        return _result(
            request=request,
            profiles=profiles,
            evidence_set=evidence_set,
            accepted=accepted,
            rejected=rejected,
            degraded_reasons=degraded_reasons,
            status=status,
        )


def _profile_contract_errors(
    candidate: ExtractionCandidate,
    profile: ExtractionProfile,
) -> list[str]:
    errors: list[str] = []
    if candidate.owner not in profile.owners:
        errors.append(f"owner_not_allowed:{candidate.owner}")
    if candidate.kind not in profile.candidate_kinds:
        errors.append(f"kind_not_allowed:{candidate.kind}")
    return errors


def _rejected(
    candidate: ExtractionCandidate,
    *,
    reason: str,
    unsupported_refs: list[str] | None = None,
    validation_errors: list[str] | None = None,
) -> RejectedExtractionCandidate:
    return RejectedExtractionCandidate(
        candidate_id=candidate.candidate_id,
        profile_ref=candidate.profile_ref,
        kind=candidate.kind,
        owner=candidate.owner,
        reason=reason,
        unsupported_refs=list(unsupported_refs or []),
        validation_errors=list(validation_errors or []),
    )


def _result(
    *,
    request: ExtractionRequest,
    profiles: list[ExtractionProfile],
    evidence_set: ExtractionEvidenceSet,
    accepted: list[ExtractionCandidate],
    rejected: list[RejectedExtractionCandidate],
    degraded_reasons: list[str],
    status: str,
) -> ExtractionResult:
    return ExtractionResult(
        run_id=f"extraction:{request.idempotency_key}",
        workspace_id=request.scope.workspace_id,
        status=status,
        source_ref=request.source_ref,
        profile_refs=[profile.profile_ref for profile in profiles],
        accepted_candidates=accepted,
        rejected_candidates=rejected,
        degraded_reasons=degraded_reasons,
        evidence_summary={
            "part_count": len(request.parts),
            "allowed_evidence_ref_count": len(evidence_set.allowed_refs),
            "profile_count": len(profiles),
        },
        source_refs=[request.source_ref],
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        resume_token=request.resume_token,
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
