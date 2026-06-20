from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import MemoryFactWriteInput
from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.retrieval_core.contracts import RetrievalContextRequest
from app.modules.retrieval_core.service import RetrievalCoreService


@dataclass(frozen=True, slots=True)
class RetrievalCoreEvalCase:
    case_id: str
    description: str
    query_text: str
    requested_fact_types: tuple[str, ...]
    expected_fact_ids: tuple[str, ...]
    expected_channels: tuple[str, ...]


class RetrievalCoreEvalCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class RetrievalCoreEvalResult(BaseModel):
    workspace_id: int
    case_id: str
    description: str
    passed: bool
    retrieved_fact_ids: list[str] = Field(default_factory=list)
    retrieval_channels: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    checks: list[RetrievalCoreEvalCheck] = Field(default_factory=list)


class RetrievalCoreEvalSuiteReport(BaseModel):
    suite: str = "retrieval-core"
    total_runs: int = Field(ge=0)
    passed_runs: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    workspace_count: int = Field(ge=0)
    cross_workspace_leak_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    median_case_duration_ms: int = Field(ge=0)
    p95_case_duration_ms: int = Field(ge=0)
    max_case_duration_ms: int = Field(ge=0)
    results: list[RetrievalCoreEvalResult] = Field(default_factory=list)


async def run_retrieval_core_eval_suite(
    *,
    repository: CommercialSpineRepository,
    workspace_ids: tuple[int, ...],
    repetitions: int = 1,
    use_live_reranker: bool = False,
) -> RetrievalCoreEvalSuiteReport:
    started = time.monotonic()
    workspace_ids = tuple(dict.fromkeys(workspace_ids))
    results: list[RetrievalCoreEvalResult] = []
    leak_count = 0
    for workspace_id in workspace_ids:
        for repetition in range(repetitions):
            run_key = f"retrieval-core-eval:ws{workspace_id}:r{repetition}"
            expected = await _seed_eval_facts(
                repository=repository,
                workspace_id=workspace_id,
                run_key=run_key,
            )
            service = RetrievalCoreService(
                repository=repository,
                gateway=LLMGateway(
                    repository=repository,
                    provider=_deterministic_retrieval_provider(),
                ),
                rerank_provider=None if use_live_reranker else _deterministic_rerank,
            )
            for case in _cases(expected):
                case_started = time.monotonic()
                result = await service.retrieve_contextual(
                    RetrievalContextRequest(
                        workspace_id=workspace_id,
                        requested_fact_types=list(case.requested_fact_types),
                        query_text=case.query_text,
                        enable_semantic=False,
                        enable_query_rewrite=True,
                        enable_agentic_search=True,
                        enable_rerank=True,
                        limit=10,
                    )
                )
                fact_ids = [candidate.fact_id for candidate in result.candidates]
                leaked_ids = [
                    fact_id
                    for fact_id in fact_ids
                    if fact_id.startswith("retrieval-eval:")
                    and not fact_id.startswith(f"retrieval-eval:{workspace_id}:")
                ]
                leak_count += len(leaked_ids)
                checks = [
                    RetrievalCoreEvalCheck(
                        name="expected_facts_recalled",
                        passed=all(fact_id in fact_ids for fact_id in case.expected_fact_ids),
                        detail=f"expected={list(case.expected_fact_ids)} retrieved={fact_ids}",
                    ),
                    RetrievalCoreEvalCheck(
                        name="expected_channels_present",
                        passed=all(
                            channel in result.trace.retrieval_channels
                            for channel in case.expected_channels
                        ),
                        detail=(
                            f"expected={list(case.expected_channels)} "
                            f"channels={list(result.trace.retrieval_channels)}"
                        ),
                    ),
                    RetrievalCoreEvalCheck(
                        name="no_degraded_retrieval",
                        passed=not result.degraded_reasons,
                        detail=f"degraded={list(result.degraded_reasons)}",
                    ),
                    RetrievalCoreEvalCheck(
                        name="no_cross_workspace_leak",
                        passed=not leaked_ids,
                        detail=f"leaked={leaked_ids}",
                    ),
                ]
                results.append(
                    RetrievalCoreEvalResult(
                        workspace_id=workspace_id,
                        case_id=case.case_id,
                        description=case.description,
                        passed=all(check.passed for check in checks),
                        retrieved_fact_ids=fact_ids,
                        retrieval_channels=list(result.trace.retrieval_channels),
                        degraded_reasons=list(result.degraded_reasons),
                        duration_ms=int((time.monotonic() - case_started) * 1000),
                        checks=checks,
                    )
                )
    passed = sum(1 for result in results if result.passed)
    durations = [result.duration_ms for result in results]
    return RetrievalCoreEvalSuiteReport(
        total_runs=len(results),
        passed_runs=passed,
        pass_rate=(passed / len(results)) if results else 0.0,
        workspace_count=len(workspace_ids),
        cross_workspace_leak_count=leak_count,
        duration_ms=int((time.monotonic() - started) * 1000),
        median_case_duration_ms=_percentile_ms(durations, 0.5),
        p95_case_duration_ms=_percentile_ms(durations, 0.95),
        max_case_duration_ms=max(durations, default=0),
        results=results,
    )


async def _seed_eval_facts(
    *,
    repository: CommercialSpineRepository,
    workspace_id: int,
    run_key: str,
) -> dict[str, str]:
    memory = BusinessBrainMemoryService(repository=repository)
    prefix = f"retrieval-eval:{workspace_id}:{run_key.split(':')[-1]}"
    facts = {
        "adapter": f"{prefix}:catalog:lightning-digital-av-adapter",
        "delivery": f"{prefix}:knowledge:delivery",
        "ruby_media": f"{prefix}:business_source_media:ruby-ring-page",
    }
    await memory.write_memory_fact(
        _fact(
            workspace_id=workspace_id,
            fact_id=facts["adapter"],
            fact_type="catalog_product",
            entity_ref=facts["adapter"],
            value={
                "title": "Apple Lightning Digital AV Adapter",
                "description": "HDMI TV display adapter from the imported catalog.",
            },
            source_refs=[f"{run_key}:source:catalog"],
        )
    )
    await memory.write_memory_fact(
        _fact(
            workspace_id=workspace_id,
            fact_id=facts["delivery"],
            fact_type="knowledge_fact",
            entity_ref="business:delivery",
            value={
                "topic": "delivery",
                "answer": "Toshkent ichida bugun yetkazib beramiz.",
            },
            source_refs=[f"{run_key}:source:faq"],
        )
    )
    await memory.write_memory_fact(
        _fact(
            workspace_id=workspace_id,
            fact_id=facts["ruby_media"],
            fact_type="business_source_media_fact",
            entity_ref=f"workspace:source_media:{facts['ruby_media']}",
            value={
                "media_ref": "ruby-ring-page",
                "source_ref": f"{run_key}:source:pdf:page:7",
                "alt_text": "PDF page shows ruby ring warranty and front side photo.",
                "source_kind": "pdf",
            },
            source_refs=[f"{run_key}:source:pdf:page:7"],
        )
    )
    return facts


def _fact(
    *,
    workspace_id: int,
    fact_id: str,
    fact_type: str,
    entity_ref: str,
    value: dict[str, Any],
    source_refs: list[str],
) -> MemoryFactWriteInput:
    return MemoryFactWriteInput(
        workspace_id=workspace_id,
        fact_id=fact_id,
        fact_type=fact_type,
        entity_ref=entity_ref,
        value=value,
        source_refs=source_refs,
        source="import",
        status="active",
        approval_state="confirmed",
        confidence=0.92,
        risk_tier="low",
        correlation_id=f"{fact_id}:corr",
        idempotency_key=f"{fact_id}:idem",
    )


def _cases(facts: dict[str, str]) -> tuple[RetrievalCoreEvalCase, ...]:
    return (
        RetrievalCoreEvalCase(
            case_id="query_rewrite_catalog_alias",
            description="Agentic/rewrite retrieval finds catalog evidence from buyer alias text.",
            query_text="hdmi perehodnik kerak",
            requested_fact_types=("catalog_product",),
            expected_fact_ids=(facts["adapter"],),
            expected_channels=("agentic_search", "query_rewrite", "rerank"),
        ),
        RetrievalCoreEvalCase(
            case_id="agentic_media_alias",
            description="Agentic media fact aliases recall PDF/source-media evidence.",
            query_text="PDFdagi ruby ring rasmi va kafolati",
            requested_fact_types=("knowledge_fact",),
            expected_fact_ids=(facts["ruby_media"],),
            expected_channels=("agentic_search", "rerank"),
        ),
        RetrievalCoreEvalCase(
            case_id="knowledge_policy_recall",
            description="Knowledge policy evidence remains retrievable through the shared boundary.",
            query_text="Toshkent ichida yetkazib berish bormi?",
            requested_fact_types=("knowledge_fact",),
            expected_fact_ids=(facts["delivery"],),
            expected_channels=("agentic_search", "rerank"),
        ),
    )


def _deterministic_retrieval_provider():
    async def provider(request) -> LLMProviderResponse:
        query = str(request.input_payload.get("query_text") or "").lower()
        if request.prompt_id == "retrieval_core.query_rewrite":
            rewrites = []
            if "perehodnik" in query:
                rewrites.append("lightning digital av adapter")
            if "ruby" in query:
                rewrites.append("ruby ring warranty front side photo")
            if "yetkazib" in query:
                rewrites.append("bugun yetkazib berish")
            return LLMProviderResponse(
                text=json.dumps(
                    {
                        "schema_version": "retrieval_query_rewrite_output.v1",
                        "rewrites": rewrites,
                    }
                ),
                model_used="deterministic-retrieval-eval",
            )
        if request.prompt_id == "retrieval_core.agentic_search_plan":
            if "ruby" in query:
                payload = {
                    "queries": ["ruby ring warranty front side photo"],
                    "fact_types": ["media_evidence_fact", "knowledge_fact"],
                    "query_modalities": ["pdf", "image"],
                }
            elif "perehodnik" in query:
                payload = {
                    "queries": ["lightning digital av adapter"],
                    "fact_types": ["catalog_product"],
                    "query_modalities": [],
                }
            else:
                payload = {
                    "queries": ["bugun yetkazib berish"],
                    "fact_types": ["knowledge_fact"],
                    "query_modalities": [],
                }
            return LLMProviderResponse(
                text=json.dumps(
                    {
                        "schema_version": "retrieval_agentic_search_output.v1",
                        **payload,
                    }
                ),
                model_used="deterministic-retrieval-eval",
            )
        raise RuntimeError(f"unexpected retrieval prompt: {request.prompt_id}")

    return provider


async def _deterministic_rerank(
    query: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    query_tokens = set(_tokens(query))
    ranked: list[tuple[float, dict[str, Any]]] = []
    for candidate in candidates:
        text_tokens = set(_tokens(str(candidate.get("text") or "")))
        overlap = len(query_tokens.intersection(text_tokens))
        score = 0.5 + min(overlap / max(len(query_tokens), 1), 0.49)
        ranked.append((score, {**candidate, "relevance_score": round(score, 6)}))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("fact_id") or "")))
    return [item for _score, item in ranked]


def _tokens(value: str) -> list[str]:
    normalized = "".join(character.lower() if character.isalnum() else " " for character in value)
    return [token for token in normalized.split() if len(token) > 1]


def _percentile_ms(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, int(round((len(ordered) - 1) * percentile))),
    )
    return ordered[index]
