from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message
from app.modules.business_brain.contracts import (
    BrainObjectDomain,
    BrainObjectEvidence,
    BrainObjectEvidenceKind,
    BrainObjectItem,
    BrainObjectProjection,
    BrainObjectSourceLifecycle,
    BrainObjectState,
)
from app.modules.commercial_spine.contracts import (
    BusinessBrainFact,
    CommercialActionProposal,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository


@dataclass(frozen=True)
class _EvidenceInfo:
    label: str
    kind: BrainObjectEvidenceKind
    detail: str | None = None
    unit_label: str | None = None


class BrainObjectProjectionService:
    """Build the object-first Brain read model used by the workbench UI."""

    def __init__(self, *, repository: CommercialSpineRepository) -> None:
        self._repository = repository

    async def projection(
        self,
        *,
        workspace_id: int,
        domain: BrainObjectDomain | None = None,
        limit: int = 100,
    ) -> BrainObjectProjection:
        bounded_limit = max(1, min(int(limit), 250))
        objects: list[BrainObjectItem] = []

        facts = await self._repository.list_facts(
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
            ),
        )
        proposals = await self._repository.list_action_proposals(
            workspace_id=workspace_id,
            lifecycle_states=("proposed", "waiting_approval", "blocked", "failed"),
            limit=100,
        )
        evidence_lookup = await self._evidence_lookup(workspace_id=workspace_id, facts=facts)
        for fact in facts:
            if fact.fact_type.startswith("catalog_") and fact.fact_type != "catalog_conflict":
                item = _catalog_fact_object(
                    fact,
                    proposals=proposals,
                    evidence_lookup=evidence_lookup,
                )
                if item is not None and domain in (None, "catalog"):
                    objects.append(item)
                continue
            if _is_empty_fact(fact):
                continue
            item = _fact_object(fact, proposals=proposals, evidence_lookup=evidence_lookup)
            if item is None:
                continue
            if domain is None or item.domain == domain:
                objects.append(item)

        objects = _dedupe_objects(objects)
        counts: dict[BrainObjectDomain, int] = {
            key: 0
            for key in ("catalog", "knowledge", "rules", "voice", "examples", "issues", "sources")
        }
        for item in objects:
            counts[item.domain] += 1
        return BrainObjectProjection(
            workspace_id=workspace_id,
            objects=objects,
            counts=counts,
            issues_count=sum(1 for item in objects if item.domain == "issues" or item.status in {"conflict", "degraded"}),
            ready_count=sum(1 for item in objects if item.status == "ready"),
            review_count=sum(1 for item in objects if item.needs_review),
        )

    async def _evidence_lookup(
        self,
        *,
        workspace_id: int,
        facts: tuple[BusinessBrainFact, ...],
    ) -> dict[str, _EvidenceInfo]:
        source_facts = await self._repository.list_facts(
            workspace_id=workspace_id,
            fact_type="business_source_fact",
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
            limit=250,
        )
        lookup = _source_evidence_lookup((*facts, *source_facts))
        message_ids, conversation_ids = _message_and_conversation_ids(facts)
        if message_ids:
            rows = (
                await self._repository.session.execute(
                    select(Message, Conversation, Customer)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .join(Customer, Conversation.customer_id == Customer.id)
                    .where(
                        Conversation.workspace_id == workspace_id,
                        Message.id.in_(message_ids),
                    )
                )
            ).all()
            for message, conversation, customer in rows:
                snippet = _trim(_text(message.content or message.media_description or message.transcription), 54)
                author = "Ega" if message.sender_type in {"seller", "ai"} else customer.display_name
                detail = f"{author}: {snippet}" if snippet else "Oldingi suhbatdan olingan dalil."
                lookup[f"message:{message.id}"] = _EvidenceInfo(
                    label=f"Suhbat: {customer.display_name}",
                    kind="conversation",
                    detail=detail,
                )
                lookup[f"message:{message.id}:media"] = _EvidenceInfo(
                    label=f"Media: {customer.display_name}",
                    kind="conversation",
                    detail=detail,
                )
                lookup[f"conversation:{conversation.id}:messages"] = _EvidenceInfo(
                    label=f"Suhbat: {customer.display_name}",
                    kind="conversation",
                    detail="Oldingi suhbatlardan olingan namuna.",
                )
        if conversation_ids:
            rows = (
                await self._repository.session.execute(
                    select(Conversation, Customer)
                    .join(Customer, Conversation.customer_id == Customer.id)
                    .where(
                        Conversation.workspace_id == workspace_id,
                        Conversation.id.in_(conversation_ids),
                    )
                )
            ).all()
            for conversation, customer in rows:
                info = _EvidenceInfo(
                    label=f"Suhbat: {customer.display_name}",
                    kind="conversation",
                    detail="Oldingi suhbatlardan olingan dalil.",
                )
                lookup[f"conversation:{conversation.id}"] = info
                lookup[f"conversation:{conversation.id}:messages"] = info
        return lookup


def _catalog_fact_object(
    fact: BusinessBrainFact,
    *,
    proposals: tuple[CommercialActionProposal, ...],
    evidence_lookup: dict[str, _EvidenceInfo] | None = None,
) -> BrainObjectItem | None:
    if _is_empty_fact(fact):
        return None
    status = _state_for_fact(fact)
    value = fact.value
    title = _text(value.get("title"), _text(value.get("name"), "Katalog obyekti"))
    summary = _trim(
        _text(value.get("description") or value.get("summary"), "Katalogdagi mahsulot yoki xizmat."),
        180,
    )
    return BrainObjectItem(
        object_id=fact.fact_id,
        domain="catalog",
        title=title,
        summary=summary,
        status=status,
        status_label=_status_label(status),
        confidence=fact.confidence,
        risk_tier=fact.risk_tier,
        source_lifecycle=_source_lifecycle(fact),
        evidence=_evidence(fact.source_refs, updated_at=fact.valid_from, evidence_lookup=evidence_lookup),
        evidence_count=len(fact.source_refs),
        updated_at=fact.valid_from,
        needs_review=status in {"needs_review", "conflict", "degraded"},
        fact_ids=[fact.fact_id],
        proposal_refs=_proposal_refs(fact.fact_id, proposals),
    )


def _fact_object(
    fact: BusinessBrainFact,
    *,
    proposals: tuple[CommercialActionProposal, ...],
    evidence_lookup: dict[str, _EvidenceInfo] | None = None,
) -> BrainObjectItem | None:
    domain = _domain_for_fact(fact)
    if domain is None:
        return None
    status = _state_for_fact(fact)
    title = _title_for_fact(fact, domain)
    summary = _summary_for_fact(fact, domain)
    return BrainObjectItem(
        object_id=fact.fact_id,
        domain=domain,
        title=title,
        summary=summary,
        status=status,
        status_label=_status_label(status),
        confidence=fact.confidence,
        risk_tier=fact.risk_tier,
        source_lifecycle=_source_lifecycle(fact),
        evidence=_evidence(fact.source_refs, updated_at=fact.valid_from, evidence_lookup=evidence_lookup),
        evidence_count=len(fact.source_refs),
        updated_at=fact.valid_from,
        can_edit=domain != "sources",
        can_archive=True,
        needs_review=status in {"needs_review", "conflict", "degraded"} or fact.risk_tier in {"high", "critical"},
        fact_ids=[fact.fact_id],
        proposal_refs=_proposal_refs(fact.fact_id, proposals),
    )


def _dedupe_objects(items: list[BrainObjectItem]) -> list[BrainObjectItem]:
    seen: set[str] = set()
    result: list[BrainObjectItem] = []
    for item in sorted(items, key=lambda value: value.updated_at, reverse=True):
        if item.object_id in seen:
            continue
        seen.add(item.object_id)
        result.append(item)
    return result


def _domain_for_fact(fact: BusinessBrainFact) -> BrainObjectDomain | None:
    fact_type = fact.fact_type
    if fact_type in {"business_source_fact", "business_source_media_fact", "media_evidence"}:
        return "sources"
    if fact_type in {"business_profile_fact", "operating_preference_fact"}:
        return "knowledge"
    if "conflict" in fact_type or fact.status == "conflicted":
        return "issues"
    if "voice" in fact_type:
        return "voice"
    if "pair" in fact_type or "example" in fact_type:
        return "examples"
    if "rule" in fact_type or "policy" in fact_type:
        return "rules"
    if fact_type == "knowledge_fact":
        return "knowledge"
    return None


def _state_for_fact(fact: BusinessBrainFact) -> BrainObjectState:
    if fact.status in {"rejected", "superseded", "historical", "expired"}:
        return "archived"
    if fact.status == "conflicted":
        return "conflict"
    if fact.status == "degraded":
        return "degraded"
    if fact.status == "proposed" or fact.risk_tier in {"high", "critical"}:
        return "needs_review"
    processing = fact.value.get("processing")
    if isinstance(processing, dict):
        state = str(processing.get("state") or "")
        if state in {"failed", "retrying"}:
            return "degraded"
        if state in {"queued", "learning", "review_ready"}:
            return "needs_review"
    return "ready"


def _source_lifecycle(fact: BusinessBrainFact) -> BrainObjectSourceLifecycle:
    if fact.status in {"historical", "expired"}:
        return "expired"
    if fact.status in {"rejected", "superseded"}:
        return "archived"
    if fact.status == "conflicted":
        return "conflicting"
    processing = fact.value.get("processing")
    if isinstance(processing, dict):
        state = str(processing.get("state") or "")
        if state == "failed":
            return "failed"
        if state == "retrying":
            return "retrying"
    return "live"


def _title_for_fact(fact: BusinessBrainFact, domain: BrainObjectDomain) -> str:
    value = fact.value
    if fact.fact_type == "business_source_fact":
        return _source_fact_label(value)
    if fact.fact_type == "business_profile_fact":
        return "Biznes profili"
    if fact.fact_type == "operating_preference_fact":
        return "Agent ishlash sozlamalari"
    if fact.fact_type == "business_source_media_fact":
        return _media_title(value)
    if fact.fact_type == "media_evidence":
        return _trim(f"Mijoz yuborgan media: {_text((value.get('evidence') or {}).get('summary'))}", 96)
    if fact.fact_type == "conversation_pair_fact":
        return _trim(f"Mijoz: {_text(value.get('customer_turn'), 'Suhbat namunasi')}", 96)
    candidates = (
        value.get("title"),
        value.get("topic"),
        value.get("question"),
        value.get("label"),
        value.get("rule"),
        value.get("name"),
        value.get("answer"),
        value.get("summary"),
        value.get("content"),
        value.get("text_preview"),
    )
    for candidate in candidates:
        text = _text(candidate)
        if text:
            return _trim(text, 96)
    fallback: dict[BrainObjectDomain, str] = {
        "catalog": "Katalog obyekti",
        "knowledge": "Bilim",
        "rules": "Qoida",
        "voice": "Yozish uslubi",
        "examples": "Suhbat namunasi",
        "issues": "Muammo",
        "sources": _source_title_from_fact(fact),
    }
    return fallback[domain]


def _summary_for_fact(fact: BusinessBrainFact, domain: BrainObjectDomain) -> str:
    value = fact.value
    if fact.fact_type == "business_source_fact":
        return _source_fact_summary(value)
    if fact.fact_type == "business_profile_fact":
        return _business_profile_summary(value)
    if fact.fact_type == "operating_preference_fact":
        return _operating_preference_summary(value)
    if fact.fact_type == "business_source_media_fact":
        return _media_summary(value)
    if fact.fact_type == "media_evidence":
        evidence = value.get("evidence") if isinstance(value.get("evidence"), dict) else {}
        return _trim(_text(evidence.get("summary") or value.get("normalized_text"), "Media dalili topilgan."), 180)
    if fact.fact_type == "conversation_pair_fact":
        seller = _text(value.get("seller_turn"))
        return _trim(f"Javob namunasi: {seller}", 180) if seller else "Oldingi suhbatdan javob namunasi."
    candidates = (
        value.get("answer"),
        value.get("summary"),
        value.get("description"),
        value.get("content"),
        value.get("text_preview"),
        value.get("text"),
    )
    for candidate in candidates:
        text = _text(candidate)
        if text:
            return _trim(_clean_source_prefix(text), 180)
    if domain == "sources":
        summary = _source_summary_from_fact(fact)
        if summary:
            return summary
        processing = value.get("processing")
        if isinstance(processing, dict):
            state = str(processing.get("state") or "")
            if state == "failed":
                return "O‘qish tugamadi. Qayta urinish mumkin; eski natija agentga tayyor deb berilmaydi."
            if state == "queued":
                return "Navbatda turibdi. OQIM o‘qishni boshlaganda dalillar shu yerda ko‘rinadi."
    return {
        "catalog": "Mahsulot yoki xizmat haqida tuzilgan ma’lumot.",
        "knowledge": "Agent javob berishda ishlatadigan tasdiqlangan bilim.",
        "rules": "Agent qanday ishlashini belgilaydigan qoida.",
        "voice": "Sotuvchi ohangi va yozish uslubi bo‘yicha namuna.",
        "examples": "Oldingi suhbatlardan o‘rganilgan namuna.",
        "issues": "To‘g‘rilash yoki tanlash kerak bo‘lgan muammo.",
        "sources": "Saqlangan dalil manbasi. O‘qish yakunlanganda topilgan obyektlar alohida ko‘rinadi.",
    }[domain]


def _evidence(
    source_refs: list[str],
    *,
    updated_at: datetime | None = None,
    evidence_lookup: dict[str, _EvidenceInfo] | None = None,
) -> list[BrainObjectEvidence]:
    visible: list[BrainObjectEvidence] = []
    seen: set[tuple[str, str | None]] = set()
    for ref in source_refs[:6]:
        info = _source_info(ref, evidence_lookup=evidence_lookup)
        key = (info.label, info.unit_label)
        if key in seen:
            continue
        seen.add(key)
        visible.append(
            BrainObjectEvidence(
                label=info.label,
                kind=info.kind,
                freshness_label=_freshness_label(updated_at),
                detail=_optional_text(info.detail),
                unit_label=_optional_text(info.unit_label),
                source_ref=ref,
            )
        )
    return visible


def _source_kind(ref: str) -> BrainObjectEvidenceKind:
    lowered = ref.lower()
    if "telegram" in lowered or "channel" in lowered:
        return "telegram"
    if "pdf" in lowered or "file" in lowered or "csv" in lowered or "xlsx" in lowered:
        return "file"
    if "http" in lowered or "website" in lowered or "site" in lowered:
        return "website"
    if "conversation" in lowered or "message" in lowered or "chat" in lowered:
        return "conversation"
    if "integration" in lowered or "crm" in lowered or "pos" in lowered:
        return "integration"
    if "owner" in lowered or "manual" in lowered:
        return "manual"
    return "source"


def _source_info(
    ref: str,
    *,
    evidence_lookup: dict[str, _EvidenceInfo] | None = None,
) -> _EvidenceInfo:
    unit_label = _source_unit_label(ref)
    if evidence_lookup:
        if ref in evidence_lookup:
            return _with_unit_label(evidence_lookup[ref], unit_label)
        for key in _source_parent_ref_candidates(ref):
            if key in evidence_lookup:
                return _with_unit_label(evidence_lookup[key], unit_label)
        for key, info in evidence_lookup.items():
            if key and key in ref:
                return _with_unit_label(info, unit_label)
    kind = _source_kind(ref)
    if kind == "telegram":
        return _telegram_source_info(ref, unit_label=unit_label)
    if kind == "file":
        return _EvidenceInfo(
            label="Fayl dalili",
            kind="file",
            detail=_optional_text(ref.rsplit(":", maxsplit=1)[-1]),
            unit_label=unit_label,
        )
    if kind == "website":
        return _EvidenceInfo(label="Sayt dalili", kind="website", detail=_website_label(ref), unit_label=unit_label)
    if kind == "conversation":
        return _EvidenceInfo(
            label=_conversation_ref_label(ref),
            kind="conversation",
            detail="Oldingi suhbatdan olingan dalil.",
            unit_label=unit_label,
        )
    if kind == "manual":
        return _EvidenceInfo(label="Qo‘lda yozilgan ma’lumot", kind="manual", unit_label=unit_label)
    if kind == "integration":
        return _EvidenceInfo(label="Integratsiya dalili", kind="integration", unit_label=unit_label)
    ref_label = _source_ref_label(ref)
    if ref_label:
        return _EvidenceInfo(
            label=ref_label,
            kind="source",
            detail="Shu dalil manbasi orqali topilgan.",
            unit_label=unit_label,
        )
    return _EvidenceInfo(
        label="Dalil manbasi",
        kind="source",
        detail="Manba nomi topilmadi; agent buni tayyor bilim sifatida ishlatmaydi.",
        unit_label=unit_label,
    )


def _with_unit_label(info: _EvidenceInfo, unit_label: str | None) -> _EvidenceInfo:
    if not unit_label or info.unit_label:
        return info
    return _EvidenceInfo(
        label=info.label,
        kind=info.kind,
        detail=info.detail,
        unit_label=unit_label,
    )


def _source_unit_label(ref: str) -> str | None:
    return "matn bo‘lagi" if ref.startswith("source_unit:") else None


def _source_parent_ref_candidates(ref: str) -> list[str]:
    source_unit_ref = ref.removeprefix("source_unit:") if ref.startswith("source_unit:") else ref
    candidates = [source_unit_ref]
    parent_ref = source_unit_ref
    if ":ingested:" in source_unit_ref:
        parent_ref, _unit_index = source_unit_ref.split(":ingested:", 1)
        candidates.append(parent_ref)
    if ":" in source_unit_ref:
        candidates.append(source_unit_ref.rsplit(":", maxsplit=1)[0])
    if parent_ref.startswith("business_source:"):
        without_prefix = parent_ref.removeprefix("business_source:")
        if without_prefix.endswith(":ingested"):
            without_prefix = without_prefix.removesuffix(":ingested")
        if ":ingested:" in without_prefix:
            without_prefix = without_prefix.split(":ingested:", 1)[0]
        if without_prefix:
            candidates.extend([
                without_prefix,
                f"workspace:source:{without_prefix}",
                f"business_source:{without_prefix}:ingested",
            ])
    if parent_ref.startswith("workspace:source:"):
        candidates.append(parent_ref.removeprefix("workspace:source:"))
    return [candidate for candidate in _unique(candidates) if candidate]


def _source_ref_label(ref: str) -> str | None:
    normalized = ref.strip()
    if not normalized:
        return None
    source_ref = normalized.removeprefix("source_unit:")
    if source_ref.startswith("business_source:"):
        source_ref = source_ref.removeprefix("business_source:")
    if source_ref.startswith("workspace:source:"):
        source_ref = source_ref.removeprefix("workspace:source:")
    if ":ingested:" in source_ref:
        source_ref = source_ref.split(":ingested:", 1)[0]
    if source_ref.endswith(":ingested"):
        source_ref = source_ref.removesuffix(":ingested")
    if source_ref.startswith("onboarding:source:"):
        name = _human_source_ref_name(source_ref.removeprefix("onboarding:source:"))
        return f"Onboarding dalili: {name}" if name else "Onboarding dalili"
    if source_ref.startswith("brain:source:"):
        parts = source_ref.split(":")
        name = _human_source_ref_name(parts[-1] if len(parts) > 2 else source_ref)
        return f"O‘qilgan dalil: {name}" if name else "O‘qilgan dalil"
    return None


def _human_source_ref_name(value: str) -> str:
    text = value.strip().strip(":")
    if not text or text.lower() in {"source", "manba"}:
        return ""
    return _trim(text.replace("_", " ").replace("-", " "), 64)


def _telegram_source_info(ref: str, *, unit_label: str | None) -> _EvidenceInfo:
    handle = _segment_after_marker(ref, ("telegram:channel:", "channel:"))
    label = f"Telegram: {handle}" if handle else "Telegram kanal"
    return _EvidenceInfo(
        label=label,
        kind="telegram",
        detail="Kanal yoki chatdan olingan dalil.",
        unit_label=unit_label,
    )


def _status_label(status: BrainObjectState) -> str:
    return {
        "ready": "Agentga tayyor",
        "needs_review": "Tasdiq kerak",
        "conflict": "Muammo bor",
        "degraded": "Yordam kerak",
        "archived": "Arxivda",
    }[status]


def _proposal_refs(
    object_ref: str,
    proposals: tuple[CommercialActionProposal, ...],
) -> list[str]:
    refs: list[str] = []
    for proposal in proposals:
        payload_refs = {
            str(value)
            for value in proposal.payload.values()
            if isinstance(value, str)
        }
        if object_ref in payload_refs or object_ref in proposal.source_refs:
            refs.append(proposal.proposal_id)
    return refs


def _freshness_label(updated_at: datetime | None) -> str:
    if updated_at is None:
        return "Dalil bor"
    age_seconds = max(0, int((datetime.now(UTC) - updated_at).total_seconds()))
    day = 24 * 60 * 60
    if age_seconds < day:
        return "Bugun"
    days = age_seconds // day
    if days < 30:
        return f"{days} kun oldin"
    months = max(1, days // 30)
    return f"{months} oy oldin"


def _text(value: Any, fallback: str = "") -> str:
    return str(value).strip() if value is not None and str(value).strip() else fallback


def _optional_text(value: str | None) -> str | None:
    text = _text(value)
    return text or None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _trim(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1].rstrip()}…"


def _clean_source_prefix(value: str) -> str:
    text = value.strip()
    if ":" in text and text.lower().startswith(("onboarding:", "brain:", "workspace:")):
        return text.split(":", maxsplit=2)[-1].strip()
    return text


def _is_empty_fact(fact: BusinessBrainFact) -> bool:
    if fact.fact_type == "seller_rule_fact":
        return not _text(fact.value.get("notes") or fact.value.get("rule") or fact.value.get("content"))
    return False


def _source_evidence_lookup(facts: tuple[BusinessBrainFact, ...]) -> dict[str, _EvidenceInfo]:
    lookup: dict[str, _EvidenceInfo] = {}
    for fact in facts:
        if fact.fact_type != "business_source_fact":
            continue
        value = fact.value
        info = _EvidenceInfo(
            label=_source_fact_label(value),
            kind=_source_kind_from_source_value(value),
            detail=_source_fact_summary(value),
        )
        keys = {fact.fact_id, fact.entity_ref}
        if fact.entity_ref.startswith("workspace:source:"):
            keys.add(fact.entity_ref.removeprefix("workspace:source:"))
        if fact.fact_id.startswith("business_source:") and fact.fact_id.endswith(":ingested"):
            keys.add(fact.fact_id.removeprefix("business_source:").removesuffix(":ingested"))
        keys.update(str(ref) for ref in fact.source_refs)
        for key in keys:
            if key:
                lookup[key] = info
    return lookup


def _source_kind_from_source_value(value: dict[str, Any]) -> BrainObjectEvidenceKind:
    kind = _normalized_source_kind(value)
    if kind == "telegram_channel":
        return "telegram"
    if kind in {"file", "pdf", "spreadsheet"}:
        return "file"
    if kind == "website":
        return "website"
    if kind in {"manual", "text", "markdown", "voice", "voice_note", "audio"}:
        return "manual"
    return "source"


def _source_fact_label(value: dict[str, Any]) -> str:
    kind = _normalized_source_kind(value)
    raw_input = value.get("input") if isinstance(value.get("input"), dict) else {}
    label = _text(value.get("label"))
    if kind == "website":
        return f"Sayt: {_website_label(_text(raw_input.get('url') or label, 'URL'))}"
    if kind == "telegram_channel":
        return f"Telegram: {_text(raw_input.get('handle') or label, 'kanal')}"
    if kind in {"voice", "voice_note", "audio"}:
        return f"Audio matni: {_safe_source_name(_text(raw_input.get('file_name') or label), 'yozuv')}"
    if kind in {"image", "screenshot"}:
        return f"Rasm: {_safe_source_name(_text(raw_input.get('file_name') or label), 'media')}"
    if kind in {"file", "pdf", "spreadsheet"}:
        return f"Fayl: {_safe_source_name(_text(raw_input.get('file_name') or label), 'hujjat')}"
    if kind in {"manual", "text", "markdown"}:
        return _safe_source_name(label, "Qo‘lda yozilgan ma’lumot")
    if raw_input:
        for key in ("url", "handle", "file_name", "text", "transcript"):
            text = _text(raw_input.get(key))
            if text and text.lower() not in {"manba", "source", "manual", "text"}:
                return f"Dalil: {_trim(_clean_source_prefix(text), 64)}"
    return _trim(_safe_source_name(label, "Dalil manbasi"), 64)


def _source_fact_summary(value: dict[str, Any]) -> str:
    raw_input = value.get("input") if isinstance(value.get("input"), dict) else {}
    processing = value.get("processing")
    if isinstance(processing, dict):
        parts: list[str] = []
        unit_count = processing.get("source_unit_count")
        media_count = processing.get("source_media_count")
        if isinstance(unit_count, int) and unit_count > 0:
            parts.append(f"{unit_count} ta matn bo‘lagi")
        if isinstance(media_count, int) and media_count > 0:
            parts.append(f"{media_count} ta media")
        if parts:
            return f"O‘qildi: {', '.join(parts)} topildi."
    candidates = (
        raw_input.get("text"),
        raw_input.get("transcript"),
        raw_input.get("url"),
        raw_input.get("handle"),
        raw_input.get("file_name"),
        value.get("summary"),
        value.get("description"),
        value.get("text_preview"),
        value.get("label"),
    )
    for candidate in candidates:
        text = _text(candidate)
        if text and text.lower() not in {"manba", "source", "manual", "text"}:
            return _trim(_clean_source_prefix(text), 180)
    if isinstance(processing, dict):
        state = str(processing.get("state") or "")
        if state == "failed":
            return "O‘qish tugamadi. Qayta urinish mumkin; eski natija agentga tayyor deb berilmaydi."
        if state == "queued":
            return "Navbatda turibdi. OQIM o‘qishni boshlaganda dalillar shu yerda ko‘rinadi."
    return "Saqlangan dalil manbasi. O‘qish yakunlanganda topilgan obyektlar alohida ko‘rinadi."


def _safe_source_name(value: str, fallback: str) -> str:
    text = value.strip()
    if not text or text.lower() in {"manba", "source", "manual", "text"}:
        return fallback
    return _trim(text, 64)


def _normalized_source_kind(value: dict[str, Any]) -> str:
    kind = _text(value.get("kind"), "source")
    raw_input = value.get("input") if isinstance(value.get("input"), dict) else {}
    if kind != "source":
        return kind
    if _text(raw_input.get("url")):
        return "website"
    if _text(raw_input.get("handle")):
        return "telegram_channel"
    file_name = _text(raw_input.get("file_name"))
    if file_name:
        lowered = file_name.lower()
        if lowered.endswith((".xlsx", ".xls", ".csv")):
            return "spreadsheet"
        if lowered.endswith((".pdf", ".doc", ".docx", ".md", ".txt")):
            return "file"
        if lowered.endswith((".webm", ".mp3", ".m4a", ".wav", ".ogg")):
            return "audio"
        if lowered.endswith((".png", ".jpg", ".jpeg", ".webp")):
            return "image"
        return "file"
    if _text(raw_input.get("text") or raw_input.get("transcript")):
        return "manual"
    return kind


def _source_title_from_fact(fact: BusinessBrainFact) -> str:
    if fact.fact_type == "business_source_fact":
        return _source_fact_label(fact.value)
    for candidate in (fact.entity_ref, *fact.source_refs):
        label = _source_ref_label(str(candidate))
        if label:
            return label
    return "Dalil manbasi"


def _source_summary_from_fact(fact: BusinessBrainFact) -> str:
    value = fact.value
    raw_input = value.get("input") if isinstance(value.get("input"), dict) else {}
    processing = value.get("processing") if isinstance(value.get("processing"), dict) else {}
    unit_count = processing.get("source_unit_count")
    media_count = processing.get("source_media_count")
    parts: list[str] = []
    if isinstance(unit_count, int) and unit_count > 0:
        parts.append(f"{unit_count} ta matn bo‘lagi")
    if isinstance(media_count, int) and media_count > 0:
        parts.append(f"{media_count} ta media")
    if parts:
        return f"O‘qildi: {', '.join(parts)} topildi."
    for candidate in (
        raw_input.get("url"),
        raw_input.get("handle"),
        raw_input.get("file_name"),
        raw_input.get("text"),
        raw_input.get("transcript"),
        value.get("text_preview"),
        value.get("summary"),
        value.get("description"),
    ):
        text = _text(candidate)
        if text and text.lower() not in {"manba", "source", "manual", "text"}:
            return _trim(_clean_source_prefix(text), 180)
    return ""


def _website_label(value: str) -> str:
    parsed = urlparse(value)
    host = parsed.netloc or parsed.path.split("/", maxsplit=1)[0]
    return _trim(host or value, 64)


def _message_and_conversation_ids(facts: tuple[BusinessBrainFact, ...]) -> tuple[set[int], set[int]]:
    message_ids: set[int] = set()
    conversation_ids: set[int] = set()
    for fact in facts:
        for ref in fact.source_refs:
            message_id = _int_after_marker(str(ref), "message:")
            if message_id is not None:
                message_ids.add(message_id)
            conversation_id_from_ref = _int_after_marker(str(ref), "conversation:")
            if conversation_id_from_ref is not None:
                conversation_ids.add(conversation_id_from_ref)
        conversation_id = fact.value.get("conversation_id")
        if isinstance(conversation_id, int):
            conversation_ids.add(conversation_id)
    return message_ids, conversation_ids


def _media_title(value: dict[str, Any]) -> str:
    alt = _text(value.get("alt_text") or value.get("caption"))
    if alt:
        return _trim(f"Rasm: {alt}", 96)
    url = _text(value.get("url"))
    if url:
        return _trim(f"Rasm: {url.rstrip('/').split('/')[-1]}", 96)
    return "Manbadan topilgan rasm"


def _media_summary(value: dict[str, Any]) -> str:
    source = _text(value.get("origin"), "source")
    content_type = _text(value.get("content_type"), "media")
    return f"{content_type} · {source} orqali topilgan."


def _business_profile_summary(value: dict[str, Any]) -> str:
    parts = [
        _text(value.get("offer_summary")),
        _text(value.get("region")),
        _text(value.get("preferred_language")).replace("_", " "),
    ]
    return _trim(" · ".join(part for part in parts if part), 180) or "Biznes haqida onboardingda berilgan asosiy kontekst."


def _operating_preference_summary(value: dict[str, Any]) -> str:
    mode = _text(value.get("permission_mode"), "ask_always").replace("_", " ")
    agents = value.get("default_agents")
    agent_text = ", ".join(str(agent).replace("_", " ") for agent in agents) if isinstance(agents, list) else ""
    return _trim(f"Ruxsat: {mode}. Agentlar: {agent_text}.", 180)


def _conversation_ref_label(ref: str) -> str:
    message_id = _int_after_marker(ref, "message:")
    if message_id is not None:
        return f"Suhbat xabari #{message_id}"
    conversation_id = _int_after_marker(ref, "conversation:")
    if conversation_id is not None:
        return f"Suhbat #{conversation_id}"
    return "Oldingi suhbatdan dalil"


def _segment_after_marker(ref: str, markers: tuple[str, ...]) -> str:
    lower_ref = ref.lower()
    for marker in markers:
        index = lower_ref.find(marker.lower())
        if index < 0:
            continue
        tail = ref[index + len(marker):].strip()
        if not tail:
            return ""
        return tail.split(":", maxsplit=1)[0].split(maxsplit=1)[0].strip()
    return ""


def _int_after_marker(ref: str, marker: str) -> int | None:
    tail = _segment_after_marker(ref, (marker,))
    digits = []
    for char in tail:
        if not char.isdigit():
            break
        digits.append(char)
    if not digits:
        return None
    return int("".join(digits))
