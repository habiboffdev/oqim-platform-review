from types import SimpleNamespace

from app.modules.agent_memory.seller_adapter import (
    build_seller_agent_memory_bundle,
    render_authority_lines,
    render_style_lines,
    render_warning_codes,
)


def render_seller_agent_memory(*, grounding, history):
    bundle = build_seller_agent_memory_bundle(
        grounding=grounding,
        history=history,
    )
    return SimpleNamespace(
        truth_evidence=render_authority_lines(bundle),
        voice_examples=render_style_lines(bundle),
        history=list(history),
        authority_warnings=render_warning_codes(bundle.warnings),
    )


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


def test_catalog_object_bundle_merges_product_variant_offer_and_media():
    grounding = SimpleNamespace(
        families={
            "catalog_product": [
                _candidate(
                    "catalog_product",
                    "catalog_product:satstation",
                    {
                        "entity_ref": "catalog:starter",
                        "title": "Starter",
                        "description": "SATStation starter coin package",
                    },
                )
            ],
            "catalog_variant": [
                _candidate(
                    "catalog_variant",
                    "catalog_variant:starter:5-coins",
                    {
                        "product_ref": "catalog:starter",
                        "name": "5 coins",
                        "attributes": {"coins": 5},
                    },
                )
            ],
            "catalog_offer": [
                _candidate(
                    "catalog_offer",
                    "catalog_offer:starter:40000",
                    {
                        "product_ref": "catalog:starter",
                        "price": "40 000",
                        "currency": "UZS",
                        "availability": "active",
                    },
                )
            ],
            "catalog_media": [
                _candidate(
                    "catalog_media",
                    "catalog_media:starter:cover",
                    {
                        "product_ref": "catalog:starter",
                        "media_ref": "media:starter-cover",
                        "sendable": True,
                    },
                )
            ],
        }
    )

    bundle = render_seller_agent_memory(
        grounding=grounding,
        history=[],
    )

    assert bundle.truth_evidence == [
        "[CATALOG] Starter — variant: 5 coins — offer: 40 000 UZS — media: media:starter-cover"
    ]
    assert bundle.voice_examples == []
    assert bundle.authority_warnings == []


def test_conversation_pairs_are_voice_examples_not_truth():
    grounding = SimpleNamespace(
        families={
            "conversation_pair_fact": [
                _candidate(
                    "conversation_pair_fact",
                    "conversation_pair:starter-old-price",
                    {
                        "customer_turn": "starter coins narxi qancha",
                        "seller_turn": "Starter 40 000 so'm",
                        "quality_label": "approved",
                    },
                    contextual_text="Customer: starter coins narxi qancha\nSeller: Starter 40 000 so'm",
                )
            ],
            "voice_fact": [
                _candidate(
                    "voice_fact",
                    "voice:short-friendly",
                    {"summary": "Short, friendly Uzbek replies."},
                )
            ],
        }
    )

    bundle = render_seller_agent_memory(
        grounding=grounding,
        history=[],
    )

    assert bundle.truth_evidence == []
    assert any("Starter 40 000" in line for line in bundle.voice_examples)
    assert any("Short, friendly Uzbek" in line for line in bundle.voice_examples)


def test_source_facts_are_truth_and_conversation_pairs_stay_voice_examples():
    grounding = SimpleNamespace(
        families={
            "business_source_media_fact": [
                _candidate(
                    "business_source_media_fact",
                    "source_media:starter-price-page",
                    {"summary": "PDF page shows Starter price table."},
                    contextual_text="PDF page shows Starter price table.",
                )
            ],
            "business_source_fact": [
                _candidate(
                    "business_source_fact",
                    "source:catalog-upload",
                    {"summary": "Owner uploaded catalog source."},
                    contextual_text="Owner uploaded catalog source.",
                )
            ],
            "conversation_pair_fact": [
                _candidate(
                    "conversation_pair_fact",
                    "conversation_pair:starter-old-price",
                    {
                        "customer_turn": "Customer: starter?",
                        "seller_turn": "Seller: old price",
                    },
                    contextual_text="Customer: starter? Seller: old price",
                )
            ],
        }
    )

    bundle = render_seller_agent_memory(
        grounding=grounding,
        history=[],
    )

    truth_text = "\n".join(bundle.truth_evidence)
    voice_text = "\n".join(bundle.voice_examples)
    assert "PDF page shows Starter price table." in truth_text
    assert "Owner uploaded catalog source." in truth_text
    assert "old price" not in truth_text
    assert "old price" in voice_text


def test_raw_contextual_text_is_not_prompt_fallback():
    grounding = SimpleNamespace(
        families={
            "business_source_fact": [
                _candidate(
                    "business_source_fact",
                    "source:raw",
                    {},
                    contextual_text="Owner uploaded catalog source.",
                )
            ],
            "voice_fact": [
                _candidate(
                    "voice_fact",
                    "voice:metadata-only",
                    {"delay_range": {"min_ms": 1500, "max_ms": 3000}},
                    contextual_text="Contextual source unit Evidence text: metadata only",
                )
            ],
        }
    )

    bundle = render_seller_agent_memory(grounding=grounding, history=[])

    assert bundle.truth_evidence == []
    assert bundle.voice_examples == []


def test_catalog_offer_without_product_still_emits_catalog_line():
    grounding = SimpleNamespace(
        families={
            "catalog_offer": [
                _candidate(
                    "catalog_offer",
                    "catalog_offer:starter:40000",
                    {
                        "product_ref": "catalog:starter",
                        "price": "40 000",
                        "currency": "UZS",
                        "availability": "active",
                    },
                )
            ]
        }
    )

    bundle = render_seller_agent_memory(
        grounding=grounding,
        history=[],
    )

    assert bundle.truth_evidence == ["[CATALOG] catalog:starter — offer: 40 000 UZS"]
    assert bundle.authority_warnings == []


def test_catalog_offer_price_dict_renders_and_inactive_offer_triggers_missing_warning():
    grounding = SimpleNamespace(
        families={
            "catalog_product": [
                _candidate(
                    "catalog_product",
                    "catalog_product:premium",
                    {
                        "entity_ref": "catalog:premium",
                        "title": "Premium",
                    },
                ),
                _candidate(
                    "catalog_product",
                    "catalog_product:inactive-only",
                    {
                        "entity_ref": "catalog:inactive-only",
                        "title": "Inactive Only",
                    },
                ),
            ],
            "catalog_offer": [
                _candidate(
                    "catalog_offer",
                    "catalog_offer:premium:250000",
                    {
                        "product_ref": "catalog:premium",
                        "price": {"amount": 250000, "currency": "UZS"},
                    },
                ),
                _candidate(
                    "catalog_offer",
                    "catalog_offer:inactive-only:100000",
                    {
                        "product_ref": "catalog:inactive-only",
                        "price": "100 000",
                        "currency": "UZS",
                        "availability": "inactive",
                    },
                ),
            ],
        }
    )

    bundle = render_seller_agent_memory(
        grounding=grounding,
        history=[],
    )

    assert "[CATALOG] Premium — offer: 250000 UZS" in bundle.truth_evidence
    assert "[CATALOG] Inactive Only" in bundle.truth_evidence
    assert "catalog_offer_missing:catalog:inactive-only" in bundle.authority_warnings


def test_catalog_variant_with_only_attributes_renders_attributes():
    grounding = SimpleNamespace(
        families={
            "catalog_variant": [
                _candidate(
                    "catalog_variant",
                    "catalog_variant:starter:five-coins",
                    {
                        "product_ref": "catalog:starter",
                        "attributes": {"coins": 5, "duration": "1 month"},
                    },
                )
            ]
        }
    )

    bundle = render_seller_agent_memory(
        grounding=grounding,
        history=[],
    )

    assert bundle.truth_evidence == [
        "[CATALOG] catalog:starter — variant: coins: 5, duration: 1 month"
    ]


def test_catalog_media_skips_pending_unapproved_and_uses_approved_sendable_media():
    grounding = SimpleNamespace(
        families={
            "catalog_media": [
                _candidate(
                    "catalog_media",
                    "catalog_media:starter:pending",
                    {
                        "product_ref": "catalog:starter",
                        "media_ref": "media:pending",
                        "sendable": True,
                        "quality_state": "pending",
                    },
                ),
                _candidate(
                    "catalog_media",
                    "catalog_media:starter:unapproved",
                    {
                        "product_ref": "catalog:starter",
                        "media_ref": "media:unapproved",
                        "sendable": True,
                        "approved": False,
                    },
                ),
                _candidate(
                    "catalog_media",
                    "catalog_media:starter:approved",
                    {
                        "product_ref": "catalog:starter",
                        "media_ref": "media:approved",
                        "sendable": True,
                        "approved": True,
                        "quality_state": "approved",
                        "crop_state": "approved",
                    },
                ),
            ]
        }
    )

    bundle = render_seller_agent_memory(
        grounding=grounding,
        history=[],
    )

    assert bundle.truth_evidence == ["[CATALOG] catalog:starter — media: media:approved"]


def test_catalog_refs_preserve_first_seen_retrieval_order():
    grounding = SimpleNamespace(
        families={
            "catalog_product": [
                _candidate(
                    "catalog_product",
                    "catalog_product:zebra",
                    {
                        "entity_ref": "catalog:zebra",
                        "title": "Zebra",
                    },
                ),
                _candidate(
                    "catalog_product",
                    "catalog_product:alpha",
                    {
                        "entity_ref": "catalog:alpha",
                        "title": "Alpha",
                    },
                ),
            ],
            "catalog_offer": [
                _candidate(
                    "catalog_offer",
                    "catalog_offer:zebra:100",
                    {
                        "product_ref": "catalog:zebra",
                        "price": 100,
                        "currency": "UZS",
                    },
                ),
                _candidate(
                    "catalog_offer",
                    "catalog_offer:alpha:200",
                    {
                        "product_ref": "catalog:alpha",
                        "price": 200,
                        "currency": "UZS",
                    },
                ),
            ],
        }
    )

    bundle = render_seller_agent_memory(
        grounding=grounding,
        history=[],
    )

    assert bundle.truth_evidence == [
        "[CATALOG] Zebra — offer: 100 UZS",
        "[CATALOG] Alpha — offer: 200 UZS",
    ]


def test_catalog_offer_beats_conversation_pair_price_in_truth_lane():
    grounding = SimpleNamespace(
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
                    {"product_ref": "catalog:starter", "price": "45 000", "currency": "UZS"},
                )
            ],
            "conversation_pair_fact": [
                _candidate(
                    "conversation_pair_fact",
                    "conversation_pair:starter:old",
                    {
                        "customer_turn": "starter coins narxi qancha",
                        "seller_turn": "Starter 40 000 so'm",
                        "quality_label": "approved",
                    },
                    contextual_text="Customer: starter coins narxi qancha\nSeller: Starter 40 000 so'm",
                )
            ],
        }
    )

    bundle = render_seller_agent_memory(
        grounding=grounding,
        history=[],
    )

    assert any("45 000 UZS" in line for line in bundle.truth_evidence)
    assert all("40 000" not in line for line in bundle.truth_evidence)
    assert any("40 000" in line for line in bundle.voice_examples)


def test_contextual_source_units_are_compacted_before_prompt_rendering():
    repeated_context = " ".join(
        [
            "Contextual source unit Fact type: knowledge_fact Entity ref: satstation "
            "Evidence text: Fact type: knowledge_fact Entity: satstation "
            '{"summary": "SATStation uses a baseline test and targeted drills.", '
            '"topic": "Platform"} Source refs: message:1 | message:2'
        ]
        * 8
    )
    voice_context = " ".join(
        [
            "Fact type: voice_fact Entity: seller_voice "
            '{"summary": "Short, warm replies that mirror the customer."} '
            "Source refs: message:3 | message:4 Contextual source unit "
            "Fact type: voice_fact Entity ref: seller_voice"
        ]
        * 8
    )
    grounding = SimpleNamespace(
        families={
            "knowledge_fact": [
                _candidate(
                    "knowledge_fact",
                    "knowledge:satstation-platform",
                    {
                        "summary": "SATStation uses a baseline test and targeted drills.",
                        "topic": "Platform",
                    },
                    contextual_text=repeated_context,
                )
            ],
            "voice_fact": [
                _candidate(
                    "voice_fact",
                    "voice:short",
                    {"summary": "Short, warm replies that mirror the customer."},
                    contextual_text=voice_context,
                )
            ],
            "business_source_fact": [
                _candidate(
                    "business_source_fact",
                    "source:raw",
                    {},
                    contextual_text=(
                        "Fact type: business_source_fact Entity: satstation "
                        "Source refs: message:8 Contextual source unit "
                        "Fact type: business_source_fact Entity ref: satstation "
                        "Evidence text: SATStation uses a baseline test."
                    ),
                )
            ],
        }
    )

    bundle = render_seller_agent_memory(
        grounding=grounding,
        history=[],
    )

    rendered = [*bundle.truth_evidence, *bundle.voice_examples]
    assert rendered
    assert all(len(line) <= 420 for line in rendered)
    assert sum(len(line) for line in rendered) < 900
    assert all("Contextual source unit" not in line for line in rendered)
    assert "Contextual source unit" not in bundle.truth_evidence[0]
    assert "baseline test and targeted drills" in bundle.truth_evidence[0]
    assert "Short, warm replies" in bundle.voice_examples[0]
