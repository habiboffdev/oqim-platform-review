from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_memory.contracts import (
    ActionOptionBundle,
    AgentMemoryBundle,
    AuthorityWarning,
    BrainMemorySearchRequest,
    BrainMemorySearchResult,
)
from app.modules.agent_memory.seller_adapter import build_seller_agent_memory_bundle
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.retrieval_core.contracts import RetrievalContextRequest
from app.modules.retrieval_core.service import RetrievalCoreService

DOMAIN_FACT_TYPES: dict[str, list[str]] = {
    "seller.catalog": [
        "catalog_product",
        "catalog_variant",
        "catalog_offer",
        "catalog_media",
    ],
    "business.rules": ["knowledge_fact", "seller_rule_fact"],
    "style.voice": [
        "voice_fact",
        "conversation_pair_fact",
        "correction_episode_fact",
    ],
}


class AgentMemoryService:
    """Local Brain MCP boundary for typed, lane-shaped agent memory."""

    def __init__(
        self,
        session: AsyncSession | Any,
        *,
        retrieval: RetrievalCoreService | None = None,
    ) -> None:
        self._session = session
        self._retrieval = retrieval or RetrievalCoreService(
            repository=CommercialSpineRepository(session)
        )

    async def search_authority(
        self,
        request: BrainMemorySearchRequest,
    ) -> BrainMemorySearchResult:
        result = await self._retrieve(request, style_only=False)
        bundle = self._bundle_from_candidates(result.candidates)
        authority_lane = [
            item
            for item in bundle.authority_lane
            if not request.domains or item.domain in set(request.domains)
        ]
        warnings = list(bundle.warnings)
        action_lane: list[ActionOptionBundle] = []
        missing_fields = _missing_required_fields(
            authority_lane=authority_lane,
            required_fields=request.required_fields,
        )
        if missing_fields:
            warnings.extend(
                _warnings_for_missing_fields(
                    missing_fields=missing_fields,
                    authority_lane=authority_lane,
                )
            )
            action_lane.append(
                ActionOptionBundle(
                    kind="missing_authority",
                    title="Missing approved authority",
                    reason="Required authority fields are not available as approved Brain truth.",
                    payload={
                        "domains": list(request.domains),
                        "query": request.query,
                        "required_fields": missing_fields,
                    },
                    evidence_refs=_authority_evidence(authority_lane),
                )
            )
        degraded_reasons = list(getattr(result, "degraded_reasons", []) or [])
        return BrainMemorySearchResult(
            status=_status(authority_lane or action_lane, degraded_reasons),
            query=request.query,
            authority_lane=authority_lane,
            action_lane=action_lane,
            warnings=_unique_warnings(warnings),
            degraded_reasons=degraded_reasons,
        )

    async def search_style(
        self,
        request: BrainMemorySearchRequest,
    ) -> BrainMemorySearchResult:
        result = await self._retrieve(request, style_only=True)
        bundle = self._bundle_from_candidates(result.candidates)
        degraded_reasons = list(getattr(result, "degraded_reasons", []) or [])
        return BrainMemorySearchResult(
            status=_status(bundle.style_lane, degraded_reasons),
            query=request.query,
            style_lane=list(bundle.style_lane),
            degraded_reasons=degraded_reasons,
        )

    def assemble_turn_memory(
        self,
        context: Any,
        *,
        history: list[Any] | None = None,
    ) -> AgentMemoryBundle:
        return build_seller_agent_memory_bundle(
            grounding=getattr(context, "grounding", None),
            history=(
                list(history)
                if history is not None
                else list(getattr(context, "recent_messages", []) or [])
            ),
        )

    async def _retrieve(
        self,
        request: BrainMemorySearchRequest,
        *,
        style_only: bool,
    ) -> Any:
        fact_types = _fact_types_for_domains(
            request.domains or (["style.voice"] if style_only else ["seller.catalog", "business.rules"])
        )
        if not fact_types:
            return SimpleNamespace(candidates=[], degraded_reasons=[])
        return await self._retrieval.retrieve_contextual(
            RetrievalContextRequest(
                workspace_id=request.workspace_id,
                requested_fact_types=fact_types,
                query_text=request.query,
                enable_semantic=True,
                enable_query_rewrite=False,
                enable_agentic_search=False,
                enable_rerank=True,
                include_proposed=False,
                include_source_units=True,
                limit=request.limit,
            )
        )

    def _bundle_from_candidates(self, candidates: Any) -> AgentMemoryBundle:
        grounding = SimpleNamespace(families={})
        for candidate in candidates or []:
            dumped = _candidate_dump(candidate)
            fact_type = dumped.get("fact_type")
            if fact_type:
                grounding.families.setdefault(fact_type, []).append(dumped)
        return build_seller_agent_memory_bundle(
            grounding=grounding,
            history=[],
        )


def _fact_types_for_domains(domains: list[str]) -> list[str]:
    fact_types: list[str] = []
    for domain in domains:
        fact_types.extend(DOMAIN_FACT_TYPES.get(domain, []))
    return list(dict.fromkeys(fact_types))


def _candidate_dump(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, dict):
        return candidate
    if hasattr(candidate, "model_dump"):
        return candidate.model_dump(mode="json")
    return dict(vars(candidate))


def _missing_required_fields(
    *,
    authority_lane: list[Any],
    required_fields: list[str],
) -> list[str]:
    missing: list[str] = []
    scopes = {scope for item in authority_lane for scope in item.claim_scope}
    for field in required_fields:
        normalized = _normalize_required_field(field)
        if normalized not in scopes and normalized not in missing:
            missing.append(normalized)
    return missing


def _normalize_required_field(field: str) -> str:
    aliases = {
        "price": "offer",
        "catalog_offer": "offer",
        "catalog_variant": "variant",
        "catalog_media": "media",
    }
    normalized = (field or "").strip().lower()
    return aliases.get(normalized, normalized)


def _warnings_for_missing_fields(
    *,
    missing_fields: list[str],
    authority_lane: list[Any],
) -> list[AuthorityWarning]:
    target_ref = _first_catalog_ref(authority_lane)
    warnings: list[AuthorityWarning] = []
    for field in missing_fields:
        code = f"catalog_{field}_missing" if field in {"offer", "variant", "media"} else "authority_field_missing"
        warnings.append(
            AuthorityWarning(
                code=code,
                message=f"Required approved authority field is missing: {field}",
                target_ref=target_ref,
                evidence_refs=_authority_evidence(authority_lane),
                metadata={"field": field},
            )
        )
    return warnings


def _first_catalog_ref(authority_lane: list[Any]) -> str | None:
    for item in authority_lane:
        product = item.object.get("product") if isinstance(item.object, dict) else None
        if isinstance(product, dict) and product.get("ref"):
            return str(product["ref"])
    return None


def _authority_evidence(authority_lane: list[Any]) -> list[str]:
    refs: list[str] = []
    for item in authority_lane:
        refs.extend(item.evidence_refs)
    return list(dict.fromkeys(refs))


def _unique_warnings(warnings: list[AuthorityWarning]) -> list[AuthorityWarning]:
    out: list[AuthorityWarning] = []
    seen: set[tuple[str, str | None]] = set()
    for warning in warnings:
        key = (warning.code, warning.target_ref)
        if key not in seen:
            out.append(warning)
            seen.add(key)
    return out


def _status(items: list[Any], degraded_reasons: list[str]) -> str:
    if degraded_reasons:
        return "degraded"
    return "ok" if items else "empty"
