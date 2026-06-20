from __future__ import annotations

from collections.abc import Sequence
from typing import Any

SOURCE_LEARNING_SCHEMA_VERSION = "onboarding_source_learning.v1"
SOURCE_LEARNING_SUMMARY_KEYS = (
    "learning",
    "learned",
    "needs_review",
    "missing",
    "conflict",
    "retrying",
    "failed",
)


def build_onboarding_source_learning_projection(
    *,
    source_facts: Sequence[Any],
    source_learning_projections: Sequence[Any] = (),
) -> dict[str, Any]:
    """Summarize durable onboarding source facts into owner-facing learning state."""
    sources_by_ref: dict[str, dict[str, Any]] = {}

    for fact in source_facts:
        entity_ref = str(getattr(fact, "entity_ref", "") or "")
        if not entity_ref.startswith("workspace:source:"):
            continue
        value = dict(getattr(fact, "value", {}) or {})
        processing = dict(value.get("processing") or {})
        source_ref = _source_learning_source_ref(fact=fact, value=value)
        projection = _source_learning_projection_for_fact(
            fact=fact,
            source_ref=source_ref,
            source_learning_projections=source_learning_projections,
        )
        status = _source_learning_visible_status(
            fact_status=str(getattr(fact, "status", "") or ""),
            processing_state=str(processing.get("state") or ""),
            learning_projection=projection,
        )
        projection_state = dict(getattr(projection, "state", {}) or {}) if projection else {}
        evidence_summary = dict(projection_state.get("evidence_summary") or {})
        projection_status = str(projection_state.get("status") or "").strip()
        stage = str(projection_state.get("stage") or "").strip()
        purpose = _source_learning_purpose(value=value, projection_state=projection_state)
        catalog_candidate_count = int(projection_state.get("catalog_candidate_count") or 0)
        memory_candidate_count = int(projection_state.get("memory_candidate_count") or 0)
        rejected_candidate_count = int(projection_state.get("rejected_candidate_count") or 0)
        attempt_count = int(projection_state.get("attempt_count") or 0)
        max_attempts = int(projection_state.get("max_attempts") or 0)
        degraded_reasons = list(processing.get("degraded_reasons") or [])
        for reason in list(getattr(projection, "degraded_reasons", []) or []):
            if reason not in degraded_reasons:
                degraded_reasons.append(reason)
        input_value = value.get("input")
        input_label = input_value.get("label") if isinstance(input_value, dict) else ""
        source = {
            "source_ref": source_ref,
            "kind": str(value.get("kind") or "source"),
            "purpose": purpose,
            "label": str(value.get("label") or input_label or ""),
            "status": status,
            "stage": stage or projection_status or str(processing.get("state") or ""),
            "raw_state": projection_status
            or str(processing.get("state") or getattr(fact, "status", "") or ""),
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
            "started_at": str(projection_state.get("started_at") or ""),
            "updated_at": str(projection_state.get("updated_at") or ""),
            "completed_at": str(projection_state.get("completed_at") or ""),
            "input_cache_reused": bool(projection_state.get("input_cache_reused") or False),
            "source_unit_count": int(
                processing.get("source_unit_count")
                or projection_state.get("source_unit_count")
                or evidence_summary.get("source_unit_count")
                or 0
            ),
            "source_media_count": int(
                processing.get("source_media_count")
                or projection_state.get("source_media_count")
                or evidence_summary.get("media_asset_count")
                or 0
            ),
            "catalog_candidate_count": catalog_candidate_count,
            "memory_candidate_count": memory_candidate_count,
            "rejected_candidate_count": rejected_candidate_count,
            "degraded_reasons": degraded_reasons,
            "retryable": status in {"retrying", "failed"},
            "fact_id": str(getattr(fact, "fact_id", "")),
            "entity_ref": entity_ref,
            "source_refs": list(getattr(fact, "source_refs", []) or []),
            "events": [
                dict(event)
                for event in list(projection_state.get("events") or [])
                if isinstance(event, dict)
            ],
        }
        sources_by_ref[source_ref] = _merge_source_learning_source(
            existing=sources_by_ref.get(source_ref),
            incoming=source,
        )

    sources = list(sources_by_ref.values())
    summary = {"total": 0, **{key: 0 for key in SOURCE_LEARNING_SUMMARY_KEYS}}
    for source in sources:
        status = str(source.get("status") or "learning")
        summary["total"] += 1
        summary[status] = summary.get(status, 0) + 1

    sources.sort(key=_source_learning_sort_key)
    return {
        "schema_version": SOURCE_LEARNING_SCHEMA_VERSION,
        "status": _source_learning_overall_status(summary),
        "percent": _source_learning_percent(summary),
        "summary": summary,
        "sources": sources,
        "events": _source_learning_events(sources),
    }


def _merge_source_learning_source(
    *,
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    if existing is None:
        return incoming
    merged = dict(existing)
    if _status_rank(str(incoming.get("status") or "")) >= _status_rank(
        str(existing.get("status") or "")
    ):
        merged["status"] = incoming.get("status")
        merged["stage"] = incoming.get("stage") or existing.get("stage")
        merged["raw_state"] = incoming.get("raw_state") or existing.get("raw_state")
        merged["fact_id"] = incoming.get("fact_id") or existing.get("fact_id")
        merged["entity_ref"] = incoming.get("entity_ref") or existing.get("entity_ref")
    merged["kind"] = (
        existing.get("kind")
        if existing.get("kind") and existing.get("kind") != "source"
        else incoming.get("kind")
    )
    merged["purpose"] = existing.get("purpose") or incoming.get("purpose") or "brain_data"
    merged["label"] = existing.get("label") or incoming.get("label") or ""
    merged["source_unit_count"] = max(
        int(existing.get("source_unit_count") or 0),
        int(incoming.get("source_unit_count") or 0),
    )
    merged["source_media_count"] = max(
        int(existing.get("source_media_count") or 0),
        int(incoming.get("source_media_count") or 0),
    )
    merged["catalog_candidate_count"] = max(
        int(existing.get("catalog_candidate_count") or 0),
        int(incoming.get("catalog_candidate_count") or 0),
    )
    merged["memory_candidate_count"] = max(
        int(existing.get("memory_candidate_count") or 0),
        int(incoming.get("memory_candidate_count") or 0),
    )
    merged["rejected_candidate_count"] = max(
        int(existing.get("rejected_candidate_count") or 0),
        int(incoming.get("rejected_candidate_count") or 0),
    )
    merged["attempt_count"] = max(
        int(existing.get("attempt_count") or 0),
        int(incoming.get("attempt_count") or 0),
    )
    merged["max_attempts"] = max(
        int(existing.get("max_attempts") or 0),
        int(incoming.get("max_attempts") or 0),
    )
    for key in ("started_at", "updated_at", "completed_at"):
        merged[key] = incoming.get(key) or existing.get(key) or ""
    merged["input_cache_reused"] = bool(existing.get("input_cache_reused")) or bool(
        incoming.get("input_cache_reused")
    )
    degraded: list[str] = []
    for reason in list(existing.get("degraded_reasons") or []) + list(
        incoming.get("degraded_reasons") or []
    ):
        if reason not in degraded:
            degraded.append(reason)
    merged["degraded_reasons"] = degraded
    source_refs: list[str] = []
    for ref in list(existing.get("source_refs") or []) + list(
        incoming.get("source_refs") or []
    ):
        if ref not in source_refs:
            source_refs.append(ref)
    merged["source_refs"] = source_refs
    merged["retryable"] = bool(existing.get("retryable")) or bool(incoming.get("retryable"))
    merged["events"] = _merge_source_events(
        list(existing.get("events") or []),
        list(incoming.get("events") or []),
    )
    return merged


def _merge_source_events(
    existing: list[Any],
    incoming: list[Any],
) -> list[dict[str, Any]]:
    by_ref: dict[str, dict[str, Any]] = {}
    fallback_index = 0
    for raw in [*existing, *incoming]:
        if not isinstance(raw, dict):
            continue
        event = dict(raw)
        event_ref = str(event.get("event_ref") or "").strip()
        if not event_ref:
            fallback_index += 1
            event_ref = f"source-learning:event:{fallback_index}"
            event["event_ref"] = event_ref
        by_ref[event_ref] = event
    return sorted(
        by_ref.values(),
        key=lambda event: str(event.get("created_at") or event.get("event_ref") or ""),
    )[-20:]


def _status_rank(status: str) -> int:
    return {
        "learned": 10,
        "missing": 20,
        "learning": 30,
        "needs_review": 40,
        "conflict": 50,
        "retrying": 60,
        "failed": 70,
    }.get(status, 0)


def _source_learning_visible_status(
    *,
    fact_status: str,
    processing_state: str,
    learning_projection: Any | None,
) -> str:
    normalized_fact_status = fact_status.strip().lower()
    normalized_processing = processing_state.strip().lower()
    if normalized_fact_status == "conflicted" or normalized_processing in {"conflict", "conflicted"}:
        return "conflict"
    if normalized_processing in {"missing", "empty"}:
        return "missing"
    if normalized_processing in {"retrying", "retry"}:
        return "retrying"
    if normalized_fact_status == "degraded" or normalized_processing in {"failed", "degraded"}:
        return "failed"
    if learning_projection is not None:
        state = dict(getattr(learning_projection, "state", {}) or {})
        projection_status = str(state.get("status") or "").strip().lower()
        if projection_status in {"retrying", "retry"}:
            return "retrying"
        if projection_status == "failed":
            return "failed"
        if projection_status in {
            "learning",
            "queued",
            "fetching",
            "fetching_telegram",
            "ingesting",
            "using_cache",
            "extracting",
        }:
            return "learning"
        if projection_status in {"conflict", "conflicted"}:
            return "conflict"
        if projection_status in {"missing", "empty"}:
            return "missing"
        candidate_count = int(state.get("catalog_candidate_count") or 0) + int(
            state.get("memory_candidate_count") or 0
        )
        rejected_count = int(state.get("rejected_candidate_count") or 0)
        degraded = bool(getattr(learning_projection, "degraded", False))
        if degraded:
            return "failed"
        if candidate_count > 0:
            return "needs_review"
        if str(state.get("gateway_status") or "").lower() == "ok" and rejected_count == 0:
            return "learned"
    if normalized_processing in {"indexed", "embedded", "learned", "completed", "ready"}:
        return "learned"
    return "learning"


def _source_learning_source_ref(*, fact: Any, value: dict[str, Any]) -> str:
    for ref in list(getattr(fact, "source_refs", []) or []):
        if str(ref).startswith("onboarding:source:"):
            return str(ref)
    entity_ref = str(getattr(fact, "entity_ref", "") or "")
    if entity_ref.startswith("workspace:source:"):
        return entity_ref.removeprefix("workspace:source:")
    return str(value.get("source_ref") or getattr(fact, "fact_id", "") or "source")


def _source_learning_purpose(*, value: dict[str, Any], projection_state: dict[str, Any]) -> str:
    for candidate in (
        projection_state.get("source_purpose"),
        value.get("purpose"),
        (value.get("input") or {}).get("purpose") if isinstance(value.get("input"), dict) else None,
    ):
        normalized = str(candidate or "").strip().lower()
        if normalized in {"brain_data", "agent_data"}:
            return normalized
    return "brain_data"


def _source_learning_projection_for_fact(
    *,
    fact: Any,
    source_ref: str,
    source_learning_projections: Sequence[Any],
) -> Any | None:
    fact_id = str(getattr(fact, "fact_id", "") or "")
    entity_ref = str(getattr(fact, "entity_ref", "") or "")
    source_refs = {str(ref) for ref in list(getattr(fact, "source_refs", []) or [])}
    source_refs.add(source_ref)
    ranked: list[tuple[int, str, Any]] = []
    for projection in source_learning_projections:
        projection_ref = str(getattr(projection, "projection_ref", "") or "")
        projection_entity_ref = str(getattr(projection, "entity_ref", "") or "")
        state = dict(getattr(projection, "state", {}) or {})
        projection_source_ref = str(state.get("source_ref") or "")
        projection_source_fact_id = str(state.get("source_fact_id") or "")
        rank: int | None = None
        if source_ref and projection_ref == f"business_source_learning:{source_ref}":
            rank = 0
        elif projection_source_ref and projection_source_ref == source_ref:
            rank = 1
        elif projection_source_fact_id and projection_source_fact_id == fact_id:
            rank = 2
        elif projection_entity_ref and projection_entity_ref == entity_ref:
            rank = 3
        elif _is_canonical_learning_source_ref(projection_source_ref) and projection_source_ref in source_refs:
            rank = 4
        if rank is not None:
            ranked.append((rank, projection_ref, projection))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked[0][2]


def _is_canonical_learning_source_ref(ref: str) -> bool:
    return ref.startswith("onboarding:source:") or ref.startswith("workspace:source:")


def _source_learning_sort_key(source: dict[str, Any]) -> tuple[int, str]:
    source_ref = str(source.get("source_ref") or "")
    if source_ref.startswith("onboarding:source:"):
        index = source_ref.removeprefix("onboarding:source:")
        if index.isdigit():
            return (int(index), source_ref)
    return (10_000, source_ref)


def _source_learning_overall_status(summary: dict[str, int]) -> str:
    if int(summary.get("total") or 0) == 0:
        return "idle"
    if int(summary.get("learning") or 0) > 0:
        return "learning"
    if int(summary.get("retrying") or 0) > 0:
        return "retrying"
    if int(summary.get("conflict") or 0) > 0:
        return "conflict"
    if int(summary.get("failed") or 0) > 0 or int(summary.get("missing") or 0) > 0:
        return "failed"
    if int(summary.get("needs_review") or 0) > 0:
        return "needs_review"
    return "learned"


def _source_learning_percent(summary: dict[str, int]) -> int:
    total = int(summary.get("total") or 0)
    if total <= 0:
        return 0
    finished = (
        int(summary.get("learned") or 0)
        + int(summary.get("needs_review") or 0)
        + int(summary.get("failed") or 0)
        + int(summary.get("missing") or 0)
        + int(summary.get("conflict") or 0)
    )
    if int(summary.get("learning") or 0) > 0 or int(summary.get("retrying") or 0) > 0:
        return max(20, min(95, int((finished / total) * 100)))
    return 100


def _source_learning_events(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    durable: list[dict[str, Any]] = []
    for source in sources:
        for raw_event in list(source.get("events") or []):
            event = _source_learning_event_from_durable(source=source, raw_event=raw_event)
            if event is not None:
                durable.append(event)
    if durable:
        durable.sort(key=lambda event: str(event.get("created_at") or event["event_ref"]))
        return durable[-12:]
    return [_source_learning_event(source) for source in sources[:8]]


def _source_learning_event_from_durable(
    *,
    source: dict[str, Any],
    raw_event: Any,
) -> dict[str, Any] | None:
    if not isinstance(raw_event, dict):
        return None
    source_ref = str(raw_event.get("source_ref") or source.get("source_ref") or "")
    if not source_ref:
        return None
    kind = str(raw_event.get("kind") or source.get("kind") or "source")
    status = str(raw_event.get("status") or source.get("status") or "learning")
    stage = str(raw_event.get("stage") or source.get("stage") or status)
    event_source = {
        **source,
        "source_ref": source_ref,
        "kind": kind,
        "status": status,
        "stage": stage,
        "attempt_count": int(raw_event.get("attempt_count") or source.get("attempt_count") or 0),
        "max_attempts": int(raw_event.get("max_attempts") or source.get("max_attempts") or 0),
        "degraded_reasons": list(raw_event.get("degraded_reasons") or source.get("degraded_reasons") or []),
        "input_cache_reused": bool(raw_event.get("input_cache_reused") or source.get("input_cache_reused")),
        "source_unit_count": _durable_event_int(raw_event, source, "source_unit_count"),
        "source_media_count": _durable_event_int(raw_event, source, "source_media_count"),
        "catalog_candidate_count": _durable_event_int(raw_event, source, "catalog_candidate_count"),
        "memory_candidate_count": _durable_event_int(raw_event, source, "memory_candidate_count"),
        "rejected_candidate_count": _durable_event_int(raw_event, source, "rejected_candidate_count"),
    }
    event = _source_learning_event(event_source)
    event["event_ref"] = str(raw_event.get("event_ref") or event["event_ref"])
    event["created_at"] = str(raw_event.get("created_at") or "")
    event["stage"] = stage
    return event


def _durable_event_int(
    raw_event: dict[str, Any],
    source: dict[str, Any],
    key: str,
) -> int:
    if key in raw_event:
        return int(raw_event.get(key) or 0)
    return int(source.get(key) or 0)


def _source_learning_event(source: dict[str, Any]) -> dict[str, Any]:
    status = str(source.get("status") or "learning")
    source_ref = str(source.get("source_ref") or source.get("fact_id") or "source")
    kind = str(source.get("kind") or "source")
    purpose = str(source.get("purpose") or "brain_data")
    unit_count = int(source.get("source_unit_count") or 0)
    media_count = int(source.get("source_media_count") or 0)
    catalog_candidate_count = int(source.get("catalog_candidate_count") or 0)
    memory_candidate_count = int(source.get("memory_candidate_count") or 0)
    rejected_candidate_count = int(source.get("rejected_candidate_count") or 0)
    stage = str(source.get("stage") or "")
    attempt_count = int(source.get("attempt_count") or 0)
    max_attempts = int(source.get("max_attempts") or 0)
    input_cache_reused = bool(source.get("input_cache_reused"))
    return {
        "event_ref": f"source-learning:{source_ref}:{status}",
        "source_ref": source_ref,
        "kind": kind,
        "status": status,
        "stage": stage or status,
        "source_unit_count": unit_count,
        "source_media_count": media_count,
        "catalog_candidate_count": catalog_candidate_count,
        "memory_candidate_count": memory_candidate_count,
        "rejected_candidate_count": rejected_candidate_count,
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
        "input_cache_reused": input_cache_reused,
        "title_uz": _source_learning_event_title(
            status=status,
            stage=stage,
            kind=kind,
            label=str(source.get("label") or ""),
            purpose=purpose,
        ),
        "detail_uz": _source_learning_event_detail(
            status=status,
            stage=stage,
            kind=kind,
            purpose=purpose,
            unit_count=unit_count,
            media_count=media_count,
            catalog_candidate_count=catalog_candidate_count,
            memory_candidate_count=memory_candidate_count,
            rejected_candidate_count=rejected_candidate_count,
            degraded_reasons=list(source.get("degraded_reasons") or []),
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            input_cache_reused=bool(source.get("input_cache_reused")),
        ),
    }


def _source_learning_event_title(
    *,
    status: str,
    stage: str,
    kind: str,
    label: str,
    purpose: str,
) -> str:
    source = label.strip() if label.strip() and label.strip().lower() != "manba" else _source_kind_label(kind)
    is_agent_data = purpose == "agent_data"
    if status in {"queued", "learning"}:
        if is_agent_data:
            if stage == "queued":
                return f"Agent manbasi navbatga qo‘yildi: {source}"
            if stage == "ingesting":
                return f"Agent dalillari ajratilmoqda: {source}"
            if stage == "using_cache":
                return f"Agent uchun saqlangan dalil ishlatilmoqda: {source}"
            if stage == "extracting":
                return f"Agent sozlamasi ajratilmoqda: {source}"
        if stage == "fetching_telegram":
            return f"Telegramdan o‘qilmoqda: {source}"
        if stage == "queued":
            return f"Navbatga qo‘yildi: {source}"
        if stage == "ingesting":
            return f"Dalillar ajratilmoqda: {source}"
        if stage == "using_cache":
            return f"Saqlangan dalil ishlatilmoqda: {source}"
        if stage == "extracting":
            return f"Katalog va bilim qidirilmoqda: {source}"
    if status in {"failed", "missing"}:
        return f"Qayta tekshirish kerak: {source}"
    if status in {"retrying"}:
        return f"Qayta urinilmoqda: {source}"
    if status in {"conflict"}:
        return f"Konflikt topildi: {source}"
    if status in {"needs_review", "review_ready"}:
        if is_agent_data:
            return f"Agent taklifi tasdiq kutmoqda: {source}"
        return f"Tasdiq kutmoqda: {source}"
    if status in {"learned", "done", "ready"}:
        if is_agent_data:
            return f"Agent manbasi o‘rganildi: {source}"
        return f"O‘rganildi: {source}"
    return f"O‘qilmoqda: {source}"


def _source_learning_event_detail(
    *,
    status: str,
    stage: str,
    kind: str,
    purpose: str,
    unit_count: int,
    media_count: int,
    catalog_candidate_count: int,
    memory_candidate_count: int,
    rejected_candidate_count: int,
    degraded_reasons: list[str],
    attempt_count: int,
    max_attempts: int,
    input_cache_reused: bool,
) -> str:
    attempt = (
        f"{attempt_count}/{max_attempts}-urinish"
        if attempt_count > 0 and max_attempts > 0
        else ""
    )
    is_agent_data = purpose == "agent_data"
    if degraded_reasons:
        reason = _source_learning_reason_label(str(degraded_reasons[0]))
        return f"{reason} · {attempt}" if attempt else reason
    if status == "retrying" and attempt:
        return f"Muammo bo‘ldi, OQIM yana urinadi · {attempt}"
    if status in {"queued", "learning"}:
        if stage == "queued":
            if is_agent_data:
                return "Agent manbasi saqlandi. OQIM uni AGENT.md, SKILL.md, ruxsat va yozish uslubiga ajratadi."
            return "Manba saqlandi. OQIM uni alohida dalil sifatida o‘qishni boshlaydi."
        if stage == "fetching_telegram":
            if is_agent_data:
                return "Kanal postlari agent qoidalari va yozish uslubi uchun belgilangan sana bo‘yicha olinmoqda."
            return "Kanal postlari va media dalillar belgilangan sana bo‘yicha olinmoqda."
        if stage == "ingesting":
            if is_agent_data:
                return "Agent manbasi matn, fayl va media dalillarga bo‘linmoqda."
            return "Manba matn, fayl va media dalillarga bo‘linmoqda."
        if stage == "using_cache" or input_cache_reused:
            if is_agent_data:
                return "Oldin saqlangan agent dalillari qayta ishlatilib, AGENT.md tezroq yangilanmoqda."
            return "Oldin saqlangan dalillar qayta ishlatilib, natija tezroq yangilanmoqda."
        if stage == "extracting":
            evidence: list[str] = []
            if unit_count > 0:
                evidence.append(f"{unit_count} ta dalil")
            if media_count > 0:
                evidence.append(f"{media_count} ta media")
            if evidence:
                if is_agent_data:
                    return f"{' · '.join(evidence)} tayyor. Endi AGENT.md, SKILL.md, qoidalar va yozish uslubi ajratilmoqda."
                return f"{' · '.join(evidence)} tayyor. Endi katalog, bilim va qoidalar ajratilmoqda."
            if is_agent_data:
                return "AGENT.md, SKILL.md, ruxsat qoidalari va yozish uslubi uchun takliflar ajratilmoqda."
            return "Katalog, bilim, qoida va yozish uslubi uchun takliflar ajratilmoqda."
    pieces: list[str] = []
    if catalog_candidate_count > 0:
        pieces.append(f"{catalog_candidate_count} ta katalog taklifi")
    if memory_candidate_count > 0:
        pieces.append(
            f"{memory_candidate_count} ta agent taklifi"
            if is_agent_data
            else f"{memory_candidate_count} ta bilim taklifi"
        )
    if unit_count > 0:
        pieces.append(f"{unit_count} ta dalil")
    if media_count > 0:
        pieces.append(f"{media_count} ta media")
    if rejected_candidate_count > 0:
        pieces.append(f"{rejected_candidate_count} ta ishonchsiz taklif ajratildi")
    if status in {"needs_review", "review_ready"}:
        pieces.append("tasdiq kutmoqda")
    if pieces:
        return " · ".join(pieces)
    if is_agent_data:
        if kind in {"file", "pdf", "spreadsheet"}:
            return "Fayldan AGENT.md, SKILL.md, ruxsat va yozish uslubi ajratilmoqda."
        if kind == "website":
            return "Saytdan agent qoidalari va ruxsat chegaralari ajratilmoqda."
        if kind == "telegram_channel":
            return "Kanal postlaridan agent ishlash uslubi va qoidalari ajratilmoqda."
        return "Agent sozlamasi uchun qoida, ruxsat va yozish uslubi ajratilmoqda."
    if kind == "telegram_channel":
        return "Kanal postlari, matn va media dalillar ajratilmoqda."
    if kind in {"file", "pdf", "spreadsheet"}:
        return "Fayldan katalog, fakt va qoidalar ajratilmoqda."
    if kind == "website":
        return "Saytdan sahifa matni va mahsulot dalillari ajratilmoqda."
    return "Brain uchun dalil va qoidalar ajratilmoqda."


def _source_kind_label(kind: str) -> str:
    return {
        "telegram_channel": "Telegram kanal",
        "website": "Sayt",
        "file": "Fayl",
        "pdf": "PDF",
        "spreadsheet": "Jadval",
        "screenshot": "Rasm yoki skrinshot",
        "text": "Qo‘lda yozilgan matn",
        "voice_note": "Audio matni",
    }.get(kind, "Biznes manbasi")


def _source_learning_reason_label(reason: str) -> str:
    normalized = reason.strip().lower()
    if "rate" in normalized or "429" in normalized:
        return "Provider band. OQIM keyinroq qayta urinishi mumkin."
    if "telegram" in normalized:
        return "Telegram manbasini o‘qishda muammo bo‘ldi."
    if "empty" in normalized or "missing" in normalized:
        return "Bu manbadan yetarli dalil topilmadi."
    return "Manbani o‘qishda muammo bo‘ldi. Qayta urinish mumkin."
