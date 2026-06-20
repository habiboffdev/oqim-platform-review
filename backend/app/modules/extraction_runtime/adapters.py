from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from app.modules.extraction_runtime.contracts import (
    ExtractionPart,
    ExtractionRequest,
    ExtractionScope,
    ExtractionSourceKind,
)

BusinessSourceKind = Literal[
    "website",
    "pdf",
    "text",
    "telegram_channel",
    "screenshot",
    "voice_note",
    "spreadsheet",
    "past_conversation",
]

_BUSINESS_SOURCE_TO_EXTRACTION_SOURCE: dict[BusinessSourceKind, ExtractionSourceKind] = {
    "website": "url",
    "pdf": "file",
    "text": "source_bundle",
    "telegram_channel": "telegram_channel",
    "screenshot": "media",
    "voice_note": "media",
    "spreadsheet": "file",
    "past_conversation": "source_bundle",
}


def build_business_source_extraction_request(
    *,
    workspace_id: int,
    source_ref: str,
    source_kind: BusinessSourceKind,
    source_units: Iterable[Any],
    media_assets: list[dict[str, Any]],
    correlation_id: str,
    idempotency_key: str,
    max_source_units: int,
    max_media_assets: int,
) -> ExtractionRequest:
    parts: list[ExtractionPart] = []
    for unit in tuple(source_units)[:max_source_units]:
        unit_ref = str(getattr(unit, "unit_ref", "") or "").strip()
        if not unit_ref:
            continue
        parts.append(
            ExtractionPart(
                kind="text",
                ref=unit_ref,
                payload=_source_unit_payload(unit),
            )
        )
    for asset in media_assets[:max_media_assets]:
        media_ref = str(asset.get("media_ref") or "").strip()
        if not media_ref:
            continue
        parts.append(
            ExtractionPart(
                kind="media_ref",
                ref=media_ref,
                payload=dict(asset),
            )
        )
    return ExtractionRequest(
        scope=ExtractionScope(workspace_id=workspace_id),
        source_kind=_BUSINESS_SOURCE_TO_EXTRACTION_SOURCE[source_kind],
        source_ref=source_ref,
        parts=parts[: max_source_units + max_media_assets],
        profile_refs=business_source_profile_refs(source_kind),
        target_kinds=business_source_target_kinds(source_kind),
        correlation_id=correlation_id,
        idempotency_key=f"{idempotency_key}:universal-extraction",
        max_parallelism=4,
        max_evidence_units=max_source_units + max_media_assets,
        persist_mode="review_candidates",
    )


def business_source_profile_refs(source_kind: BusinessSourceKind) -> list[str]:
    profile_refs = [
        "commerce_generic.v1",
        "generic_kb.v1",
        "seller_voice.v1",
    ]
    if source_kind == "telegram_channel":
        profile_refs.append("telegram_marketplace.v1")
    if source_kind == "past_conversation":
        profile_refs.append("conversation_pairs.v1")
    return profile_refs


def business_source_target_kinds(source_kind: BusinessSourceKind) -> list[str]:
    target_kinds = [
        "catalog_family",
        "kb_entry",
        "seller_rule",
        "voice_observation",
    ]
    if source_kind == "past_conversation":
        target_kinds.append("conversation_pair")
    return target_kinds


def _source_unit_payload(record: Any) -> dict[str, Any]:
    return {
        "unit_ref": str(getattr(record, "unit_ref", "")),
        "source_refs": list(getattr(record, "source_refs", ()) or ()),
        "state": getattr(record, "state", None),
        "embedding_state": getattr(record, "embedding_state", None),
        "degraded_reason": getattr(record, "degraded_reason", None),
        "text": str(getattr(record, "source_text", "") or "")[:6000],
    }


def _chat_turn_payload(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        source = record
        message_ref = source.get("message_ref") or source.get("ref")
        source_refs = source.get("source_refs") or []
        media_semantics = source.get("media_semantics") or {}
        return {
            "message_ref": str(message_ref or ""),
            "sender_type": source.get("sender_type"),
            "created_at": source.get("created_at"),
            "text": str(source.get("content") or source.get("text") or "")[:4000],
            "source_refs": list(source_refs),
            "media_semantics": dict(media_semantics)
            if isinstance(media_semantics, dict)
            else {},
        }

    message_ref = str(getattr(record, "message_ref", "") or getattr(record, "ref", ""))
    source_refs = getattr(record, "source_refs", ()) or ()
    media_semantics = getattr(record, "media_semantics", {}) or {}
    return {
        "message_ref": message_ref,
        "sender_type": getattr(record, "sender_type", None),
        "created_at": getattr(record, "created_at", None),
        "text": str(getattr(record, "content", "") or getattr(record, "text", "") or "")[
            :4000
        ],
        "source_refs": list(source_refs),
        "media_semantics": dict(media_semantics)
        if isinstance(media_semantics, dict)
        else {},
    }
