from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.channel_runtime.delivery import ChannelRuntimeCore as DeliveryChannelRuntimeCore
from app.services.channel_sync_models import ChannelMessageRecord


class ChannelSourceRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChannelSourceSubscription(ChannelSourceRuntimeModel):
    schema_version: Literal["channel_source_subscription.v1"] = (
        "channel_source_subscription.v1"
    )
    subscription_id: str = Field(min_length=1, max_length=255)
    workspace_id: int = Field(gt=0)
    channel_account_id: str = Field(min_length=1, max_length=255)
    channel_kind: str = Field(min_length=1, max_length=80)
    external_channel_ref: str = Field(min_length=1, max_length=255)
    source_scope: Literal["catalog", "kb", "chat_memory", "mixed"] = "catalog"
    sync_policy: dict[str, Any] = Field(default_factory=dict)
    freshness_state: Literal["fresh", "degraded", "stale", "unknown"] = "unknown"
    last_cursor: str | None = Field(default=None, max_length=255)
    status: Literal["active", "paused", "disabled"] = "active"


class ChannelSourceIngestionItem(ChannelSourceRuntimeModel):
    schema_version: Literal["channel_source_ingestion_item.v1"] = (
        "channel_source_ingestion_item.v1"
    )
    external_message_id: str = Field(min_length=1, max_length=255)
    source_evidence_ref: str = Field(min_length=1, max_length=512)
    text: str = ""
    media_refs: list[str] = Field(default_factory=list)
    grouped_media_ref: str | None = Field(default=None, max_length=512)
    change_kind: Literal["create", "edit"] = "create"
    edited_at: datetime | None = None
    edit_version: str | None = Field(default=None, max_length=80)
    supersedes_source_evidence_ref: str | None = Field(default=None, max_length=512)


class ChannelGroupedMedia(ChannelSourceRuntimeModel):
    schema_version: Literal["channel_grouped_media.v1"] = "channel_grouped_media.v1"
    group_ref: str = Field(min_length=1, max_length=512)
    media_refs: list[str] = Field(default_factory=list)


class ChannelSourceExtractionJob(ChannelSourceRuntimeModel):
    schema_version: Literal["channel_source_extraction_job.v1"] = (
        "channel_source_extraction_job.v1"
    )
    job_kind: Literal["source_to_catalog", "source_to_kb", "source_to_chat_memory"]
    source_refs: list[str] = Field(default_factory=list)
    source_scope: str = Field(min_length=1, max_length=80)


class ChannelSourceIngestionPlan(ChannelSourceRuntimeModel):
    schema_version: Literal["channel_source_ingestion_plan.v1"] = (
        "channel_source_ingestion_plan.v1"
    )
    workspace_id: int = Field(gt=0)
    subscription_id: str = Field(min_length=1, max_length=255)
    channel_account_id: str = Field(min_length=1, max_length=255)
    channel_kind: str = Field(min_length=1, max_length=80)
    external_channel_ref: str = Field(min_length=1, max_length=255)
    source_scope: str = Field(min_length=1, max_length=80)
    sync_policy: dict[str, Any] = Field(default_factory=dict)
    background: bool = True
    freshness_state: Literal["fresh", "degraded", "stale", "unknown"] = "unknown"
    last_cursor: str | None = Field(default=None, max_length=255)
    degraded_reasons: list[str] = Field(default_factory=list)
    retry_after_seconds: float | None = Field(default=None, ge=0)
    account_state_impact: Literal["none"] = "none"
    items: list[ChannelSourceIngestionItem] = Field(default_factory=list)
    grouped_media: list[ChannelGroupedMedia] = Field(default_factory=list)
    extraction_jobs: list[ChannelSourceExtractionJob] = Field(default_factory=list)
    source_change_events: list[dict[str, Any]] = Field(default_factory=list)


class ChannelRuntimeCore(DeliveryChannelRuntimeCore):
    """Channel-owned planning boundary for background source ingestion work."""

    def plan_channel_source_ingestion(
        self,
        *,
        subscription: ChannelSourceSubscription,
        messages: list[ChannelMessageRecord],
        degraded_reason: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> ChannelSourceIngestionPlan:
        items: list[ChannelSourceIngestionItem] = []
        media_groups: dict[str, list[str]] = {}
        source_change_events: list[dict[str, Any]] = []
        for message in messages:
            evidence_ref = _source_evidence_ref(subscription, message.external_message_id)
            media_refs = _media_refs(subscription, message)
            group_ref = (
                _group_ref(subscription, message.grouped_id)
                if message.grouped_id is not None
                else None
            )
            supersedes_ref = _supersedes_source_evidence_ref(subscription, message)
            change_kind = "edit" if supersedes_ref or message.edited_at or message.edit_version else "create"
            if group_ref and media_refs:
                media_groups.setdefault(group_ref, []).extend(media_refs)
            item = ChannelSourceIngestionItem(
                external_message_id=message.external_message_id,
                source_evidence_ref=evidence_ref,
                text=message.text,
                media_refs=media_refs,
                grouped_media_ref=group_ref,
                change_kind=change_kind,
                edited_at=message.edited_at,
                edit_version=message.edit_version,
                supersedes_source_evidence_ref=supersedes_ref,
            )
            items.append(item)
            if item.change_kind == "edit":
                source_change_events.append(_source_change_event(item))

        grouped_media = [
            ChannelGroupedMedia(group_ref=group_ref, media_refs=_unique(refs))
            for group_ref, refs in sorted(media_groups.items())
        ]
        source_refs = [item.source_evidence_ref for item in items]
        source_refs.extend(group.group_ref for group in grouped_media)
        extraction_jobs = (
            [
                ChannelSourceExtractionJob(
                    job_kind=_job_kind(subscription.source_scope),
                    source_refs=_unique(source_refs),
                    source_scope=subscription.source_scope,
                )
            ]
            if source_refs and subscription.status == "active"
            else []
        )
        return ChannelSourceIngestionPlan(
            workspace_id=subscription.workspace_id,
            subscription_id=subscription.subscription_id,
            channel_account_id=subscription.channel_account_id,
            channel_kind=subscription.channel_kind,
            external_channel_ref=subscription.external_channel_ref,
            source_scope=subscription.source_scope,
            sync_policy=dict(subscription.sync_policy),
            background=True,
            freshness_state="degraded" if degraded_reason else subscription.freshness_state,
            last_cursor=_last_cursor(subscription.last_cursor, messages),
            degraded_reasons=[degraded_reason] if degraded_reason else [],
            retry_after_seconds=retry_after_seconds,
            account_state_impact="none",
            items=items,
            grouped_media=grouped_media,
            extraction_jobs=extraction_jobs,
            source_change_events=source_change_events,
        )


def _source_evidence_ref(subscription: ChannelSourceSubscription, message_id: str) -> str:
    return ":".join(
        [
            "channel_source",
            _clean_ref(subscription.channel_kind),
            _clean_ref(subscription.external_channel_ref),
            _clean_ref(message_id),
        ]
    )


def _supersedes_source_evidence_ref(
    subscription: ChannelSourceSubscription,
    message: ChannelMessageRecord,
) -> str | None:
    supersedes_id = str(
        message.supersedes_external_message_id or message.external_message_id
    ).strip()
    if not (message.edited_at or message.edit_version or message.supersedes_external_message_id):
        return None
    if not supersedes_id:
        return None
    return _source_evidence_ref(subscription, supersedes_id)


def _source_change_event(item: ChannelSourceIngestionItem) -> dict[str, Any]:
    return {
        "change_kind": item.change_kind,
        "source_evidence_ref": item.source_evidence_ref,
        "supersedes_source_evidence_ref": item.supersedes_source_evidence_ref,
        "external_message_id": item.external_message_id,
        "edit_version": item.edit_version,
        "changed_at": item.edited_at.isoformat() if item.edited_at else None,
        "catalog_update_policy": "create_update_proposal",
    }


def _media_refs(
    subscription: ChannelSourceSubscription,
    message: ChannelMessageRecord,
) -> list[str]:
    media_type = str(message.media_type or "").strip()
    if not media_type:
        return []
    return [
        ":".join(
            [
                "channel_media",
                _clean_ref(subscription.channel_kind),
                _clean_ref(subscription.external_channel_ref),
                _clean_ref(message.external_message_id),
                _clean_ref(media_type),
            ]
        )
    ]


def _group_ref(subscription: ChannelSourceSubscription, grouped_id: int) -> str:
    return ":".join(
        [
            "channel_media_group",
            _clean_ref(subscription.channel_kind),
            _clean_ref(subscription.external_channel_ref),
            str(grouped_id),
        ]
    )


def _last_cursor(current: str | None, messages: list[ChannelMessageRecord]) -> str | None:
    if not messages:
        return current
    return str(messages[-1].external_message_id)


def _job_kind(source_scope: str) -> Literal["source_to_catalog", "source_to_kb", "source_to_chat_memory"]:
    if source_scope == "kb":
        return "source_to_kb"
    if source_scope == "chat_memory":
        return "source_to_chat_memory"
    return "source_to_catalog"


def _clean_ref(value: object) -> str:
    return str(value or "").strip().replace(":", "_")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
