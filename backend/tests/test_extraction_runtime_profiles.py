from __future__ import annotations

import pytest

from app.modules.extraction_runtime.contracts import (
    ExtractionCandidate,
    ExtractionPart,
    ExtractionRequest,
    ExtractionScope,
)
from app.modules.extraction_runtime.profiles import default_profile_registry
from app.modules.extraction_runtime.runtime import (
    StaticCandidateProvider,
    UniversalExtractionRuntime,
)


def _request(profile_ref: str, *, workspace_id: int = 7) -> ExtractionRequest:
    return ExtractionRequest(
        scope=ExtractionScope(workspace_id=workspace_id, conversation_id=11),
        source_kind="source_bundle",
        source_ref=f"source:{profile_ref}",
        parts=[
            ExtractionPart(
                kind="text",
                ref=f"evidence:{profile_ref}:1",
                payload={"text": "direct source evidence"},
            )
        ],
        profile_refs=[profile_ref],
        correlation_id=f"corr:{profile_ref}",
        idempotency_key=f"idem:{profile_ref}",
    )


def _candidate(
    *,
    profile_ref: str,
    owner: str,
    kind: str,
    candidate_id: str,
    workspace_id: int = 7,
) -> ExtractionCandidate:
    return ExtractionCandidate.model_validate(
        {
            "candidate_id": candidate_id,
            "workspace_id": workspace_id,
            "owner": owner,
            "profile_ref": profile_ref,
            "kind": kind,
            "entity_ref": f"entity:{candidate_id}",
            "operation": "create",
            "value": {"summary": "directly supported candidate"},
            "confidence": 0.81,
            "risk_tier": "medium",
            "evidence_refs": [f"evidence:{profile_ref}:1"],
            "evidence_state": "valid",
            "requires_review": True,
            "reason_code": "profile_eval_fixture",
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile_ref", "owner", "kind"),
    [
        ("commerce_generic.v1", "commerce_core", "catalog_family"),
        ("generic_kb.v1", "business_brain", "kb_entry"),
        ("seller_voice.v1", "business_brain", "voice_observation"),
        ("conversation_pairs.v1", "business_brain", "conversation_pair"),
        ("telegram_marketplace.v1", "marketplace", "marketplace_listing"),
    ],
)
async def test_default_profiles_accept_their_owned_candidate_shapes(
    profile_ref: str,
    owner: str,
    kind: str,
) -> None:
    candidate = _candidate(
        profile_ref=profile_ref,
        owner=owner,
        kind=kind,
        candidate_id=f"candidate:{profile_ref}",
    )
    runtime = UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=StaticCandidateProvider([candidate]),
    )

    result = await runtime.extract(_request(profile_ref))

    assert result.status == "ok"
    assert result.accepted_candidates == [candidate]
    assert result.rejected_candidates == []


@pytest.mark.asyncio
async def test_default_profiles_reject_cross_owner_and_cross_kind_drift() -> None:
    candidate = _candidate(
        profile_ref="seller_voice.v1",
        owner="marketplace",
        kind="payment_state",
        candidate_id="candidate:voice-drift",
    )
    runtime = UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=StaticCandidateProvider([candidate]),
    )

    result = await runtime.extract(_request("seller_voice.v1"))

    assert result.status == "degraded"
    assert result.accepted_candidates == []
    assert result.rejected_candidates[0].reason == "profile_contract_violation"
    assert result.rejected_candidates[0].validation_errors == [
        "owner_not_allowed:marketplace",
        "kind_not_allowed:payment_state",
    ]
