from __future__ import annotations

from datetime import UTC, datetime

from app.modules.channel_runtime.source import (
    ChannelRuntimeCore,
    ChannelSourceSubscription,
)
from app.services.channel_sync_models import ChannelMessageRecord


def test_channel_runtime_plans_source_ingestion_with_grouped_media_and_freshness() -> None:
    subscription = ChannelSourceSubscription(
        subscription_id="source-sub:telegram:@shop",
        workspace_id=7,
        channel_account_id="telegram:user:owner",
        channel_kind="telegram_channel",
        external_channel_ref="@shop",
        source_scope="catalog",
        sync_policy={"mode": "background", "max_posts": 50},
        freshness_state="fresh",
        last_cursor="41",
        status="active",
    )
    messages = [
        ChannelMessageRecord(
            external_message_id="42",
            sender_external_id="@shop",
            text="Yangi sumka 120000 UZS",
            sent_at=datetime(2026, 6, 5, 10, 0, tzinfo=UTC),
            is_outgoing=False,
            media_type="photo",
            media_metadata={"mime_type": "image/jpeg"},
            grouped_id=9001,
        ),
        ChannelMessageRecord(
            external_message_id="43",
            sender_external_id="@shop",
            text="Orqa tomoni",
            sent_at=datetime(2026, 6, 5, 10, 1, tzinfo=UTC),
            is_outgoing=False,
            media_type="photo",
            media_metadata={"mime_type": "image/jpeg"},
            grouped_id=9001,
        ),
    ]

    plan = ChannelRuntimeCore().plan_channel_source_ingestion(
        subscription=subscription,
        messages=messages,
    )

    assert plan.workspace_id == 7
    assert plan.subscription_id == "source-sub:telegram:@shop"
    assert plan.background is True
    assert plan.freshness_state == "fresh"
    assert plan.last_cursor == "43"
    assert [item.source_evidence_ref for item in plan.items] == [
        "channel_source:telegram_channel:@shop:42",
        "channel_source:telegram_channel:@shop:43",
    ]
    assert [item.media_refs for item in plan.items] == [
        ["channel_media:telegram_channel:@shop:42:photo"],
        ["channel_media:telegram_channel:@shop:43:photo"],
    ]
    assert plan.grouped_media[0].group_ref == "channel_media_group:telegram_channel:@shop:9001"
    assert plan.grouped_media[0].media_refs == [
        "channel_media:telegram_channel:@shop:42:photo",
        "channel_media:telegram_channel:@shop:43:photo",
    ]
    assert plan.extraction_jobs[0].job_kind == "source_to_catalog"
    assert plan.extraction_jobs[0].source_refs == [
        "channel_source:telegram_channel:@shop:42",
        "channel_source:telegram_channel:@shop:43",
        "channel_media_group:telegram_channel:@shop:9001",
    ]


def test_channel_runtime_source_flood_wait_degrades_freshness_only() -> None:
    subscription = ChannelSourceSubscription(
        subscription_id="source-sub:telegram:@slow",
        workspace_id=8,
        channel_account_id="telegram:user:owner",
        channel_kind="telegram_channel",
        external_channel_ref="@slow",
        source_scope="catalog",
        sync_policy={"mode": "background"},
        freshness_state="fresh",
        last_cursor="99",
        status="active",
    )

    plan = ChannelRuntimeCore().plan_channel_source_ingestion(
        subscription=subscription,
        messages=[],
        degraded_reason="flood_wait",
        retry_after_seconds=120,
    )

    assert plan.background is True
    assert plan.freshness_state == "degraded"
    assert plan.degraded_reasons == ["flood_wait"]
    assert plan.retry_after_seconds == 120
    assert plan.last_cursor == "99"
    assert plan.account_state_impact == "none"
    assert plan.extraction_jobs == []


def test_channel_runtime_plans_channel_post_edit_with_superseded_evidence() -> None:
    subscription = ChannelSourceSubscription(
        subscription_id="source-sub:telegram:@shop",
        workspace_id=7,
        channel_account_id="telegram:user:owner",
        channel_kind="telegram_channel",
        external_channel_ref="@shop",
        source_scope="catalog",
        sync_policy={"mode": "background", "max_posts": 50},
        freshness_state="fresh",
        last_cursor="42",
        status="active",
    )
    edited_at = datetime(2026, 6, 5, 11, 5, tzinfo=UTC)

    plan = ChannelRuntimeCore().plan_channel_source_ingestion(
        subscription=subscription,
        messages=[
            ChannelMessageRecord(
                external_message_id="42",
                sender_external_id="@shop",
                text="Yangi sumka 150000 UZS",
                sent_at=datetime(2026, 6, 5, 10, 0, tzinfo=UTC),
                edited_at=edited_at,
                edit_version="2",
                supersedes_external_message_id="42",
                is_outgoing=False,
                media_type="photo",
                media_metadata={"mime_type": "image/jpeg"},
            )
        ],
    )

    item = plan.items[0]
    assert item.change_kind == "edit"
    assert item.edited_at == edited_at
    assert item.edit_version == "2"
    assert item.supersedes_source_evidence_ref == "channel_source:telegram_channel:@shop:42"
    assert plan.source_change_events == [
        {
            "change_kind": "edit",
            "source_evidence_ref": "channel_source:telegram_channel:@shop:42",
            "supersedes_source_evidence_ref": "channel_source:telegram_channel:@shop:42",
            "external_message_id": "42",
            "edit_version": "2",
            "changed_at": edited_at.isoformat(),
            "catalog_update_policy": "create_update_proposal",
        }
    ]
    assert "channel_source:telegram_channel:@shop:42" in plan.extraction_jobs[0].source_refs
