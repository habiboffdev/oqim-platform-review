"""Format Retrieval-Core agent grounding into prompt-ready evidence lines (P5a).

The Reply Agent runtime consumes the SHARED `AgentRuntimeContextService` grounding
(catalog / KB / rules / voice via `build_agent_grounding`) instead of rolling its
own retrieval. This module turns that `AgentGroundingBundle` into short, readable
evidence lines for the prompt — so the agent grounds on real business facts and
stops dead-ending with "I don't know".
"""

from __future__ import annotations

from typing import Any

# Readable value keys, ordered: identity first, then commercial detail, then prose.
_VALUE_KEYS: tuple[str, ...] = (
    "name",
    "title",
    "topic",
    "question",
    "answer",
    "rule",
    "requirement",
    "instruction",
    "instructions",
    "price",
    "amount",
    "stock",
    "availability",
    "description",
    "summary",
    "details",
)

_FACT_LABELS: dict[str, str] = {
    "catalog_product": "MAHSULOT",
    "catalog_variant": "VARIANT",
    "catalog_offer": "TAKLIF",
    "catalog_media": "MAHSULOT MEDIA",
    "knowledge_fact": "BILIM",
    "seller_rule_fact": "QOIDA",
    "voice_fact": "USLUB",
    "business_source_media_fact": "MANBA",
}


def _candidate_text(candidate: dict[str, Any]) -> str:
    contextual = str(candidate.get("contextual_text") or "").strip()
    if contextual:
        return contextual[:240]
    value = candidate.get("value")
    value = value if isinstance(value, dict) else {}
    parts = [f"{value[key]}".strip() for key in _VALUE_KEYS if value.get(key) not in (None, "")]
    return " — ".join(part for part in parts if part)[:240]


def format_agent_grounding(
    grounding: Any,
    *,
    per_family: int = 3,
    max_lines: int = 10,
) -> list[str]:
    """Render an AgentGroundingBundle into short evidence lines for the prompt.

    Tolerant of missing/odd shapes (returns [] rather than raising) so a grounding
    miss lowers confidence and escalates rather than crashing the reply.
    """
    families = getattr(grounding, "families", None)
    if not isinstance(families, dict):
        return []
    lines: list[str] = []
    for fact_type, candidates in families.items():
        if not isinstance(candidates, list):
            continue
        label = _FACT_LABELS.get(fact_type, str(fact_type))
        for candidate in candidates[:per_family]:
            if not isinstance(candidate, dict):
                continue
            text = _candidate_text(candidate)
            if text:
                lines.append(f"[{label}] {text}")
            if len(lines) >= max_lines:
                return lines
    return lines
