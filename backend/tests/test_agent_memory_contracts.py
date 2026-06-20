import pytest
from pydantic import ValidationError


def test_agent_memory_contracts_forbid_extra_fields():
    from app.modules.agent_memory.contracts import AuthorityBundle

    with pytest.raises(ValidationError):
        AuthorityBundle(
            domain="seller.catalog",
            kind="catalog_object",
            authority="approved",
            claim_scope=["offer"],
            text="[CATALOG] Starter — offer: 40 000 UZS",
            unexpected=True,
        )


def test_agent_memory_bundle_preserves_lane_separation_in_json():
    from app.modules.agent_memory.contracts import (
        AgentMemoryBundle,
        AuthorityBundle,
        AuthorityWarning,
        StyleBundle,
    )

    bundle = AgentMemoryBundle(
        authority_lane=[
            AuthorityBundle(
                domain="seller.catalog",
                kind="catalog_object",
                authority="approved",
                claim_scope=["offer"],
                text="[CATALOG] Starter — offer: 45 000 UZS",
                object={
                    "product": {"title": "Starter"},
                    "offers": [{"price": "45 000", "currency": "UZS"}],
                },
                evidence_refs=["source:approved-catalog"],
            )
        ],
        style_lane=[
            StyleBundle(
                domain="style.voice",
                kind="conversation_pair",
                text="[VOICE] Customer: starter? Seller: Starter 40 000 so'm",
                evidence_refs=["conversation:old-example"],
            )
        ],
        warnings=[
            AuthorityWarning(
                code="conversation_evidence_not_runtime_truth",
                message="Conversation examples are style only.",
            )
        ],
    )

    payload = bundle.model_dump(mode="json")

    assert payload["authority_lane"][0]["object"]["offers"][0]["price"] == "45 000"
    assert "40 000" not in payload["authority_lane"][0]["text"]
    assert "40 000" in payload["style_lane"][0]["text"]
    assert payload["warnings"][0]["code"] == "conversation_evidence_not_runtime_truth"


def test_brain_memory_search_result_serializes_structured_tool_output():
    from app.modules.agent_memory.contracts import (
        AuthorityBundle,
        BrainMemorySearchResult,
    )

    result = BrainMemorySearchResult(
        status="ok",
        query="starter coins narxi",
        authority_lane=[
            AuthorityBundle(
                domain="seller.catalog",
                kind="catalog_object",
                authority="approved",
                claim_scope=["product_identity", "offer"],
                text="[CATALOG] Starter — offer: 40 000 UZS",
                evidence_refs=["source:approved-catalog"],
            )
        ],
    )

    payload = result.model_dump(mode="json")

    assert payload["schema_version"] == "brain_memory_search_result.v1"
    assert payload["authority_lane"][0]["domain"] == "seller.catalog"
    assert payload["authority_lane"][0]["claim_scope"] == ["product_identity", "offer"]
