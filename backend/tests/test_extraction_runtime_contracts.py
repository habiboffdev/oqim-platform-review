from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.modules.extraction_runtime.adapters import build_business_source_extraction_request
from app.modules.extraction_runtime.contracts import (
    ExtractionCandidate,
    ExtractionEvidenceSet,
    ExtractionPart,
    ExtractionRequest,
    ExtractionScope,
)
from app.modules.extraction_runtime.profiles import default_profile_registry
from app.modules.extraction_runtime.runtime import (
    StaticCandidateProvider,
    UniversalExtractionRuntime,
)


class SlowCandidateProvider:
    async def extract_candidates(self, **_):
        await asyncio.sleep(0.05)
        return [_candidate()]


def _request() -> ExtractionRequest:
    return ExtractionRequest(
        scope=ExtractionScope(workspace_id=7, conversation_id=11, customer_id=13),
        source_kind="source_bundle",
        source_ref="source:catalog-pdf",
        parts=[
            ExtractionPart(
                kind="text",
                ref="source_unit:pdf:page:1",
                payload={"text": "Qizil ko'ylak 120000 so'm"},
            ),
            ExtractionPart(
                kind="media_ref",
                ref="source_media:pdf:page:1:image:1",
                payload={"mime_type": "image/png"},
            ),
        ],
        profile_refs=["commerce_generic.v1"],
        target_kinds=["catalog_family"],
        correlation_id="corr-extract-1",
        idempotency_key="idem-extract-1",
    )


def _candidate(**overrides) -> ExtractionCandidate:
    payload = {
        "candidate_id": "candidate:catalog:qizil-koylak",
        "workspace_id": 7,
        "owner": "commerce_core",
        "profile_ref": "commerce_generic.v1",
        "kind": "catalog_family",
        "entity_ref": "catalog_product:qizil-koylak",
        "operation": "create",
        "value": {"title": "Qizil ko'ylak", "price": {"amount": 120000}},
        "confidence": 0.84,
        "risk_tier": "medium",
        "evidence_refs": ["source_unit:pdf:page:1"],
        "evidence_state": "valid",
        "requires_review": True,
        "reason_code": "catalog_candidate",
    }
    payload.update(overrides)
    return ExtractionCandidate.model_validate(payload)


@pytest.mark.asyncio
async def test_runtime_rejects_candidate_with_unsupported_evidence_ref():
    runtime = UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=StaticCandidateProvider(
            [
                _candidate(
                    evidence_refs=[
                        "source_unit:pdf:page:1",
                        "source_unit:invented:page:99",
                    ]
                )
            ]
        ),
    )

    result = await runtime.extract(_request())

    assert result.status == "degraded"
    assert result.accepted_candidates == []
    assert result.rejected_candidates[0].candidate_id == "candidate:catalog:qizil-koylak"
    assert result.rejected_candidates[0].reason == "unsupported_evidence_refs"
    assert result.rejected_candidates[0].unsupported_refs == [
        "source_unit:invented:page:99"
    ]


@pytest.mark.asyncio
async def test_runtime_rejects_candidate_that_does_not_belong_to_profile():
    runtime = UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=StaticCandidateProvider(
            [
                _candidate(
                    owner="marketplace",
                    kind="payment_state",
                    profile_ref="commerce_generic.v1",
                    entity_ref="payment:maybe-paid",
                )
            ]
        ),
    )

    result = await runtime.extract(_request())

    assert result.status == "degraded"
    assert result.accepted_candidates == []
    assert result.rejected_candidates[0].reason == "profile_contract_violation"
    assert result.rejected_candidates[0].validation_errors == [
        "owner_not_allowed:marketplace",
        "kind_not_allowed:payment_state",
    ]


@pytest.mark.asyncio
async def test_runtime_rejects_candidate_from_another_workspace():
    runtime = UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=StaticCandidateProvider([_candidate(workspace_id=999)]),
    )

    result = await runtime.extract(_request())

    assert result.status == "degraded"
    assert result.accepted_candidates == []
    assert result.rejected_candidates[0].reason == "workspace_mismatch"
    assert result.rejected_candidates[0].validation_errors == ["workspace_mismatch:999"]


@pytest.mark.asyncio
async def test_runtime_accepts_valid_evidence_gated_candidate():
    candidate = _candidate()
    runtime = UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=StaticCandidateProvider([candidate]),
    )

    result = await runtime.extract(_request())

    assert result.status == "ok"
    assert result.rejected_candidates == []
    assert result.accepted_candidates == [candidate]
    assert result.evidence_summary == {
        "part_count": 2,
        "allowed_evidence_ref_count": 2,
        "profile_count": 1,
    }


@pytest.mark.asyncio
async def test_runtime_degrades_when_candidate_provider_times_out():
    runtime = UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=SlowCandidateProvider(),
        provider_timeout_seconds=0.001,
    )

    result = await runtime.extract(_request())

    assert result.status == "degraded"
    assert result.accepted_candidates == []
    assert result.rejected_candidates == []
    assert result.degraded_reasons == ["provider_timeout"]
    assert result.evidence_summary == {
        "part_count": 2,
        "allowed_evidence_ref_count": 2,
        "profile_count": 1,
    }


def test_default_registry_contains_first_target_profiles():
    registry = default_profile_registry()

    assert set(registry.profile_refs()) >= {
        "commerce_generic.v1",
        "generic_kb.v1",
        "seller_voice.v1",
        "conversation_pairs.v1",
        "buyer_intent.v1",
        "telegram_marketplace.v1",
    }
    assert registry.get("buyer_intent.v1").candidate_kinds == ("buyer_intent",)
    assert registry.get("buyer_intent.v1").owners == ("action_runtime", "review_only")


def test_evidence_set_dedupes_refs_and_reports_unsupported_refs():
    evidence_set = ExtractionEvidenceSet.from_refs(["unit:1", "unit:1", "", "media:1"])

    assert evidence_set.allowed_refs == ("unit:1", "media:1")
    assert evidence_set.unsupported_refs(["unit:1", "invented:9"]) == ["invented:9"]


def test_business_source_adapter_builds_canonical_extraction_request():
    request = build_business_source_extraction_request(
        workspace_id=7,
        source_ref="telegram_channel:@nafis",
        source_kind="telegram_channel",
        source_units=[
            SimpleNamespace(
                unit_ref="source_unit:channel:1",
                source_refs=["telegram_channel:@nafis"],
                state="ready",
                embedding_state="pending",
                degraded_reason=None,
                source_text="Yashil atlas ko'ylak",
            )
        ],
        media_assets=[
            {
                "media_ref": "source_media:channel:1",
                "mime_type": "image/jpeg",
            }
        ],
        correlation_id="corr-adapter",
        idempotency_key="idem-adapter",
        max_source_units=12,
        max_media_assets=40,
    )

    assert request.schema_version == "universal_extraction_request.v1"
    assert request.source_kind == "telegram_channel"
    assert request.allowed_evidence_refs() == (
        "source_unit:channel:1",
        "source_media:channel:1",
    )
    assert "telegram_marketplace.v1" in request.profile_refs
