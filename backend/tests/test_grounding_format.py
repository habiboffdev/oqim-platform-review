"""Tests for format_agent_grounding (P5a) — render an AgentGroundingBundle to prompt lines."""

from types import SimpleNamespace

from app.modules.agent_runtime_v2.grounding import format_agent_grounding


def _bundle(families):
    return SimpleNamespace(families=families)


def test_formats_contextual_text_with_fact_label():
    bundle = _bundle(
        {
            "catalog_product": [{"contextual_text": "Qizil futbolka, M-XL, 90 000 so'm"}],
            "knowledge_fact": [{"contextual_text": "Qaytarish 14 kun ichida"}],
        }
    )
    lines = format_agent_grounding(bundle)
    assert "[MAHSULOT] Qizil futbolka, M-XL, 90 000 so'm" in lines
    assert "[BILIM] Qaytarish 14 kun ichida" in lines


def test_falls_back_to_value_keys_when_no_contextual_text():
    bundle = _bundle({"catalog_product": [{"value": {"name": "Telefon ekran", "price": "150 000"}}]})
    assert format_agent_grounding(bundle) == ["[MAHSULOT] Telefon ekran — 150 000"]


def test_caps_per_family_and_total_lines():
    families = {
        "catalog_product": [{"contextual_text": f"p{i}"} for i in range(10)],
        "knowledge_fact": [{"contextual_text": f"k{i}"} for i in range(10)],
    }
    lines = format_agent_grounding(_bundle(families), per_family=2, max_lines=3)
    assert len(lines) == 3


def test_empty_or_missing_families_returns_empty():
    assert format_agent_grounding(None) == []
    assert format_agent_grounding(_bundle({})) == []
    assert format_agent_grounding(_bundle({"catalog_product": "not-a-list"})) == []


def test_skips_blank_candidates():
    bundle = _bundle(
        {"catalog_product": [{"value": {}}, {"contextual_text": "   "}, {"contextual_text": "real"}]}
    )
    assert format_agent_grounding(bundle) == ["[MAHSULOT] real"]
