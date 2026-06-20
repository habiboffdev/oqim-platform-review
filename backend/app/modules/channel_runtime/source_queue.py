from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import MemoryFactWriteInput
from app.modules.channel_runtime.source import ChannelSourceIngestionPlan
from app.modules.commercial_spine.contracts import BusinessBrainProjection
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.hermes_runtime.contracts import (
    HermesRunInput,
    HermesRunLane,
    HermesRunMode,
    HermesRunPatch,
)
from app.modules.hermes_runtime.service import HermesRunService


class ChannelSourceQueueModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChannelSourceLearningQueueResult(ChannelSourceQueueModel):
    schema_version: Literal["channel_source_learning_queue_result.v1"] = (
        "channel_source_learning_queue_result.v1"
    )
    queued: bool
    workspace_id: int = Field(gt=0)
    source_ref: str = Field(min_length=1)
    source_fact_id: str = Field(min_length=1)
    projection_ref: str = Field(min_length=1)
    hermes_run_id: str = Field(min_length=1)
    source_unit_count: int = Field(ge=0)
    source_media_count: int = Field(ge=0)
    grouped_media_count: int = Field(ge=0)
    extraction_job_count: int = Field(ge=0)
    degraded_reasons: list[str] = Field(default_factory=list)


class ChannelSourceLearningQueueService:
    """Queues watched-channel source evidence into the canonical Source Learning worker path."""

    def __init__(self, repository: CommercialSpineRepository) -> None:
        self._repository = repository
        self._memory = BusinessBrainMemoryService(repository=repository)
        self._runs = HermesRunService(repository.session)

    async def queue_ingestion_plan(
        self,
        *,
        plan: ChannelSourceIngestionPlan,
        correlation_id: str,
        max_attempts: int = 3,
    ) -> ChannelSourceLearningQueueResult:
        source_ref = _source_ref(plan)
        source_fact_id = f"{source_ref}:source"
        source_refs = _source_refs(plan=plan, source_ref=source_ref, source_fact_id=source_fact_id)
        source_unit_count = len(plan.items)
        source_media_count = sum(len(item.media_refs) for item in plan.items)
        grouped_media_count = len(plan.grouped_media)
        extraction_job_count = len(plan.extraction_jobs)
        await self._memory.write_memory_fact(
            MemoryFactWriteInput(
                workspace_id=plan.workspace_id,
                fact_id=source_fact_id,
                fact_type="business_source_fact",
                entity_ref=f"workspace:source:{source_ref}",
                value=_source_fact_value(plan),
                source_refs=source_refs,
                source="integration",
                status="active",
                approval_state="confirmed",
                confidence=0.96,
                risk_tier="low",
                correlation_id=correlation_id,
                idempotency_key=f"channel-source:{source_fact_id}",
                actor_ref="channel_source_runtime",
            )
        )
        projection_ref = f"business_source_learning:{source_ref}"
        state = _projection_state(
            plan=plan,
            source_ref=source_ref,
            source_fact_id=source_fact_id,
            correlation_id=correlation_id,
            max_attempts=max_attempts,
        )
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=projection_ref,
                workspace_id=plan.workspace_id,
                projection_type="business_source_learning",
                entity_ref=f"workspace:source:{source_ref}",
                state=state,
                source_refs=source_refs,
                degraded=bool(plan.degraded_reasons),
                degraded_reasons=list(plan.degraded_reasons),
            )
        )
        hermes_run = await self._record_hermes_run(
            plan=plan,
            source_ref=source_ref,
            source_fact_id=source_fact_id,
            projection_ref=projection_ref,
            correlation_id=correlation_id,
        )
        return ChannelSourceLearningQueueResult(
            queued=True,
            workspace_id=plan.workspace_id,
            source_ref=source_ref,
            source_fact_id=source_fact_id,
            projection_ref=projection_ref,
            hermes_run_id=hermes_run.run_id,
            source_unit_count=source_unit_count,
            source_media_count=source_media_count,
            grouped_media_count=grouped_media_count,
            extraction_job_count=extraction_job_count,
            degraded_reasons=list(plan.degraded_reasons),
        )

    async def _record_hermes_run(
        self,
        *,
        plan: ChannelSourceIngestionPlan,
        source_ref: str,
        source_fact_id: str,
        projection_ref: str,
        correlation_id: str,
    ):
        source_unit_count = len(plan.items)
        source_media_count = sum(len(item.media_refs) for item in plan.items)
        run = await self._runs.start_or_dedupe(
            HermesRunInput(
                workspace_id=plan.workspace_id,
                agent_id=None,
                agent_kind="channel_source",
                lane=HermesRunLane.BACKGROUND,
                run_mode=HermesRunMode.LEARNING,
                trigger_type="channel_source",
                trigger_id=_trigger_id(plan),
                event_id=f"{source_ref}:{plan.last_cursor or 'current'}",
                source_refs=[source_ref, *[item.source_evidence_ref for item in plan.items]],
                input_summary=(
                    f"Queue {source_unit_count} {plan.channel_kind} source item(s) "
                    f"for Source-to-Catalog learning."
                ),
                details={
                    "runtime_profile_kind": "channel_source",
                    "source_ref": source_ref,
                    "source_fact_id": source_fact_id,
                    "projection_ref": projection_ref,
                    "subscription_id": plan.subscription_id,
                    "source_scope": plan.source_scope,
                    "freshness_state": plan.freshness_state,
                    "source_unit_count": source_unit_count,
                    "source_media_count": source_media_count,
                    "grouped_media_count": len(plan.grouped_media),
                    "extraction_job_count": len(plan.extraction_jobs),
                    "source_change_event_count": len(plan.source_change_events),
                    "source_change_events": list(plan.source_change_events),
                    "degraded_reasons": list(plan.degraded_reasons),
                },
                correlation_id=correlation_id,
                idempotency_key=f"hermes_run:{plan.workspace_id}:channel_source:{_trigger_id(plan)}",
            )
        )
        if run.state == "completed":
            return run
        return await self._runs.complete(
            run.run_id,
            HermesRunPatch(
                output_action="queue_source_learning",
                output_ref=projection_ref,
                confidence=1.0,
                warnings_count=len(plan.degraded_reasons),
                details={
                    "queued": True,
                    "worker_role": "source_learning",
                },
            ),
        )


def _source_ref(plan: ChannelSourceIngestionPlan) -> str:
    return ":".join(
        [
            "channel_source",
            _clean_ref(plan.channel_kind),
            _clean_ref(plan.external_channel_ref),
        ]
    )


def _source_fact_value(plan: ChannelSourceIngestionPlan) -> dict:
    return {
        "kind": "telegram_channel" if plan.channel_kind.startswith("telegram") else plan.channel_kind,
        "purpose": "brain_data",
        "input": {
            "channel_id": plan.external_channel_ref,
            "subscription_id": plan.subscription_id,
            "channel_account_id": plan.channel_account_id,
            "messages": [_message_payload(item) for item in plan.items],
            "metadata": _source_metadata(plan),
        },
        "processing": {
            "state": "queued",
            "trigger_runtime": "channel_source",
            "source_scope": plan.source_scope,
            "freshness_state": plan.freshness_state,
            "source_unit_count": len(plan.items),
            "source_media_count": sum(len(item.media_refs) for item in plan.items),
            "grouped_media_count": len(plan.grouped_media),
            "extraction_job_count": len(plan.extraction_jobs),
            "source_change_event_count": len(plan.source_change_events),
            "degraded_reasons": list(plan.degraded_reasons),
            "last_cursor": plan.last_cursor,
        },
        "media_assets": [
            _media_payload(item, media_ref)
            for item in plan.items
            for media_ref in item.media_refs
        ],
        "grouped_media": [group.model_dump(mode="json") for group in plan.grouped_media],
        "source_change_events": list(plan.source_change_events),
        "source_refs": [item.source_evidence_ref for item in plan.items],
    }


def _source_metadata(plan: ChannelSourceIngestionPlan) -> dict:
    structured_source = str(plan.sync_policy.get("structured_source") or "").strip()
    metadata = {
        "trigger_runtime": "channel_source",
        "source_scope": plan.source_scope,
        "freshness_state": plan.freshness_state,
    }
    if structured_source:
        metadata["structured_source"] = structured_source
    return metadata


def _message_payload(item) -> dict:
    media_ref = item.media_refs[0] if item.media_refs else None
    payload = {
        "message_id": item.external_message_id,
        "id": item.external_message_id,
        "text": item.text,
        "source_evidence_ref": item.source_evidence_ref,
        "grouped_id": _grouped_id(item.grouped_media_ref),
        "change_kind": item.change_kind,
        "edited_at": item.edited_at.isoformat() if item.edited_at else None,
        "edit_version": item.edit_version,
        "supersedes_source_evidence_ref": item.supersedes_source_evidence_ref,
    }
    if media_ref:
        payload.update(
            {
                "media_type": _media_type(media_ref),
                "media_ref": media_ref,
                "media_metadata": {"media_ref": media_ref},
            }
        )
    return {key: value for key, value in payload.items() if value is not None}


def _media_payload(item, media_ref: str) -> dict:
    return {
        "media_ref": media_ref,
        "source_ref": item.source_evidence_ref,
        "media_type": _media_type(media_ref),
        "origin": "telegram_channel_message",
        "caption": item.text,
        "channel_message_id": item.external_message_id,
        "grouped_id": _grouped_id(item.grouped_media_ref),
    }


def _projection_state(
    *,
    plan: ChannelSourceIngestionPlan,
    source_ref: str,
    source_fact_id: str,
    correlation_id: str,
    max_attempts: int,
) -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "source_ref": source_ref,
        "source_kind": "telegram_channel" if plan.channel_kind.startswith("telegram") else plan.channel_kind,
        "source_purpose": "brain_data",
        "source_fact_id": source_fact_id,
        "status": "queued",
        "stage": "queued",
        "attempt_count": 0,
        "max_attempts": max(1, int(max_attempts)),
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "lease_owner": None,
        "leased_until": None,
        "next_attempt_at": None,
        "input_cache_reused": True,
        "trigger_runtime": "channel_source",
        "correlation_id": correlation_id,
        "source_scope": plan.source_scope,
        "source_unit_count": len(plan.items),
        "source_media_count": sum(len(item.media_refs) for item in plan.items),
        "grouped_media_count": len(plan.grouped_media),
        "extraction_job_count": len(plan.extraction_jobs),
        "source_change_event_count": len(plan.source_change_events),
        "source_change_events": list(plan.source_change_events),
        "evidence_summary": {
            "source_unit_count": len(plan.items),
            "media_asset_count": sum(len(item.media_refs) for item in plan.items),
            "grouped_media_count": len(plan.grouped_media),
            "source_change_event_count": len(plan.source_change_events),
        },
        "events": [
            {
                "status": "queued",
                "stage": "queued",
                "source_ref": source_ref,
                "source_fact_id": source_fact_id,
                "trigger_runtime": "channel_source",
                "created_at": now,
            }
        ],
    }


def _source_refs(
    *,
    plan: ChannelSourceIngestionPlan,
    source_ref: str,
    source_fact_id: str,
) -> list[str]:
    refs = [source_ref, source_fact_id]
    refs.extend(item.source_evidence_ref for item in plan.items)
    refs.extend(
        item.supersedes_source_evidence_ref
        for item in plan.items
        if item.supersedes_source_evidence_ref
    )
    refs.extend(group.group_ref for group in plan.grouped_media)
    return list(dict.fromkeys(ref for ref in refs if ref))


def _trigger_id(plan: ChannelSourceIngestionPlan) -> str:
    digest = hashlib.sha1(
        "|".join(
            [
                plan.subscription_id,
                plan.external_channel_ref,
                plan.last_cursor or "",
                *[item.external_message_id for item in plan.items],
                *[str(item.change_kind or "") for item in plan.items],
                *[str(item.edit_version or "") for item in plan.items],
                *[item.edited_at.isoformat() if item.edited_at else "" for item in plan.items],
            ]
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"{plan.subscription_id}:{digest}"


def _grouped_id(group_ref: str | None) -> str | None:
    if not group_ref:
        return None
    return group_ref.rsplit(":", 1)[-1]


def _media_type(media_ref: str) -> str:
    return media_ref.rsplit(":", 1)[-1] or "media"


def _clean_ref(value: object) -> str:
    return str(value or "").strip().replace(":", "_")
