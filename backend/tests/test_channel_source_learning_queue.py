from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.channel_runtime.source import (
    ChannelRuntimeCore,
    ChannelSourceSubscription,
)
from app.modules.channel_runtime.source_queue import ChannelSourceLearningQueueService
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.hermes_runtime.service import HermesRunService
from app.services.channel_sync_models import ChannelMessageRecord
from app.services.source_learning_worker import claim_due_source_learning_jobs


async def test_channel_source_plan_queues_canonical_source_learning_job(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    subscription = ChannelSourceSubscription(
        subscription_id="source-sub:telegram:@catalog",
        workspace_id=workspace.id,
        channel_account_id="telegram:user:owner",
        channel_kind="telegram_channel",
        external_channel_ref="@catalog",
        source_scope="catalog",
        sync_policy={"mode": "background", "max_posts": 50},
        freshness_state="fresh",
        last_cursor="40",
        status="active",
    )
    plan = ChannelRuntimeCore().plan_channel_source_ingestion(
        subscription=subscription,
        messages=[
            ChannelMessageRecord(
                external_message_id="41",
                sender_external_id="@catalog",
                text="Atlas sumka 189000 UZS",
                sent_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
                is_outgoing=False,
                media_type="photo",
                media_metadata={"mime_type": "image/jpeg", "url": "https://cdn.example/atlas.jpg"},
                grouped_id=7001,
            )
        ],
    )
    repository = CommercialSpineRepository(db_session)

    queued = await ChannelSourceLearningQueueService(repository).queue_ingestion_plan(
        plan=plan,
        correlation_id="corr:channel-source-queue",
    )

    assert queued.queued is True
    assert queued.source_ref == "channel_source:telegram_channel:@catalog"
    assert queued.source_fact_id == "channel_source:telegram_channel:@catalog:source"
    assert queued.hermes_run_id.startswith("hermes_run:")
    assert queued.extraction_job_count == 1

    fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=queued.source_fact_id,
    )
    assert fact is not None
    assert fact.fact_type == "business_source_fact"
    assert fact.entity_ref == "workspace:source:channel_source:telegram_channel:@catalog"
    assert fact.status == "active"
    assert fact.value["kind"] == "telegram_channel"
    assert fact.value["input"]["channel_id"] == "@catalog"
    assert fact.value["input"]["messages"][0]["text"] == "Atlas sumka 189000 UZS"
    assert fact.value["input"]["messages"][0]["media_ref"] == (
        "channel_media:telegram_channel:@catalog:41:photo"
    )
    assert fact.value["processing"]["state"] == "queued"
    assert fact.value["processing"]["source_unit_count"] == 1
    assert fact.value["processing"]["source_media_count"] == 1
    assert fact.value["media_assets"] == [
        {
            "media_ref": "channel_media:telegram_channel:@catalog:41:photo",
            "source_ref": "channel_source:telegram_channel:@catalog:41",
            "media_type": "photo",
            "origin": "telegram_channel_message",
            "caption": "Atlas sumka 189000 UZS",
            "channel_message_id": "41",
            "grouped_id": "7001",
        }
    ]

    projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref=f"business_source_learning:{queued.source_ref}",
    )
    assert projection is not None
    assert projection.state["status"] == "queued"
    assert projection.state["stage"] == "queued"
    assert projection.state["source_ref"] == queued.source_ref
    assert projection.state["source_fact_id"] == queued.source_fact_id
    assert projection.state["source_unit_count"] == 1
    assert projection.state["source_media_count"] == 1
    assert projection.state["trigger_runtime"] == "channel_source"

    claims = await claim_due_source_learning_jobs(
        db_session,
        lease_owner="test-worker",
        now=datetime(2026, 6, 5, 12, 1, tzinfo=UTC),
    )
    assert [(claim.workspace_id, claim.source_refs) for claim in claims] == [
        (workspace.id, (queued.source_ref,))
    ]

    run = await HermesRunService(db_session).get_by_output_ref(
        f"business_source_learning:{queued.source_ref}"
    )
    assert run is not None
    assert run.lane == "background"
    assert run.run_mode == "learning"
    assert run.agent_kind == "channel_source"
    assert run.state == "completed"
    assert run.details["runtime_profile_kind"] == "channel_source"
    assert run.details["source_unit_count"] == 1


async def test_channel_source_queue_preserves_edit_update_context(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    subscription = ChannelSourceSubscription(
        subscription_id="source-sub:telegram:@catalog",
        workspace_id=workspace.id,
        channel_account_id="telegram:user:owner",
        channel_kind="telegram_channel",
        external_channel_ref="@catalog",
        source_scope="catalog",
        sync_policy={"mode": "background", "max_posts": 50},
        freshness_state="fresh",
        last_cursor="41",
        status="active",
    )
    edited_at = datetime(2026, 6, 5, 12, 5, tzinfo=UTC)
    plan = ChannelRuntimeCore().plan_channel_source_ingestion(
        subscription=subscription,
        messages=[
            ChannelMessageRecord(
                external_message_id="41",
                sender_external_id="@catalog",
                text="Atlas sumka 199000 UZS",
                sent_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
                edited_at=edited_at,
                edit_version="2",
                supersedes_external_message_id="41",
                is_outgoing=False,
                media_type="photo",
                media_metadata={"mime_type": "image/jpeg", "url": "https://cdn.example/atlas.jpg"},
            )
        ],
    )
    repository = CommercialSpineRepository(db_session)

    queued = await ChannelSourceLearningQueueService(repository).queue_ingestion_plan(
        plan=plan,
        correlation_id="corr:channel-source-edit",
    )

    fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=queued.source_fact_id,
    )
    assert fact is not None
    message = fact.value["input"]["messages"][0]
    assert message["change_kind"] == "edit"
    assert message["edited_at"] == edited_at.isoformat()
    assert message["edit_version"] == "2"
    assert message["supersedes_source_evidence_ref"] == (
        "channel_source:telegram_channel:@catalog:41"
    )
    assert fact.value["source_change_events"] == [
        {
            "change_kind": "edit",
            "source_evidence_ref": "channel_source:telegram_channel:@catalog:41",
            "supersedes_source_evidence_ref": "channel_source:telegram_channel:@catalog:41",
            "external_message_id": "41",
            "edit_version": "2",
            "changed_at": edited_at.isoformat(),
            "catalog_update_policy": "create_update_proposal",
        }
    ]

    projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref=f"business_source_learning:{queued.source_ref}",
    )
    assert projection is not None
    assert projection.state["source_change_event_count"] == 1
    assert projection.state["source_change_events"] == fact.value["source_change_events"]

    run = await HermesRunService(db_session).get_by_output_ref(
        f"business_source_learning:{queued.source_ref}"
    )
    assert run is not None
    assert run.details["source_change_event_count"] == 1
