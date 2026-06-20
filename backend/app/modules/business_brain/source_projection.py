from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from app.modules.business_brain.contracts import (
    SourceIntakeItem,
    SourceIntakeLifecycle,
    SourceIntakeProjection,
    SourceIntakePurpose,
)
from app.modules.commercial_spine.contracts import BusinessBrainFact
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.source_progress import (
    build_onboarding_source_learning_projection,
)


class SourceIntakeProjectionService:
    """Build the continuous Source Intake read model.

    This intentionally reads existing source facts/projections. It does not own
    source ingestion and it does not mutate Brain truth.
    """

    def __init__(self, *, repository: CommercialSpineRepository) -> None:
        self._repository = repository

    async def projection(self, *, workspace_id: int, limit: int = 250) -> SourceIntakeProjection:
        bounded_limit = max(1, min(int(limit), 250))
        all_facts = await self._repository.list_facts(
            workspace_id=workspace_id,
            limit=bounded_limit,
            statuses=(
                "active",
                "confirmed",
                "proposed",
                "conflicted",
                "degraded",
                "expired",
                "historical",
                "superseded",
                "rejected",
            ),
        )
        source_facts = tuple(fact for fact in all_facts if fact.fact_type == "business_source_fact")
        learning = build_onboarding_source_learning_projection(
            source_facts=source_facts,
            source_learning_projections=await self._repository.list_projections(
                workspace_id=workspace_id,
                projection_type="business_source_learning",
                limit=bounded_limit,
            ),
        )
        source_refs = {_source_ref_for_fact(fact) for fact in source_facts}
        outputs_by_source = _outputs_by_source(all_facts=all_facts, source_refs=source_refs)
        fact_by_ref = {_source_ref_for_fact(fact): fact for fact in source_facts}

        items: list[SourceIntakeItem] = []
        for source in learning.get("sources", []):
            source_ref = str(source.get("source_ref") or "").strip()
            if not source_ref:
                continue
            fact = fact_by_ref.get(source_ref)
            items.append(
                _source_item(source=source, fact=fact, outputs=outputs_by_source.get(source_ref, []))
            )

        counts: Counter[SourceIntakeLifecycle] = Counter(item.lifecycle for item in items)
        kind_counts: Counter[str] = Counter(item.kind for item in items)
        return SourceIntakeProjection(
            workspace_id=workspace_id,
            sources=sorted(items, key=lambda item: item.updated_at, reverse=True),
            counts={
                key: counts.get(key, 0)
                for key in (
                    "live",
                    "snapshot",
                    "learning",
                    "needs_review",
                    "retrying",
                    "failed",
                    "conflicting",
                    "archived",
                )
            },
            kind_counts=dict(kind_counts),
            ready_count=sum(1 for item in items if item.lifecycle in {"live", "snapshot"}),
            review_count=sum(1 for item in items if item.lifecycle in {"needs_review", "conflicting"}),
            failed_count=sum(1 for item in items if item.lifecycle == "failed"),
            live_count=sum(1 for item in items if item.lifecycle == "live"),
        )


def _source_item(
    *,
    source: dict[str, Any],
    fact: BusinessBrainFact | None,
    outputs: list[str],
) -> SourceIntakeItem:
    kind = _normalized_kind(str(source.get("kind") or "source"))
    lifecycle = _lifecycle(source=source, fact=fact, kind=kind)
    purpose = _purpose(source.get("purpose"))
    title = _source_title(source=source, fact=fact, kind=kind)
    issue_label = _issue_label(source=source, lifecycle=lifecycle)
    watchable = kind in {"telegram_channel", "telegram_history", "website"}
    watch_state = _watch_state(fact)
    return SourceIntakeItem(
        source_ref=str(source.get("source_ref") or _source_ref_for_fact(fact)),
        title=title,
        kind=kind,
        kind_label=_kind_label(kind),
        purpose=purpose,
        purpose_label="Javob ma'lumoti" if purpose == "brain_data" else "Agent sozlamasi",
        lifecycle=lifecycle,
        status_label=_lifecycle_label(lifecycle),
        summary=_summary(source=source, lifecycle=lifecycle, issue_label=issue_label),
        preview=_preview(source=source, fact=fact),
        learned_object_count=len(outputs),
        learned_object_labels=outputs[:6],
        source_unit_count=int(source.get("source_unit_count") or 0),
        media_count=int(source.get("source_media_count") or 0),
        issue_label=issue_label,
        retryable=bool(source.get("retryable")) or lifecycle in {"failed", "retrying"},
        can_retry=lifecycle in {"failed", "retrying", "learning", "needs_review"},
        can_archive=lifecycle != "archived",
        can_pause=watchable and lifecycle == "live",
        can_resume=watchable and lifecycle == "snapshot" and watch_state == "paused",
        fact_id=str(source.get("fact_id") or getattr(fact, "fact_id", "") or "") or None,
        updated_at=getattr(fact, "valid_from", None) or datetime.now(UTC),
    )


def _outputs_by_source(
    *,
    all_facts: tuple[BusinessBrainFact, ...],
    source_refs: set[str],
) -> dict[str, list[str]]:
    outputs: dict[str, set[str]] = defaultdict(set)
    for fact in all_facts:
        if fact.fact_type == "business_source_fact":
            continue
        domain = _domain_label_for_fact(fact)
        for source_ref in source_refs:
            if _fact_refs_source(fact, source_ref):
                outputs[source_ref].add(domain)
    return {key: sorted(value, key=_domain_rank) for key, value in outputs.items()}


def _fact_refs_source(fact: BusinessBrainFact, source_ref: str) -> bool:
    refs = {str(ref) for ref in fact.source_refs}
    refs.add(str(fact.entity_ref))
    for ref in refs:
        if ref == source_ref or ref.endswith(source_ref):
            return True
        if ref.startswith(f"{source_ref}/") or ref.startswith(f"{source_ref}:"):
            return True
    return False


def _source_ref_for_fact(fact: BusinessBrainFact | None) -> str:
    if fact is None:
        return "source"
    for ref in fact.source_refs:
        text = str(ref)
        if text.startswith(("onboarding:source:", "brain:source:", "telegram:channel:")):
            return text
    if fact.entity_ref.startswith("workspace:source:"):
        return fact.entity_ref.removeprefix("workspace:source:")
    return fact.fact_id


def _lifecycle(
    *,
    source: dict[str, Any],
    fact: BusinessBrainFact | None,
    kind: str,
) -> SourceIntakeLifecycle:
    status = str(source.get("status") or "").strip().lower()
    fact_status = str(getattr(fact, "status", "") or "").strip().lower()
    if fact_status in {"rejected", "superseded", "historical"}:
        return "archived"
    if _watch_state(fact) in {"paused", "stopped", "snapshot"}:
        return "snapshot"
    if status == "conflict" or fact_status == "conflicted":
        return "conflicting"
    if status == "failed":
        return "failed"
    if status == "retrying":
        return "retrying"
    if status == "needs_review":
        return "needs_review"
    if status in {"learning", "missing"}:
        return "learning"
    if kind in {"telegram_channel", "telegram_history", "website", "integration"}:
        return "live"
    return "snapshot"


def _watch_state(fact: BusinessBrainFact | None) -> str:
    value = getattr(fact, "value", {}) or {}
    processing = value.get("processing") if isinstance(value, dict) else {}
    if not isinstance(processing, dict):
        return ""
    return str(processing.get("watch_state") or "").strip().lower()


def _purpose(value: Any) -> SourceIntakePurpose:
    return "agent_data" if str(value or "").strip().lower() == "agent_data" else "brain_data"


def _normalized_kind(kind: str) -> str:
    value = kind.strip().lower().replace("-", "_")
    if value in {"telegram", "telegram_channel", "channel"}:
        return "telegram_channel"
    if value in {"history", "conversation_history", "telegram_history"}:
        return "telegram_history"
    if value in {"site", "url", "web", "website"}:
        return "website"
    if value in {"audio", "voice", "voice_note"}:
        return "voice"
    if value in {"image", "photo", "screenshot"}:
        return "image"
    if value in {"manual", "text", "markdown"}:
        return "manual"
    if value in {"csv", "xlsx", "pdf", "document", "file"}:
        return "file"
    if value in {"integration", "export"}:
        return "integration"
    return "source"


def _kind_label(kind: str) -> str:
    return {
        "file": "Fayl",
        "website": "Sayt",
        "telegram_channel": "Telegram kanal",
        "telegram_history": "Telegram tarixi",
        "voice": "Ovoz",
        "image": "Rasm",
        "manual": "Qo'lda",
        "integration": "Integratsiya",
        "source": "Dalil",
    }.get(kind, "Dalil")


def _lifecycle_label(lifecycle: SourceIntakeLifecycle) -> str:
    return {
        "live": "Jonli o'qiladi",
        "snapshot": "Nusxa tayyor",
        "learning": "O'qilmoqda",
        "needs_review": "Tasdiq kerak",
        "retrying": "Qayta urinadi",
        "failed": "Yordam kerak",
        "conflicting": "Zid ma'lumot bor",
        "archived": "Arxivda",
    }[lifecycle]


def _source_title(*, source: dict[str, Any], fact: BusinessBrainFact | None, kind: str) -> str:
    raw = str(source.get("label") or "").strip()
    if raw and not _looks_internal(raw) and not _is_generic_source_label(raw):
        return raw
    value = getattr(fact, "value", {}) or {}
    input_value = value.get("input") if isinstance(value, dict) else {}
    if isinstance(input_value, dict):
        for key in ("file_name", "url", "handle", "label"):
            candidate = str(input_value.get(key) or "").strip()
            if candidate and not _looks_internal(candidate) and not _is_generic_source_label(candidate):
                return candidate
    return _kind_label(kind)


def _preview(*, source: dict[str, Any], fact: BusinessBrainFact | None) -> str:
    value = getattr(fact, "value", {}) or {}
    input_value = value.get("input") if isinstance(value, dict) else {}
    if isinstance(input_value, dict):
        for key in ("url", "handle", "file_name", "transcript", "text"):
            candidate = str(input_value.get(key) or "").strip()
            if candidate and not _is_generic_source_label(candidate):
                return _clip(candidate)
    title = str(source.get("label") or "").strip()
    return _clip(title) if title and not _is_generic_source_label(title) else "Dalil tafsiloti o'qilganda shu yerda ko'rinadi."


def _summary(
    *,
    source: dict[str, Any],
    lifecycle: SourceIntakeLifecycle,
    issue_label: str | None,
) -> str:
    if issue_label:
        return issue_label
    units = int(source.get("source_unit_count") or 0)
    media = int(source.get("source_media_count") or 0)
    if lifecycle == "learning":
        return "OQIM bu manbadan ma'lumot ajratmoqda."
    if units or media:
        return f"{units} matn bo'lagi, {media} media dalil topildi."
    return "Saqlangan dalil. O'qish natijalari tayyor bo'lganda shu yerda ko'rinadi."


def _issue_label(*, source: dict[str, Any], lifecycle: SourceIntakeLifecycle) -> str | None:
    if lifecycle == "conflicting":
        return "Bu manbadagi ma'lumot boshqa dalil bilan mos kelmadi."
    if lifecycle == "failed":
        return _plain_problem(source.get("degraded_reasons") or []) or "Dalilni o'qib bo'lmadi."
    if lifecycle == "retrying":
        return "Qayta o'qishga qo'yilgan."
    if lifecycle == "needs_review":
        return "Topilgan ma'lumot egasi tasdig'ini kutyapti."
    return None


def _plain_problem(reasons: list[Any]) -> str | None:
    joined = " ".join(str(reason).lower() for reason in reasons)
    if "fetch" in joined or "download" in joined or "network" in joined:
        return "Manbaga ulanishda muammo bo'ldi."
    if "empty" in joined or "missing" in joined:
        return "Bu manbadan o'qiladigan matn topilmadi."
    if "media" in joined or "image" in joined:
        return "Media o'qishda muammo bo'ldi."
    if joined:
        return "Manbani tekshirish kerak."
    return None


def _domain_label_for_fact(fact: BusinessBrainFact) -> str:
    fact_type = fact.fact_type
    if fact_type.startswith("catalog_"):
        return "Katalog"
    if "rule" in fact_type or "policy" in fact_type:
        return "Qoida"
    if "voice" in fact_type:
        return "Ovoz"
    if "pair" in fact_type or "example" in fact_type:
        return "Namuna"
    if "conflict" in fact_type or fact.status == "conflicted":
        return "Muammo"
    return "Bilim"


def _domain_rank(label: str) -> int:
    return {
        "Katalog": 0,
        "Bilim": 1,
        "Qoida": 2,
        "Ovoz": 3,
        "Namuna": 4,
        "Muammo": 5,
    }.get(label, 99)


def _clip(value: str, limit: int = 180) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _looks_internal(value: str) -> bool:
    return any(
        token in value
        for token in (
            "onboarding:source:",
            "workspace:source:",
            "brain:source:",
            "source_unit:",
            "message:",
            "conversation:",
        )
    )


def _is_generic_source_label(value: str) -> bool:
    return value.strip().lower() in {"manba", "source", "manual", "text", "dalil"}
