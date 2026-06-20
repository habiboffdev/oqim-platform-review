from __future__ import annotations

from collections.abc import Sequence
from typing import Any

LEARNED_REVIEW_SCHEMA_VERSION = "onboarding_learned_review.v1"


def build_onboarding_learned_review_projection(
    *,
    facts: Sequence[Any],
) -> dict[str, Any]:
    """Build the owner-facing review queue from proposed Business Brain facts."""
    source_evidence_by_ref = _build_source_evidence_by_ref(facts=facts)
    proposed = [
        fact
        for fact in facts
        if str(getattr(fact, "status", "") or "").strip().lower() == "proposed"
    ]
    product_rows: dict[str, dict[str, Any]] = {}
    offers_by_product: dict[str, list[dict[str, Any]]] = {}
    media_by_product: dict[str, list[dict[str, Any]]] = {}
    knowledge: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    voice: list[dict[str, Any]] = []
    integrations: list[dict[str, Any]] = []

    for fact in proposed:
        fact_type = str(getattr(fact, "fact_type", "") or "")
        value = dict(getattr(fact, "value", {}) or {})
        if fact_type == "catalog_product":
            product_ref = str(
                value.get("identity_ref")
                or getattr(fact, "fact_id", "")
                or getattr(fact, "entity_ref", "")
            )
            product_rows[product_ref] = {
                "product_ref": product_ref,
                "fact_id": str(getattr(fact, "fact_id", "")),
                "title": str(value.get("title") or product_ref),
                "category": value.get("category"),
                "description": value.get("description"),
                "confidence": float(getattr(fact, "confidence", 0.0) or 0.0),
                "risk_tier": str(getattr(fact, "risk_tier", "") or ""),
                "source_refs": list(getattr(fact, "source_refs", []) or []),
                "source_evidence": _source_evidence_for_refs(
                    source_refs=list(getattr(fact, "source_refs", []) or []),
                    source_evidence_by_ref=source_evidence_by_ref,
                ),
                "offers": [],
                "media": [],
            }
            continue
        if fact_type == "catalog_offer":
            product_ref = _product_ref_for_related_fact(fact=fact, value=value)
            offers_by_product.setdefault(product_ref, []).append(
                {
                    "fact_id": str(getattr(fact, "fact_id", "")),
                    "offer_ref": value.get("offer_ref"),
                    "price": value.get("price"),
                    "stock": value.get("stock"),
                    "active": value.get("active"),
                    "source_refs": list(getattr(fact, "source_refs", []) or []),
                }
            )
            continue
        if fact_type == "catalog_media":
            product_ref = _product_ref_for_related_fact(fact=fact, value=value)
            media_by_product.setdefault(product_ref, []).append(
                {
                    "fact_id": str(getattr(fact, "fact_id", "")),
                    "media_ref": value.get("media_ref"),
                    "source_media_ref": value.get("source_media_ref"),
                    "media_type": value.get("media_type"),
                    "url": value.get("url"),
                    "quality_state": value.get("quality_state"),
                    "crop_state": value.get("crop_state"),
                    "approved": bool(value.get("approved") is True),
                    "source_refs": list(getattr(fact, "source_refs", []) or []),
                }
            )
            continue
        if fact_type == "knowledge_fact":
            knowledge.append(
                _memory_review_item(
                    fact=fact,
                    value=value,
                    source_evidence_by_ref=source_evidence_by_ref,
                )
            )
            continue
        if fact_type == "seller_rule_fact":
            rules.append(
                _memory_review_item(
                    fact=fact,
                    value=value,
                    source_evidence_by_ref=source_evidence_by_ref,
                )
            )
            continue
        if fact_type == "voice_fact":
            voice.append(
                _memory_review_item(
                    fact=fact,
                    value=value,
                    source_evidence_by_ref=source_evidence_by_ref,
                )
            )
            continue
        if fact_type == "integration_intent_fact":
            integrations.append(
                _memory_review_item(
                    fact=fact,
                    value=value,
                    source_evidence_by_ref=source_evidence_by_ref,
                )
            )

    for product_ref, product in product_rows.items():
        product["offers"] = _sort_by_ref(offers_by_product.get(product_ref, ()))
        product["media"] = _sort_by_ref(media_by_product.get(product_ref, ()))

    products = sorted(product_rows.values(), key=lambda item: str(item["product_ref"]))
    summary = {
        "products": len(products),
        "knowledge": len(knowledge),
        "rules": len(rules),
        "voice": len(voice),
        "integrations": len(integrations),
        "media": sum(len(items) for items in media_by_product.values()),
        "offers": sum(len(items) for items in offers_by_product.values()),
        "total_review_items": (
            len(products)
            + len(knowledge)
            + len(rules)
            + len(voice)
            + len(integrations)
        ),
    }
    return {
        "schema_version": LEARNED_REVIEW_SCHEMA_VERSION,
        "status": "needs_review" if summary["total_review_items"] else "empty",
        "summary": summary,
        "products": products,
        "knowledge": _sort_by_ref(knowledge),
        "rules": _sort_by_ref(rules),
        "voice": _sort_by_ref(voice),
        "integrations": _sort_by_ref(integrations),
    }


def _product_ref_for_related_fact(*, fact: Any, value: dict[str, Any]) -> str:
    return str(
        value.get("product_ref")
        or getattr(fact, "entity_ref", "")
        or getattr(fact, "fact_id", "")
    )


def _memory_review_item(
    *,
    fact: Any,
    value: dict[str, Any],
    source_evidence_by_ref: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source_refs = list(getattr(fact, "source_refs", []) or [])
    return {
        "fact_id": str(getattr(fact, "fact_id", "")),
        "fact_type": str(getattr(fact, "fact_type", "") or ""),
        "entity_ref": str(getattr(fact, "entity_ref", "") or ""),
        "topic": value.get("topic"),
        "question": value.get("question"),
        "answer": value.get("answer"),
        "summary": value.get("summary"),
        "requirement": value.get("requirement"),
        "rule": value.get("rule"),
        "details": dict(value.get("details") or {}),
        "observations": list(value.get("observations") or []),
        "confidence": float(getattr(fact, "confidence", 0.0) or 0.0),
        "risk_tier": str(getattr(fact, "risk_tier", "") or ""),
        "source_refs": source_refs,
        "source_evidence": _source_evidence_for_refs(
            source_refs=source_refs,
            source_evidence_by_ref=source_evidence_by_ref,
        ),
    }


def _build_source_evidence_by_ref(*, facts: Sequence[Any]) -> dict[str, dict[str, Any]]:
    evidence_by_ref: dict[str, dict[str, Any]] = {}
    for fact in facts:
        if str(getattr(fact, "fact_type", "") or "") != "business_source_fact":
            continue
        fact_id = str(getattr(fact, "fact_id", "") or "")
        source_ref = _source_ref_from_business_source_fact_id(fact_id)
        if not source_ref:
            continue
        value = dict(getattr(fact, "value", {}) or {})
        source_input = dict(value.get("input") or {})
        kind = str(value.get("kind") or source_input.get("kind") or "source")
        evidence_by_ref[source_ref] = {
            "ref": source_ref,
            "kind": kind,
            "label": _source_label(kind=kind, source_input=source_input),
            "detail": _source_detail(kind=kind, source_input=source_input),
        }
    return evidence_by_ref


def _source_ref_from_business_source_fact_id(fact_id: str) -> str | None:
    prefix = "business_source:"
    suffix = ":ingested"
    if fact_id.startswith(prefix) and fact_id.endswith(suffix):
        return fact_id[len(prefix) : -len(suffix)]
    return None


def _source_label(*, kind: str, source_input: dict[str, Any]) -> str:
    if kind == "telegram_channel":
        handle = str(source_input.get("handle") or "").strip()
        return f"Telegram {handle}" if handle else "Telegram kanal"
    if kind == "website":
        url = str(source_input.get("url") or "").strip()
        return _host_from_url(url) or "Sayt"
    if kind in {"file", "screenshot", "voice_note"}:
        return str(
            source_input.get("file_name")
            or source_input.get("label")
            or source_input.get("name")
            or ("Rasm yoki screenshot" if kind == "screenshot" else "Fayl")
        )
    return str(source_input.get("label") or "Manba")


def _source_detail(*, kind: str, source_input: dict[str, Any]) -> str | None:
    if kind == "telegram_channel":
        message_count = source_input.get("message_count")
        if isinstance(message_count, int) and message_count > 0:
            return f"{message_count} ta xabar"
    if kind == "website":
        url = str(source_input.get("url") or "").strip()
        return url or None
    text_preview = str(source_input.get("text") or "").strip()
    return text_preview[:140] if text_preview else None


def _host_from_url(url: str) -> str | None:
    if not url:
        return None
    without_scheme = url.split("://", 1)[-1]
    host = without_scheme.split("/", 1)[0].strip()
    return host or None


def _source_evidence_for_refs(
    *,
    source_refs: Sequence[Any],
    source_evidence_by_ref: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for raw_ref in source_refs:
        ref = str(raw_ref or "")
        parent_ref, unit_label = _source_parent_ref(ref)
        if not parent_ref:
            continue
        source = source_evidence_by_ref.get(parent_ref)
        if not source:
            continue
        key = (parent_ref, unit_label)
        if key in seen:
            continue
        seen.add(key)
        visible.append({
            **source,
            "ref": ref,
            "unit_label": unit_label,
        })
    return visible


def _source_parent_ref(ref: str) -> tuple[str | None, str | None]:
    if ref.startswith("source_unit:business_source:"):
        source_unit_ref = ref.removeprefix("source_unit:business_source:")
        if ":ingested:" in source_unit_ref:
            parent_ref, unit_index = source_unit_ref.split(":ingested:", 1)
            return parent_ref, f"bo‘lak {unit_index}"
        return source_unit_ref, None
    if ref.startswith("brain:source:"):
        return ref, None
    return None, None


def _sort_by_ref(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(item) for item in items),
        key=lambda item: str(
            item.get("fact_id")
            or item.get("offer_ref")
            or item.get("media_ref")
            or item.get("entity_ref")
            or ""
        ),
    )
