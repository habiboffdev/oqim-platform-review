from __future__ import annotations

import re
from typing import Any

from app.modules.agent_memory.contracts import (
    AgentMemoryBundle,
    AuthorityBundle,
    AuthorityWarning,
    StyleBundle,
)

TRUTH_LIMIT = 10
VOICE_LIMIT = 6
PROMPT_TEXT_LIMIT = 360
UNUSABLE_OFFER_AVAILABILITY = {"inactive", "unavailable", "out_of_stock"}
UNUSABLE_MEDIA_STATE = {"pending", "rejected", "degraded"}
_WS_RE = re.compile(r"\s+")


def build_seller_agent_memory_bundle(
    *,
    grounding: Any,
    history: list[Any],
) -> AgentMemoryBundle:
    families = getattr(grounding, "families", None)
    families = families if isinstance(families, dict) else {}

    authority_lane: list[AuthorityBundle] = []
    style_lane: list[StyleBundle] = []
    warnings: list[AuthorityWarning] = []

    catalog_authority, catalog_warnings = _catalog_authority(families)
    authority_lane.extend(catalog_authority)
    warnings.extend(catalog_warnings)

    authority_lane.extend(_authority_fact_bundles(families.get("knowledge_fact"), "business.rules", "KNOWLEDGE"))
    authority_lane.extend(_authority_fact_bundles(families.get("seller_rule_fact"), "business.rules", "RULE"))
    authority_lane.extend(_authority_fact_bundles(families.get("business_source_media_fact"), "business.source", "SOURCE_MEDIA"))
    authority_lane.extend(_authority_fact_bundles(families.get("business_source_fact"), "business.source", "SOURCE"))

    for fact_type in ("voice_fact", "conversation_pair_fact", "correction_episode_fact"):
        style_lane.extend(_style_fact_bundles(families.get(fact_type), fact_type))

    return AgentMemoryBundle(
        authority_lane=authority_lane[:TRUTH_LIMIT],
        style_lane=style_lane[:VOICE_LIMIT],
        warnings=warnings,
        evidence_budget={
            "authority_limit": TRUTH_LIMIT,
            "style_limit": VOICE_LIMIT,
            "history_count": len(history),
        },
    )


def render_authority_lines(bundle: AgentMemoryBundle) -> list[str]:
    return [item.text for item in bundle.authority_lane if item.text]


def render_style_lines(bundle: AgentMemoryBundle) -> list[str]:
    return [item.text for item in bundle.style_lane if item.text]


def render_warning_codes(warnings: list[AuthorityWarning]) -> list[str]:
    out: list[str] = []
    for warning in warnings:
        code = warning.code
        if warning.target_ref:
            code = f"{code}:{warning.target_ref}"
        out.append(code)
    return out


def _catalog_authority(families: dict[str, Any]) -> tuple[list[AuthorityBundle], list[AuthorityWarning]]:
    products = _candidates(families.get("catalog_product"))
    products_by_ref = _group_by_product_ref(products)
    variant_candidates = _candidates(families.get("catalog_variant"))
    offer_candidates = _candidates(families.get("catalog_offer"))
    media_candidates = _candidates(families.get("catalog_media"))
    variants = _group_by_product_ref(variant_candidates)
    offers = _group_by_product_ref(offer_candidates)
    media = _group_by_product_ref(media_candidates)
    product_refs = _catalog_refs_in_order(products, variant_candidates, offer_candidates, media_candidates)

    bundles: list[AuthorityBundle] = []
    warnings: list[AuthorityWarning] = []
    for product_ref in product_refs:
        product = _first(products_by_ref.get(product_ref))
        value = _value(product) if product else {}
        title = _first_text(value, "title", "name") or product_ref
        parts = [f"[CATALOG] {title}"]
        claim_scope = ["product_identity"]
        object_payload: dict[str, Any] = {"product": {"title": title, "ref": product_ref}}
        missing_fields: list[str] = []
        evidence_refs: list[str] = []
        if product:
            evidence_refs.extend(_source_refs(product))

        variant = _first(variants.get(product_ref))
        variant_text = _variant_text(variant)
        if variant_text:
            parts.append(f"variant: {variant_text}")
            claim_scope.append("variant")
            object_payload["variants"] = [_catalog_object_piece(variant, variant_text)]
            evidence_refs.extend(_source_refs(variant))

        offer = _first_usable_offer(offers.get(product_ref))
        offer_text = _offer_text(offer)
        if offer_text:
            parts.append(f"offer: {offer_text}")
            claim_scope.append("offer")
            object_payload["offers"] = [_catalog_object_piece(offer, offer_text)]
            evidence_refs.extend(_source_refs(offer))
        elif product:
            missing_fields.append("offer")
            warnings.append(
                AuthorityWarning(
                    code="catalog_offer_missing",
                    message="Approved catalog product has no usable active offer.",
                    target_ref=product_ref,
                    evidence_refs=_source_refs(product),
                )
            )

        media_item = _first_usable_media(media.get(product_ref))
        media_text = _media_text(media_item)
        if media_text:
            parts.append(f"media: {media_text}")
            claim_scope.append("media")
            object_payload["media"] = [_catalog_object_piece(media_item, media_text)]
            evidence_refs.extend(_source_refs(media_item))

        bundles.append(
            AuthorityBundle(
                domain="seller.catalog",
                kind="catalog_object",
                authority="approved",
                claim_scope=claim_scope,
                text=" — ".join(parts),
                object=object_payload,
                evidence_refs=_unique(evidence_refs),
                missing_fields=missing_fields,
            )
        )

    return bundles, warnings


def _authority_fact_bundles(candidates: Any, domain: str, label: str) -> list[AuthorityBundle]:
    bundles: list[AuthorityBundle] = []
    for candidate in _candidates(candidates):
        text = _candidate_text(candidate)
        if text:
            bundles.append(
                AuthorityBundle(
                    domain=domain,
                    kind=str(candidate.get("fact_type") or label.lower()),
                    authority="approved",
                    claim_scope=[domain],
                    text=f"[{label}] {text}",
                    object=_value(candidate),
                    evidence_refs=_source_refs(candidate),
                )
            )
    return bundles


def _style_fact_bundles(candidates: Any, fact_type: str) -> list[StyleBundle]:
    bundles: list[StyleBundle] = []
    for candidate in _candidates(candidates):
        if not _has_style_guidance(candidate):
            continue
        text = _candidate_text(candidate)
        if text:
            bundles.append(
                StyleBundle(
                    domain="style.voice",
                    kind=fact_type,
                    text=f"[VOICE] {text}",
                    evidence_refs=_source_refs(candidate),
                    metadata={"fact_id": candidate.get("fact_id")},
                )
            )
    return bundles


def _has_style_guidance(candidate: dict[str, Any]) -> bool:
    value = _value(candidate)
    semantic_keys = (
        "summary",
        "observations",
        "instruction",
        "instructions",
        "examples",
        "seller_turn",
        "customer_turn",
        "correction",
        "lesson",
    )
    return any(value.get(key) not in (None, "") for key in semantic_keys)


def _candidate_text(candidate: dict[str, Any]) -> str:
    value = _value(candidate)
    for key in (
        "summary",
        "answer",
        "rule",
        "observations",
        "instruction",
        "instructions",
        "requirement",
        "description",
        "details",
        "seller_turn",
        "customer_turn",
        "title",
        "name",
    ):
        if value.get(key) not in (None, ""):
            return _compact(_stringify_value(value[key]))
    return ""


def _group_by_product_ref(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        product_ref = _product_ref(candidate)
        if product_ref:
            grouped.setdefault(product_ref, []).append(candidate)
    return grouped


def _catalog_refs_in_order(*candidate_groups: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for candidates in candidate_groups:
        for candidate in candidates:
            product_ref = _product_ref(candidate)
            if product_ref and product_ref not in seen:
                refs.append(product_ref)
                seen.add(product_ref)
    return refs


def _product_ref(candidate: dict[str, Any]) -> str:
    value = _value(candidate)
    ref = value.get("product_ref") or value.get("entity_ref") or candidate.get("entity_ref")
    return str(ref) if ref not in (None, "") else ""


def _variant_text(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return ""
    value = _value(candidate)
    text = _first_text(value, "name", "title", "summary", "description")
    if text:
        return text
    attributes = value.get("attributes")
    if not isinstance(attributes, dict):
        return ""
    parts = [f"{key}: {item}" for key, item in attributes.items() if item not in (None, "")]
    return ", ".join(parts)


def _offer_text(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return ""
    value = _value(candidate)
    availability = str(value.get("availability") or "").lower()
    if value.get("active") is False or availability in UNUSABLE_OFFER_AVAILABILITY:
        return ""
    price_value = value.get("price")
    price_dict = price_value if isinstance(price_value, dict) else {}
    price = _first_text(price_dict, "amount") if price_dict else _first_text(value, "price", "amount")
    currency = _first_text(price_dict, "currency") or _first_text(value, "currency")
    if price and currency:
        return f"{price} {currency}"
    return price or currency


def _media_text(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return ""
    value = _value(candidate)
    quality_state = str(value.get("quality_state") or "").lower()
    crop_state = str(value.get("crop_state") or "").lower()
    if (
        value.get("sendable") is False
        or value.get("approved") is False
        or quality_state in UNUSABLE_MEDIA_STATE
        or crop_state in UNUSABLE_MEDIA_STATE
    ):
        return ""
    return _first_text(value, "media_ref", "url", "file_ref")


def _first_usable_offer(candidates: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for candidate in candidates or []:
        if _offer_text(candidate):
            return candidate
    return None


def _first_usable_media(candidates: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for candidate in candidates or []:
        if _media_text(candidate):
            return candidate
    return None


def _catalog_object_piece(candidate: dict[str, Any] | None, text: str) -> dict[str, Any]:
    if not candidate:
        return {"text": text}
    value = dict(_value(candidate))
    value.setdefault("text", text)
    if candidate.get("fact_id"):
        value.setdefault("ref", candidate["fact_id"])
    return value


def _first_text(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        item = value.get(key)
        if item not in (None, ""):
            return str(item)
    return ""


def _candidates(candidates: Any) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        return []
    return [_candidate_dump(candidate) for candidate in candidates]


def _candidate_dump(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, dict):
        return candidate
    if hasattr(candidate, "model_dump"):
        return candidate.model_dump(mode="json")
    return dict(vars(candidate))


def _value(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("value")
    return value if isinstance(value, dict) else {}


def _source_refs(candidate: dict[str, Any] | None) -> list[str]:
    if not candidate:
        return []
    refs = candidate.get("source_refs")
    return [str(ref) for ref in refs] if isinstance(refs, list) else []


def _first(candidates: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    return candidates[0] if candidates else None


def _stringify_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(_stringify_value(item) for item in value if item not in (None, ""))
    if isinstance(value, dict):
        parts = [
            f"{key}: {_stringify_value(item)}"
            for key, item in value.items()
            if item not in (None, "")
        ]
        return "; ".join(parts)
    return str(value)


def _compact(text: str, *, limit: int = PROMPT_TEXT_LIMIT) -> str:
    compacted = _WS_RE.sub(" ", text).strip()
    if limit <= 0 or len(compacted) <= limit:
        return compacted
    return compacted[: max(0, limit - 1)].rstrip() + "…"


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))
