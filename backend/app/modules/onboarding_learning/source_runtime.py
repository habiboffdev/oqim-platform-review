from __future__ import annotations

import asyncio
import base64
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.business_brain.source_learning import (
    BusinessSourceLearningRequest,
    BusinessSourceLearningService,
)
from app.modules.commercial_spine.contracts import BusinessBrainProjection
from app.modules.commercial_spine.llm_gateway import LLMGateway
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.source_ingestion import (
    OnboardingSourceIngestionRequest,
    OnboardingSourceIngestionService,
    SourceKind,
)

OnboardingSourceRuntimeStatus = Literal[
    "queued",
    "processed",
    "review_ready",
    "learned",
    "retrying",
    "failed",
    "skipped",
]

_SUPPORTED_SOURCE_KINDS = {
    "website",
    "pdf",
    "text",
    "telegram_channel",
    "screenshot",
    "voice_note",
    "spreadsheet",
    "past_conversation",
}

FetchTelegramChannelMessages = Callable[..., Awaitable[list[dict[str, Any]] | None]]
SessionFactory = Callable[[], contextlib.AbstractAsyncContextManager[AsyncSession]]
GatewayFactory = Callable[[CommercialSpineRepository], LLMGateway]


class _SourceJob(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    fact: Any
    source_ref: str
    source_kind: str
    source_purpose: str = "brain_data"
    source_fact_id: str
    projection: BusinessBrainProjection | None = None


def _source_jobs(
    *,
    source_facts: list[Any],
    projections_by_source_ref: dict[str, BusinessBrainProjection],
    limit: int,
    source_refs: set[str] | None,
    force: bool,
) -> list[_SourceJob]:
    jobs: list[_SourceJob] = []
    seen_source_refs: set[str] = set()
    processable_count = 0
    for fact in source_facts:
        if processable_count >= limit:
            break
        source_ref = _source_ref_for_fact(fact)
        if not source_ref:
            continue
        if source_ref in seen_source_refs:
            continue
        seen_source_refs.add(source_ref)
        if not str(getattr(fact, "entity_ref", "") or "").startswith(
            "workspace:source:"
        ):
            continue
        if source_refs is not None and source_ref not in source_refs:
            continue
        projection = projections_by_source_ref.get(source_ref)
        source_kind = str((getattr(fact, "value", {}) or {}).get("kind") or "source")
        source_purpose = _source_purpose_for_fact(fact)
        if not force and not _should_process_source(fact=fact, projection=projection):
            jobs.append(
                _SourceJob(
                    fact=fact,
                    source_ref=source_ref,
                    source_kind="__skipped__",
                    source_purpose=source_purpose,
                    source_fact_id=str(getattr(fact, "fact_id")),
                    projection=projection,
                )
            )
            continue
        processable_count += 1
        jobs.append(
            _SourceJob(
                fact=fact,
                source_ref=source_ref,
                source_kind=source_kind,
                source_purpose=source_purpose,
                source_fact_id=str(getattr(fact, "fact_id")),
                projection=projection,
            )
        )
    return jobs


class OnboardingSourceRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OnboardingSourceRuntimeItem(OnboardingSourceRuntimeModel):
    schema_version: Literal["onboarding_source_runtime_item.v1"] = (
        "onboarding_source_runtime_item.v1"
    )
    source_ref: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    source_purpose: str = "brain_data"
    source_fact_id: str = Field(min_length=1)
    status: OnboardingSourceRuntimeStatus
    attempt_count: int = Field(ge=0)
    degraded_reasons: list[str] = Field(default_factory=list)


class OnboardingSourceRuntimeResult(OnboardingSourceRuntimeModel):
    schema_version: Literal["onboarding_source_runtime_result.v1"] = (
        "onboarding_source_runtime_result.v1"
    )
    processed_count: int = Field(ge=0)
    review_ready_count: int = Field(ge=0)
    learned_count: int = Field(ge=0)
    retrying_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    items: list[OnboardingSourceRuntimeItem] = Field(default_factory=list)


class OnboardingSourceLearningRuntimeService:
    """Processes queued onboarding sources into Business Brain proposals.

    This service is deliberately thin: deterministic code owns queue selection,
    idempotency, retry state, and source-shape adaptation; Business Brain source
    ingestion and source learning own extraction, evidence, and semantic judgment.
    """

    def __init__(
        self,
        *,
        repository: CommercialSpineRepository,
        gateway: LLMGateway | None = None,
        ingestion: OnboardingSourceIngestionService | None = None,
        source_learning: BusinessSourceLearningService | None = None,
        fetch_telegram_channel_messages: FetchTelegramChannelMessages | None = None,
        embed_source_units: bool | None = None,
        contextualize_source_units: bool | None = None,
        session_factory: SessionFactory | None = None,
        gateway_factory: GatewayFactory | None = None,
        max_parallelism: int = 1,
        commit_hook: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._repository = repository
        self._gateway = gateway or LLMGateway(repository=repository)
        self._custom_ingestion = ingestion is not None
        self._custom_source_learning = source_learning is not None
        settings = get_settings()
        self._embed_source_units = (
            settings.onboarding_source_unit_embeddings_enabled
            if embed_source_units is None
            else embed_source_units
        )
        self._contextualize_source_units = (
            settings.onboarding_contextual_source_units_enabled
            if contextualize_source_units is None
            else contextualize_source_units
        )
        self._ingestion = ingestion or OnboardingSourceIngestionService(
            repository=repository,
            gateway=self._gateway,
        )
        self._source_learning = source_learning or BusinessSourceLearningService(
            repository=repository,
            gateway=self._gateway,
        )
        self._fetch_telegram_channel_messages = (
            fetch_telegram_channel_messages or _fetch_telegram_channel_messages
        )
        self._session_factory = session_factory
        self._gateway_factory = gateway_factory
        self._max_parallelism = max(1, int(max_parallelism or 1))
        self._commit_hook = commit_hook

    async def process_workspace_sources(
        self,
        *,
        workspace_id: int,
        correlation_id: str,
        limit: int = 10,
        max_attempts: int = 3,
        source_refs: set[str] | None = None,
        force: bool = False,
    ) -> OnboardingSourceRuntimeResult:
        source_facts = await self._repository.list_facts(
            workspace_id=workspace_id,
            fact_type="business_source_fact",
            statuses=("active", "degraded"),
            limit=250,
        )
        projections = await self._repository.list_projections(
            workspace_id=workspace_id,
            projection_type="business_source_learning",
            limit=250,
        )
        projections_by_source_ref = {
            _projection_source_ref(projection): projection
            for projection in projections
            if _projection_source_ref(projection)
        }

        items: list[OnboardingSourceRuntimeItem] = []
        jobs: list[_SourceJob] = []
        for job in _source_jobs(
            source_facts=source_facts,
            projections_by_source_ref=projections_by_source_ref,
            limit=limit,
            source_refs=source_refs,
            force=force,
        ):
            if job.source_kind == "__skipped__":
                items.append(
                    OnboardingSourceRuntimeItem(
                        source_ref=job.source_ref,
                        source_kind=str(
                            (getattr(job.fact, "value", {}) or {}).get("kind")
                            or "source"
                        ),
                        source_purpose=job.source_purpose,
                        source_fact_id=job.source_fact_id,
                        status="skipped",
                        attempt_count=_projection_attempt_count(job.projection),
                    )
                )
                continue
            jobs.append(job)

        if jobs:
            processed = await self._process_jobs(
                jobs=jobs,
                correlation_id=correlation_id,
                max_attempts=max_attempts,
            )
            items.extend(processed)

        return OnboardingSourceRuntimeResult(
            processed_count=sum(
                1
                for item in items
                if item.status in {"processed", "review_ready", "learned"}
            ),
            review_ready_count=sum(1 for item in items if item.status == "review_ready"),
            learned_count=sum(1 for item in items if item.status == "learned"),
            retrying_count=sum(1 for item in items if item.status == "retrying"),
            failed_count=sum(1 for item in items if item.status == "failed"),
            skipped_count=sum(1 for item in items if item.status == "skipped"),
            items=items,
        )

    async def queue_workspace_sources(
        self,
        *,
        workspace_id: int,
        limit: int = 10,
        max_attempts: int = 3,
        source_refs: set[str] | None = None,
        force: bool = False,
    ) -> OnboardingSourceRuntimeResult:
        """Persist a durable queued marker before async source learning starts."""
        source_facts = await self._repository.list_facts(
            workspace_id=workspace_id,
            fact_type="business_source_fact",
            statuses=("active", "degraded"),
            limit=250,
        )
        projections = await self._repository.list_projections(
            workspace_id=workspace_id,
            projection_type="business_source_learning",
            limit=250,
        )
        projections_by_source_ref = {
            _projection_source_ref(projection): projection
            for projection in projections
            if _projection_source_ref(projection)
        }

        items: list[OnboardingSourceRuntimeItem] = []
        for job in _source_jobs(
            source_facts=source_facts,
            projections_by_source_ref=projections_by_source_ref,
            limit=limit,
            source_refs=source_refs,
            force=force,
        ):
            if job.source_kind == "__skipped__":
                items.append(
                    OnboardingSourceRuntimeItem(
                        source_ref=job.source_ref,
                        source_kind=str(
                            (getattr(job.fact, "value", {}) or {}).get("kind")
                            or "source"
                        ),
                        source_purpose=job.source_purpose,
                        source_fact_id=job.source_fact_id,
                        status="skipped",
                        attempt_count=_projection_attempt_count(job.projection),
                    )
                )
                continue
            projection_status = _projection_status(job.projection)
            if not force and projection_status in {"queued", "learning"}:
                items.append(
                    OnboardingSourceRuntimeItem(
                        source_ref=job.source_ref,
                        source_kind=job.source_kind,
                        source_purpose=job.source_purpose,
                        source_fact_id=job.source_fact_id,
                        status="skipped",
                        attempt_count=_projection_attempt_count(job.projection),
                        degraded_reasons=[],
                    )
                )
                continue
            attempt_count = _projection_attempt_count(job.projection)
            await self._persist_runtime_projection(
                workspace_id=workspace_id,
                source_ref=job.source_ref,
                source_kind=job.source_kind,
                source_purpose=job.source_purpose,
                source_fact_id=job.source_fact_id,
                source_refs=_runtime_source_refs(fact=job.fact, source_ref=job.source_ref),
                status="queued",
                stage="queued",
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                degraded_reasons=[],
            )
            items.append(
                OnboardingSourceRuntimeItem(
                    source_ref=job.source_ref,
                    source_kind=job.source_kind,
                    source_purpose=job.source_purpose,
                    source_fact_id=job.source_fact_id,
                    status="queued",
                    attempt_count=attempt_count,
                    degraded_reasons=[],
                )
            )

        return OnboardingSourceRuntimeResult(
            processed_count=0,
            review_ready_count=0,
            learned_count=0,
            retrying_count=0,
            failed_count=0,
            skipped_count=sum(1 for item in items if item.status == "skipped"),
            items=items,
        )

    async def _process_jobs(
        self,
        *,
        jobs: list[_SourceJob],
        correlation_id: str,
        max_attempts: int,
    ) -> list[OnboardingSourceRuntimeItem]:
        if not self._can_parallelize_jobs:
            return [
                await self._process_source_fact(
                    fact=job.fact,
                    source_ref=job.source_ref,
                    projection=job.projection,
                    correlation_id=correlation_id,
                    max_attempts=max_attempts,
                )
                for job in jobs
            ]

        semaphore = asyncio.Semaphore(self._max_parallelism)

        async def run(job: _SourceJob) -> OnboardingSourceRuntimeItem:
            async with semaphore:
                return await self._process_source_job_in_new_session(
                    job=job,
                    correlation_id=correlation_id,
                    max_attempts=max_attempts,
                )

        return list(await asyncio.gather(*(run(job) for job in jobs)))

    @property
    def _can_parallelize_jobs(self) -> bool:
        return (
            self._max_parallelism > 1
            and self._session_factory is not None
            and not self._custom_ingestion
            and not self._custom_source_learning
        )

    async def _process_source_job_in_new_session(
        self,
        *,
        job: _SourceJob,
        correlation_id: str,
        max_attempts: int,
    ) -> OnboardingSourceRuntimeItem:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            repository = CommercialSpineRepository(session)
            gateway = (
                self._gateway_factory(repository)
                if self._gateway_factory is not None
                else LLMGateway(repository=repository)
            )
            runtime = OnboardingSourceLearningRuntimeService(
                repository=repository,
                gateway=gateway,
                fetch_telegram_channel_messages=self._fetch_telegram_channel_messages,
                embed_source_units=self._embed_source_units,
                contextualize_source_units=self._contextualize_source_units,
                commit_hook=session.commit,
            )
            item = await runtime._process_source_fact(
                fact=job.fact,
                source_ref=job.source_ref,
                projection=job.projection,
                correlation_id=correlation_id,
                max_attempts=max_attempts,
            )
            await session.commit()
            return item

    async def _process_source_fact(
        self,
        *,
        fact: Any,
        source_ref: str,
        projection: BusinessBrainProjection | None,
        correlation_id: str,
        max_attempts: int,
    ) -> OnboardingSourceRuntimeItem:
        value = dict(getattr(fact, "value", {}) or {})
        source_fact_id = str(getattr(fact, "fact_id"))
        source_purpose = _source_purpose_for_fact(fact)
        attempt_count = _projection_attempt_count(projection) + 1
        try:
            source_kind, source_payload, content_base64 = _runtime_source_payload(value)
            if source_kind not in _SUPPORTED_SOURCE_KINDS:
                raise ValueError(f"unsupported_source_kind:{source_kind}")
            if (
                source_kind == "telegram_channel"
                and not isinstance(source_payload.get("messages"), list)
            ):
                await self._persist_runtime_projection(
                    workspace_id=int(getattr(fact, "workspace_id")),
                    source_ref=source_ref,
                    source_kind=source_kind,
                    source_purpose=source_purpose,
                    source_fact_id=source_fact_id,
                    source_refs=_runtime_source_refs(fact=fact, source_ref=source_ref),
                    status="learning",
                    stage="fetching_telegram",
                    attempt_count=attempt_count,
                    max_attempts=max_attempts,
                    degraded_reasons=[],
                )
                messages = await self._fetch_telegram_channel_messages(
                    workspace_id=int(getattr(fact, "workspace_id")),
                    source_payload=source_payload,
                )
                if messages:
                    source_payload = {**source_payload, "messages": messages}

            ingested_source = _is_ingested_source_fact(fact=fact, value=value)
            await self._persist_runtime_projection(
                workspace_id=int(getattr(fact, "workspace_id")),
                source_ref=source_ref,
                source_kind=source_kind,
                source_purpose=source_purpose,
                source_fact_id=source_fact_id,
                source_refs=_runtime_source_refs(fact=fact, source_ref=source_ref),
                status="learning",
                stage="using_cache" if ingested_source else "ingesting",
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                degraded_reasons=[],
            )
            learning_source_fact_id = source_fact_id
            ingestion_source_unit_count: int | None = _source_fact_unit_count(value)
            ingestion_source_media_count: int | None = _source_fact_media_count(value)
            if not ingested_source:
                ingestion = await self._ingestion.ingest(
                    OnboardingSourceIngestionRequest(
                        workspace_id=int(getattr(fact, "workspace_id")),
                        source_ref=source_ref,
                        source_kind=source_kind,  # type: ignore[arg-type]
                        source_payload=source_payload,
                        content_base64=content_base64,
                        correlation_id=correlation_id,
                        idempotency_key=f"onboarding-source-runtime:{source_ref}:ingest",
                        actor_ref="onboarding_source_runtime",
                        embed_source_units=self._embed_source_units,
                        contextualize_source_units=self._contextualize_source_units,
                    )
                )
                learning_source_fact_id = ingestion.source_fact_id
                ingestion_source_unit_count = len(ingestion.source_units)
                ingestion_source_media_count = len(ingestion.media_assets)
            await self._persist_runtime_projection(
                workspace_id=int(getattr(fact, "workspace_id")),
                source_ref=source_ref,
                source_kind=source_kind,
                source_purpose=source_purpose,
                source_fact_id=learning_source_fact_id,
                source_refs=_runtime_source_refs(fact=fact, source_ref=source_ref),
                status="learning",
                stage="extracting",
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                degraded_reasons=[],
                source_unit_count=ingestion_source_unit_count,
                source_media_count=ingestion_source_media_count,
            )
            learning = await self._source_learning.learn_from_source(
                BusinessSourceLearningRequest(
                    workspace_id=int(getattr(fact, "workspace_id")),
                    source_ref=source_ref,
                    source_kind=source_kind,  # type: ignore[arg-type]
                    source_fact_id=learning_source_fact_id,
                    correlation_id=correlation_id,
                    idempotency_key=f"onboarding-source-runtime:{source_ref}:learn",
                    content_parts=_source_learning_content_parts(
                        source_kind=source_kind,
                        source_payload=source_payload,
                        content_base64=content_base64,
                    ),
                )
            )
            if learning.gateway_status != "ok" or learning.degraded_reasons:
                return await self._persist_failure_item(
                    fact=fact,
                    source_ref=source_ref,
                    source_kind=source_kind,
                    source_purpose=source_purpose,
                    source_fact_id=learning_source_fact_id,
                    attempt_count=attempt_count,
                    max_attempts=max_attempts,
                    degraded_reasons=(
                        _normalized_degraded_reasons(learning.degraded_reasons)
                        or [f"gateway_{learning.gateway_status}"]
                    ),
                )
            if learning.catalog_candidate_count + learning.memory_candidate_count > 0:
                status: OnboardingSourceRuntimeStatus = "review_ready"
            else:
                status = "learned"
            await self._persist_runtime_projection(
                workspace_id=int(getattr(fact, "workspace_id")),
                source_ref=source_ref,
                source_kind=source_kind,
                source_purpose=source_purpose,
                source_fact_id=learning_source_fact_id,
                source_refs=_runtime_source_refs(fact=fact, source_ref=source_ref),
                status=status,
                stage=status,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                degraded_reasons=[],
                source_unit_count=int(learning.evidence_summary.get("source_unit_count") or 0),
                source_media_count=int(learning.evidence_summary.get("media_asset_count") or 0),
                catalog_candidate_count=learning.catalog_candidate_count,
                memory_candidate_count=learning.memory_candidate_count,
                rejected_candidate_count=len(learning.rejected_candidates),
            )
            return OnboardingSourceRuntimeItem(
                source_ref=source_ref,
                source_kind=source_kind,
                source_purpose=source_purpose,
                source_fact_id=learning_source_fact_id,
                status=status,
                attempt_count=attempt_count,
                degraded_reasons=[],
            )
        except Exception as exc:
            return await self._persist_failure_item(
                fact=fact,
                source_ref=source_ref,
                source_kind=str(value.get("kind") or "source"),
                source_purpose=source_purpose,
                source_fact_id=source_fact_id,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                degraded_reasons=[_error_reason(exc)],
            )

    async def _persist_failure_item(
        self,
        *,
        fact: Any,
        source_ref: str,
        source_kind: str,
        source_purpose: str,
        source_fact_id: str,
        attempt_count: int,
        max_attempts: int,
        degraded_reasons: list[str],
    ) -> OnboardingSourceRuntimeItem:
        status: OnboardingSourceRuntimeStatus = (
            "failed" if attempt_count >= max_attempts else "retrying"
        )
        await self._persist_runtime_projection(
            workspace_id=int(getattr(fact, "workspace_id")),
            source_ref=source_ref,
            source_kind=source_kind,
            source_purpose=source_purpose,
            source_fact_id=source_fact_id,
            source_refs=_runtime_source_refs(fact=fact, source_ref=source_ref),
            status=status,
            stage=status,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            degraded_reasons=degraded_reasons or [status],
        )
        return OnboardingSourceRuntimeItem(
            source_ref=source_ref,
            source_kind=source_kind,
            source_purpose=source_purpose,
            source_fact_id=source_fact_id,
            status=status,
            attempt_count=attempt_count,
            degraded_reasons=degraded_reasons,
        )

    async def _persist_runtime_projection(
        self,
        *,
        workspace_id: int,
        source_ref: str,
        source_kind: str,
        source_purpose: str,
        source_fact_id: str,
        source_refs: list[str],
        status: str,
        stage: str,
        attempt_count: int,
        max_attempts: int,
        degraded_reasons: list[str],
        source_unit_count: int | None = None,
        source_media_count: int | None = None,
        catalog_candidate_count: int | None = None,
        memory_candidate_count: int | None = None,
        rejected_candidate_count: int | None = None,
    ) -> None:
        existing = await self._repository.get_projection(
            workspace_id=workspace_id,
            projection_ref=f"business_source_learning:{source_ref}",
        )
        existing_state = dict(existing.state or {}) if existing is not None else {}
        now = datetime.now(UTC).isoformat()
        started_at = str(existing_state.get("started_at") or now)
        terminal_statuses = {"review_ready", "learned", "failed"}
        clear_lease = status in terminal_statuses or status == "retrying"
        next_attempt_at = (
            (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
            if status == "retrying"
            else None
        )
        input_cache_reused = bool(existing_state.get("input_cache_reused")) or stage == "using_cache"
        evidence_summary = dict(existing_state.get("evidence_summary") or {})
        if source_unit_count is None:
            source_unit_count = int(evidence_summary.get("source_unit_count") or 0)
        if source_media_count is None:
            source_media_count = int(evidence_summary.get("media_asset_count") or 0)
        if catalog_candidate_count is None:
            catalog_candidate_count = int(existing_state.get("catalog_candidate_count") or 0)
        if memory_candidate_count is None:
            memory_candidate_count = int(existing_state.get("memory_candidate_count") or 0)
        if rejected_candidate_count is None:
            rejected_candidate_count = int(existing_state.get("rejected_candidate_count") or 0)
        if source_unit_count:
            evidence_summary["source_unit_count"] = source_unit_count
        if source_media_count:
            evidence_summary["media_asset_count"] = source_media_count
        next_state = {
            **existing_state,
            "source_ref": source_ref,
            "source_kind": source_kind,
            "source_purpose": source_purpose,
            "source_fact_id": source_fact_id,
            "status": status,
            "stage": stage,
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
            "started_at": started_at,
            "updated_at": now,
            **(
                {"completed_at": now}
                if status in terminal_statuses
                else {"completed_at": existing_state.get("completed_at")}
            ),
            "lease_owner": None if clear_lease else existing_state.get("lease_owner"),
            "leased_until": None if clear_lease else existing_state.get("leased_until"),
            "next_attempt_at": next_attempt_at,
            "input_cache_reused": input_cache_reused,
            "source_unit_count": source_unit_count,
            "source_media_count": source_media_count,
            "catalog_candidate_count": catalog_candidate_count,
            "memory_candidate_count": memory_candidate_count,
            "rejected_candidate_count": rejected_candidate_count,
            "evidence_summary": evidence_summary,
        }
        next_state["events"] = _append_runtime_event(
            existing_events=list(existing_state.get("events") or []),
            source_ref=source_ref,
            source_kind=source_kind,
            source_purpose=source_purpose,
            source_fact_id=source_fact_id,
            status=status,
            stage=stage,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            degraded_reasons=degraded_reasons,
            input_cache_reused=input_cache_reused,
            source_unit_count=source_unit_count,
            source_media_count=source_media_count,
            catalog_candidate_count=catalog_candidate_count,
            memory_candidate_count=memory_candidate_count,
            rejected_candidate_count=rejected_candidate_count,
            created_at=now,
        )
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=f"business_source_learning:{source_ref}",
                workspace_id=workspace_id,
                projection_type="business_source_learning",
                entity_ref=f"workspace:source:{source_ref}",
                state=next_state,
                source_refs=source_refs,
                degraded=bool(degraded_reasons),
                degraded_reasons=degraded_reasons,
            )
        )
        if self._commit_hook is not None:
            await self._commit_hook()


def _runtime_source_payload(
    value: dict[str, Any],
) -> tuple[SourceKind, dict[str, Any], str | None]:
    raw_kind = str(value.get("kind") or "").strip()
    raw_input = value.get("input")
    payload = dict(raw_input) if isinstance(raw_input, dict) else {}
    if raw_kind == "voice_note":
        transcript = str(payload.get("transcript") or "").strip()
        content_base64 = _payload_content_base64(payload)
        if content_base64:
            return "voice_note", payload, content_base64
        payload = {**payload, "text": transcript}
        return "text", payload, None
    if raw_kind == "file":
        content_type = str(payload.get("content_type") or "").strip().lower()
        file_name = str(payload.get("file_name") or "").strip().lower()
        content_base64 = _payload_content_base64(payload)
        if content_type == "application/pdf" or file_name.endswith(".pdf"):
            return "pdf", payload, content_base64
        if content_base64 and (
            content_type in {
                "text/csv",
                "application/csv",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.ms-excel",
            }
            or file_name.endswith((".csv", ".xlsx", ".xlsm", ".xltx", ".xltm"))
        ):
            return "spreadsheet", payload, content_base64
        if content_base64 and (
            content_type.startswith("text/")
            or file_name.endswith((".txt", ".md"))
        ):
            return "text", {"text": _decode_text_base64(content_base64)}, None
        return "text", {"text": str(payload.get("text") or "")}, None
    if raw_kind in _SUPPORTED_SOURCE_KINDS:
        content_base64 = _payload_content_base64(payload)
        return raw_kind, payload, content_base64  # type: ignore[return-value]
    return raw_kind, payload, None  # type: ignore[return-value]


def _append_runtime_event(
    *,
    existing_events: list[Any],
    source_ref: str,
    source_kind: str,
    source_purpose: str,
    source_fact_id: str,
    status: str,
    stage: str,
    attempt_count: int,
    max_attempts: int,
    degraded_reasons: list[str],
    input_cache_reused: bool,
    source_unit_count: int,
    source_media_count: int,
    catalog_candidate_count: int,
    memory_candidate_count: int,
    rejected_candidate_count: int,
    created_at: str,
) -> list[dict[str, Any]]:
    event_ref = f"source-learning:{source_ref}:{attempt_count}:{stage}:{status}"
    event = {
        "event_ref": event_ref,
        "source_ref": source_ref,
        "kind": source_kind,
        "purpose": source_purpose,
        "source_fact_id": source_fact_id,
        "status": status,
        "stage": stage,
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
        "degraded_reasons": list(degraded_reasons),
        "input_cache_reused": input_cache_reused,
        "source_unit_count": source_unit_count,
        "source_media_count": source_media_count,
        "catalog_candidate_count": catalog_candidate_count,
        "memory_candidate_count": memory_candidate_count,
        "rejected_candidate_count": rejected_candidate_count,
        "created_at": created_at,
    }
    merged: list[dict[str, Any]] = []
    replaced = False
    for raw in existing_events:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("event_ref") or "") == event_ref:
            merged.append(event)
            replaced = True
        else:
            merged.append(dict(raw))
    if not replaced:
        merged.append(event)
    return merged[-20:]


def _source_learning_content_parts(
    *,
    source_kind: str,
    source_payload: dict[str, Any],
    content_base64: str | None,
) -> list[dict[str, Any]]:
    if not content_base64:
        return []
    mime_type = _source_payload_mime_type(source_kind=source_kind, payload=source_payload)
    if not mime_type:
        return []
    part = {
        "kind": "inline_data",
        "mime_type": mime_type,
        "data_base64": content_base64,
    }
    if source_kind == "pdf":
        file_name = str(source_payload.get("file_name") or "").strip()
        if file_name:
            part["file_name"] = file_name
        part["upload_strategy"] = "file_api"
    return [part]


def _source_payload_mime_type(*, source_kind: str, payload: dict[str, Any]) -> str | None:
    explicit = str(
        payload.get("content_type")
        or payload.get("mime_type")
        or ""
    ).strip()
    if explicit:
        return explicit
    if source_kind == "pdf":
        return "application/pdf"
    if source_kind == "screenshot":
        return "image/png"
    if source_kind == "voice_note":
        return "audio/ogg"
    return None


async def _fetch_telegram_channel_messages(
    *,
    workspace_id: int,
    source_payload: dict[str, Any],
) -> list[dict[str, Any]] | None:
    channel_ref = str(
        source_payload.get("channel_id")
        or source_payload.get("channel")
        or source_payload.get("handle")
        or ""
    ).strip()
    if not channel_ref:
        return None
    settings = get_settings()
    headers: dict[str, str] = {}
    if settings.sidecar_api_key:
        headers["X-Sidecar-Key"] = settings.sidecar_api_key
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{settings.sidecar_url}/channel-posts",
                headers=headers,
                params={
                    "workspaceId": workspace_id,
                    "channelId": channel_ref,
                    "limit": int(source_payload.get("limit") or 100),
                    **_telegram_channel_date_params(source_payload),
                },
            )
            if response.status_code != 200:
                return None
            payload = response.json()
            if not isinstance(payload, list):
                return None
            return [dict(item) for item in payload if isinstance(item, dict)]
    except Exception:
        return None


def _telegram_channel_date_params(source_payload: dict[str, Any]) -> dict[str, str]:
    params: dict[str, str] = {}
    date_from = str(source_payload.get("date_from") or "").strip()
    date_to = str(source_payload.get("date_to") or "").strip()
    if date_from:
        params["dateFrom"] = date_from
    if date_to:
        params["dateTo"] = date_to
    return params


def _payload_content_base64(payload: dict[str, Any]) -> str | None:
    for key in ("content_base64", "file_base64", "data_base64"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return None


def _decode_text_base64(content_base64: str) -> str:
    try:
        return base64.b64decode(content_base64).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _source_ref_for_fact(fact: Any) -> str:
    for ref in list(getattr(fact, "source_refs", []) or []):
        ref_text = str(ref)
        if ref_text.startswith("onboarding:source:"):
            return ref_text
    entity_ref = str(getattr(fact, "entity_ref", "") or "")
    if entity_ref.startswith("workspace:source:"):
        return entity_ref.removeprefix("workspace:source:")
    return ""


def _source_purpose_for_fact(fact: Any) -> str:
    value = dict(getattr(fact, "value", {}) or {})
    purpose = str(value.get("purpose") or "brain_data").strip().lower()
    if purpose in {"brain_data", "agent_data"}:
        return purpose
    raw_input = value.get("input")
    if isinstance(raw_input, dict):
        input_purpose = str(raw_input.get("purpose") or "").strip().lower()
        if input_purpose in {"brain_data", "agent_data"}:
            return input_purpose
    return "brain_data"


def _source_fact_unit_count(value: dict[str, Any]) -> int:
    processing = dict(value.get("processing") or {})
    return int(processing.get("source_unit_count") or 0)


def _source_fact_media_count(value: dict[str, Any]) -> int:
    processing = dict(value.get("processing") or {})
    media_assets = value.get("media_assets")
    return int(
        processing.get("source_media_count")
        or (len(media_assets) if isinstance(media_assets, list) else 0)
    )


def _projection_source_ref(projection: BusinessBrainProjection) -> str:
    state = dict(projection.state or {})
    return str(state.get("source_ref") or "").strip()


def _projection_attempt_count(projection: BusinessBrainProjection | None) -> int:
    if projection is None:
        return 0
    state = dict(projection.state or {})
    return max(0, int(state.get("attempt_count") or 0))


def _projection_status(projection: BusinessBrainProjection | None) -> str:
    if projection is None:
        return ""
    state = dict(projection.state or {})
    return str(state.get("status") or "").strip().lower()


def _should_process_source(
    *,
    fact: Any,
    projection: BusinessBrainProjection | None,
) -> bool:
    value = dict(getattr(fact, "value", {}) or {})
    processing = dict(value.get("processing") or {})
    processing_state = str(processing.get("state") or "").strip().lower()
    if projection is None:
        return processing_state in {"queued", "indexed", "degraded", "retrying", ""}
    state = dict(projection.state or {})
    projection_status = str(state.get("status") or "").strip().lower()
    if projection_status in {"queued", "learning", "retrying"}:
        return True
    if projection.degraded and projection_status != "failed":
        return True
    return False


def _is_ingested_source_fact(*, fact: Any, value: dict[str, Any]) -> bool:
    processing = dict(value.get("processing") or {})
    processing_state = str(processing.get("state") or "").strip().lower()
    fact_id = str(getattr(fact, "fact_id", "") or "")
    return fact_id.startswith("business_source:") and processing_state in {
        "indexed",
        "degraded",
        "embedded",
        "ready",
    }


def _runtime_source_refs(*, fact: Any, source_ref: str) -> list[str]:
    refs = [source_ref, str(getattr(fact, "fact_id"))]
    refs.extend(str(ref) for ref in list(getattr(fact, "source_refs", []) or []))
    unique: list[str] = []
    for ref in refs:
        if ref and ref not in unique:
            unique.append(ref)
    return unique


def _error_reason(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "provider_timeout"
    message = str(exc)
    normalized = message.lower().replace("-", "_").replace(" ", "_")
    if "429" in normalized or "rate_limit" in normalized or "ratelimit" in normalized:
        return "provider_rate_limited"
    if message.startswith("unsupported_source_kind:"):
        return message
    return exc.__class__.__name__


def _normalized_degraded_reasons(reasons: list[str]) -> list[str]:
    normalized: list[str] = []
    for reason in reasons:
        text = str(reason or "").strip()
        slug = text.lower().replace("-", "_").replace(" ", "_")
        if "429" in slug or "rate_limit" in slug or "ratelimit" in slug:
            text = "provider_rate_limited"
        if text and text not in normalized:
            normalized.append(text)
    return normalized
