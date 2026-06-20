from types import SimpleNamespace

import pytest


def _candidate(fact_type: str, fact_id: str, value: dict, contextual_text: str = "") -> dict:
    return {
        "fact_id": fact_id,
        "fact_type": fact_type,
        "entity_ref": value.get("entity_ref") or value.get("product_ref") or fact_id,
        "value": value,
        "source_refs": value.get("source_refs", ["source:approved"]),
        "confidence": 0.92,
        "risk_tier": "low",
        "status": value.get("status", "active"),
        "freshness": {"state": "current"},
        "contextual_text": contextual_text,
        "retrieval_scores": {"rerank": 0.9},
        "source_units": [],
    }


class _FakeRetrieval:
    def __init__(self, candidates):
        self.candidates = candidates
        self.captured_request = None

    async def retrieve_contextual(self, request):
        self.captured_request = request
        return SimpleNamespace(
            candidates=list(self.candidates),
            degraded_reasons=[],
            missing_evidence=[],
            trace=SimpleNamespace(model_dump=lambda mode: {}),
        )


class _ExplodingRetrieval:
    async def retrieve_contextual(self, request):
        raise AssertionError(f"unexpected retrieval call: {request}")


@pytest.mark.asyncio
async def test_search_authority_uses_seller_catalog_domain_and_no_proposed_or_rewrite():
    from app.modules.agent_memory.contracts import BrainMemorySearchRequest
    from app.modules.agent_memory.service import AgentMemoryService

    retrieval = _FakeRetrieval(
        [
            _candidate(
                "catalog_product",
                "catalog_product:starter",
                {"entity_ref": "catalog:starter", "title": "Starter"},
            ),
            _candidate(
                "catalog_offer",
                "catalog_offer:starter:40000",
                {"product_ref": "catalog:starter", "price": "40 000", "currency": "UZS"},
            ),
        ]
    )

    result = await AgentMemoryService(session=object(), retrieval=retrieval).search_authority(
        BrainMemorySearchRequest(
            workspace_id=1,
            query="starter coins narxi",
            domains=["seller.catalog"],
            required_fields=["offer"],
        )
    )

    assert retrieval.captured_request.requested_fact_types == [
        "catalog_product",
        "catalog_variant",
        "catalog_offer",
        "catalog_media",
    ]
    assert retrieval.captured_request.enable_query_rewrite is False
    assert retrieval.captured_request.enable_agentic_search is False
    assert retrieval.captured_request.include_proposed is False
    assert result.status == "ok"
    assert result.authority_lane[0].domain == "seller.catalog"
    assert result.action_lane == []


@pytest.mark.asyncio
async def test_search_authority_unknown_domain_does_not_widen_to_default_retrieval():
    from app.modules.agent_memory.contracts import BrainMemorySearchRequest
    from app.modules.agent_memory.service import AgentMemoryService

    result = await AgentMemoryService(
        session=object(),
        retrieval=_ExplodingRetrieval(),
    ).search_authority(
        BrainMemorySearchRequest(
            workspace_id=1,
            query="starter coins narxi",
            domains=["unknown.domain"],
        )
    )

    assert result.status == "empty"
    assert result.authority_lane == []


@pytest.mark.asyncio
async def test_search_authority_missing_offer_returns_warning_and_action_option():
    from app.modules.agent_memory.contracts import BrainMemorySearchRequest
    from app.modules.agent_memory.service import AgentMemoryService

    retrieval = _FakeRetrieval(
        [
            _candidate(
                "catalog_product",
                "catalog_product:starter",
                {"entity_ref": "catalog:starter", "title": "Starter"},
            )
        ]
    )

    result = await AgentMemoryService(session=object(), retrieval=retrieval).search_authority(
        BrainMemorySearchRequest(
            workspace_id=1,
            query="starter coins narxi",
            domains=["seller.catalog"],
            required_fields=["offer"],
        )
    )

    assert any(warning.code == "catalog_offer_missing" for warning in result.warnings)
    assert result.action_lane[0].kind == "missing_authority"
    assert result.action_lane[0].payload["required_fields"] == ["offer"]


@pytest.mark.asyncio
async def test_search_style_returns_conversation_examples_without_authority():
    from app.modules.agent_memory.contracts import BrainMemorySearchRequest
    from app.modules.agent_memory.service import AgentMemoryService

    retrieval = _FakeRetrieval(
        [
            _candidate(
                "conversation_pair_fact",
                "conversation_pair:starter:old",
                {
                    "customer_turn": "starter coins narxi qancha",
                    "seller_turn": "Starter 40 000 so'm",
                },
                contextual_text="Customer: starter coins narxi qancha\nSeller: Starter 40 000 so'm",
            )
        ]
    )

    result = await AgentMemoryService(session=object(), retrieval=retrieval).search_style(
        BrainMemorySearchRequest(
            workspace_id=1,
            query="starter reply style",
            domains=["style.voice"],
        )
    )

    assert retrieval.captured_request.requested_fact_types == [
        "voice_fact",
        "conversation_pair_fact",
        "correction_episode_fact",
    ]
    assert result.authority_lane == []
    assert any("40 000" in item.text for item in result.style_lane)


def test_assemble_turn_memory_converts_runtime_context_to_generic_lanes():
    from app.modules.agent_memory.service import AgentMemoryService

    context = SimpleNamespace(
        grounding=SimpleNamespace(
            families={
                "catalog_product": [
                    _candidate(
                        "catalog_product",
                        "catalog_product:starter",
                        {"entity_ref": "catalog:starter", "title": "Starter"},
                    )
                ],
                "catalog_offer": [
                    _candidate(
                        "catalog_offer",
                        "catalog_offer:starter:45000",
                        {
                            "product_ref": "catalog:starter",
                            "price": "45 000",
                            "currency": "UZS",
                        },
                    )
                ],
                "conversation_pair_fact": [
                    _candidate(
                        "conversation_pair_fact",
                        "conversation_pair:starter:old",
                        {},
                        contextual_text="Customer: starter? Seller: Starter 40 000 so'm",
                    )
                ],
            }
        ),
        recent_messages=[],
    )

    bundle = AgentMemoryService(session=object()).assemble_turn_memory(context)

    assert any("45 000 UZS" in item.text for item in bundle.authority_lane)
    assert all("40 000" not in item.text for item in bundle.authority_lane)
    assert any("40 000" in item.text for item in bundle.style_lane)
    assert bundle.situation_lane == []
