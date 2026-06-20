from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onboarding_runtime import OnboardingRuntime
from app.models.workspace import Workspace
from app.services import onboarding_runtime


def _workspace():
    return SimpleNamespace(id=77)


def test_runtime_projection_uses_latest_progress_floor_when_runtime_snapshot_lags():
    workspace = SimpleNamespace(id=77, telegram_connected=True, onboarding_completed=False)
    runtime = SimpleNamespace(
        workspace_id=77,
        state=onboarding_runtime.ONBOARDING_RUNTIME_RUNNING,
        phase="starting",
        percent=1,
        leased_until=None,
        attempt_count=1,
        max_attempts=3,
        lease_owner="test-worker",
        next_attempt_at=None,
        started_at=None,
        completed_at=None,
        failed_at=None,
        last_error=None,
        progress_snapshot={
            "workspace_id": 77,
            "phase": "starting",
            "percent": 1,
            "completed": False,
            "contacts_found": 0,
            "customers_identified": 0,
        },
    )
    latest_progress = {
        "workspace_id": 77,
        "phase": "reading_dialogs",
        "percent": 35,
        "completed": False,
        "contacts_found": 548,
        "customers_identified": 0,
        "voice_profile_ready": False,
        "errors": [],
    }

    projection = onboarding_runtime.build_onboarding_runtime_projection(
        workspace=workspace,
        runtime=runtime,
        progress=latest_progress,
    )

    assert projection["schema_version"] == "onboarding_runtime.v1"
    assert projection["phase"] == "reading_dialogs"
    assert projection["percent"] == 35
    assert projection["progress"]["contacts_found"] == 548
    assert next(
        stage for stage in projection["stages"] if stage["id"] == "dialogs_scanned"
    )["status"] == "completed"


def test_runtime_projection_for_completed_workspace_overrides_stale_progress():
    workspace = SimpleNamespace(id=77, telegram_connected=True, onboarding_completed=True)
    runtime = SimpleNamespace(
        workspace_id=77,
        state=onboarding_runtime.ONBOARDING_RUNTIME_COMPLETED,
        phase="awaiting_channels",
        percent=100,
        leased_until=None,
        attempt_count=1,
        max_attempts=3,
        lease_owner=None,
        next_attempt_at=None,
        started_at=None,
        completed_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
        failed_at=None,
        last_error=None,
        progress_snapshot={
            "workspace_id": 77,
            "phase": "awaiting_channels",
            "percent": 65,
            "completed": False,
            "contacts_found": 127,
            "customers_identified": 0,
            "voice_profile_ready": True,
        },
    )

    projection = onboarding_runtime.build_onboarding_runtime_projection(
        workspace=workspace,
        runtime=runtime,
        progress={},
    )

    assert projection["phase"] == "done"
    assert projection["percent"] == 100
    assert projection["progress"]["completed"] is True
    assert projection["current_stage_id"] == "completed"
    assert next(
        stage for stage in projection["stages"] if stage["id"] == "completed"
    )["status"] == "completed"


def test_runtime_projection_embeds_current_source_learning_in_progress_snapshot():
    workspace = SimpleNamespace(id=77, telegram_connected=True, onboarding_completed=True)
    runtime = SimpleNamespace(
        workspace_id=77,
        state=onboarding_runtime.ONBOARDING_RUNTIME_COMPLETED,
        phase="done",
        percent=100,
        leased_until=None,
        attempt_count=1,
        max_attempts=3,
        lease_owner=None,
        next_attempt_at=None,
        started_at=None,
        completed_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
        failed_at=None,
        last_error=None,
        progress_snapshot={
            "workspace_id": 77,
            "phase": "done",
            "percent": 100,
            "completed": True,
            "source_learning": {
                "schema_version": "onboarding_source_runtime_result.v1",
                "processed_count": 0,
                "review_ready_count": 0,
            },
        },
    )
    source_learning = {
        "schema_version": "onboarding_source_learning.v1",
        "status": "needs_review",
        "summary": {"total": 1, "needs_review": 1},
        "sources": [{"source_ref": "onboarding:source:0", "status": "needs_review"}],
    }

    projection = onboarding_runtime.build_onboarding_runtime_projection(
        workspace=workspace,
        runtime=runtime,
        progress={},
        source_learning=source_learning,
    )

    assert projection["source_learning"] == source_learning
    assert projection["progress"]["source_learning"] == source_learning


def test_progress_db_floor_accepts_business_brain_voice_projection():
    progress = {
        "workspace_id": 77,
        "phase": "reading_dialogs",
        "percent": 35,
        "completed": False,
        "contacts_found": 10,
        "customers_identified": 0,
        "voice_profile_ready": False,
        "errors": [],
    }
    voice_projection = SimpleNamespace(
        degraded=False,
        state={"traits": [{"message_count_analyzed": 7, "quality_score": "weak"}]},
    )

    reconciled = onboarding_runtime.apply_progress_db_floor(
        progress,
        contact_count=10,
        customer_count=0,
        voice_projection=voice_projection,
    )

    assert reconciled["phase"] == "awaiting_channels"
    assert reconciled["percent"] == 65
    assert reconciled["voice_profile_ready"] is True
    assert reconciled["voice_profile_degraded"] is False


def test_runtime_projection_distinguishes_imported_dialogs_from_customer_classification():
    workspace = SimpleNamespace(id=77, telegram_connected=True, onboarding_completed=False)
    progress = {
        "workspace_id": 77,
        "phase": "classifying_contacts",
        "percent": 45,
        "completed": False,
        "contacts_found": 151,
        "customers_identified": 7,
        "voice_profile_ready": False,
        "errors": [],
    }

    projection = onboarding_runtime.build_onboarding_runtime_projection(
        workspace=workspace,
        runtime=None,
        progress=progress,
    )

    dialogs_stage = next(
        stage for stage in projection["stages"] if stage["id"] == "dialogs_scanned"
    )
    contacts_stage = next(
        stage for stage in projection["stages"] if stage["id"] == "contacts_classified"
    )
    assert dialogs_stage["detail"] == "151 ta suhbat ko‘rildi"
    assert contacts_stage["label"] == "Suhbatlar saralandi"
    assert contacts_stage["detail"] == "151 ta suhbatdan 7 tasi savdo mijoziga o‘xshaydi"


def test_source_learning_projection_matches_learning_result_by_source_ref():
    source_fact = SimpleNamespace(
        fact_id="onboarding:77:source:000",
        fact_type="business_source_fact",
        entity_ref="workspace:source:000",
        value={
            "kind": "website",
            "label": "Asosiy sayt",
            "processing": {"state": "queued"},
        },
        status="active",
        source_refs=["onboarding:source:0"],
    )
    learning_projection = SimpleNamespace(
        entity_ref="workspace:source:onboarding:source:0",
        state={
            "source_ref": "onboarding:source:0",
            "source_fact_id": "onboarding:77:source:000",
            "gateway_status": "ok",
            "catalog_candidate_count": 2,
            "memory_candidate_count": 1,
            "rejected_candidate_count": 0,
        },
        degraded=False,
        degraded_reasons=[],
    )

    projection = onboarding_runtime.build_onboarding_source_learning_projection(
        source_facts=[source_fact],
        source_learning_projections=[learning_projection],
    )

    assert projection["status"] == "needs_review"
    assert projection["summary"]["needs_review"] == 1
    assert projection["sources"][0]["status"] == "needs_review"
    assert projection["sources"][0]["source_ref"] == "onboarding:source:0"
    assert projection["events"] == [
        {
            "event_ref": "source-learning:onboarding:source:0:needs_review",
            "source_ref": "onboarding:source:0",
            "kind": "website",
            "status": "needs_review",
            "stage": "queued",
            "source_unit_count": 0,
            "source_media_count": 0,
            "catalog_candidate_count": 2,
            "memory_candidate_count": 1,
            "rejected_candidate_count": 0,
            "attempt_count": 0,
            "max_attempts": 0,
            "input_cache_reused": False,
            "title_uz": "Tasdiq kutmoqda: Asosiy sayt",
            "detail_uz": "2 ta katalog taklifi · 1 ta bilim taklifi · tasdiq kutmoqda",
        }
    ]


def test_source_learning_projection_does_not_smear_one_projection_across_sources():
    telegram_source = SimpleNamespace(
        fact_id="onboarding:77:source:telegram",
        fact_type="business_source_fact",
        entity_ref="workspace:source:telegram",
        value={
            "kind": "telegram_channel",
            "label": "@satstation",
            "processing": {"state": "queued"},
        },
        status="active",
        source_refs=["onboarding:source:0"],
    )
    site_source = SimpleNamespace(
        fact_id="onboarding:77:source:site",
        fact_type="business_source_fact",
        entity_ref="workspace:source:site",
        value={
            "kind": "website",
            "label": "satstation.io",
            "processing": {"state": "queued"},
        },
        status="active",
        source_refs=["onboarding:source:1"],
    )
    telegram_projection = SimpleNamespace(
        projection_ref="business_source_learning:onboarding:source:0",
        entity_ref="workspace:source:onboarding:source:0",
        state={
            "source_ref": "onboarding:source:0",
            "source_fact_id": "onboarding:77:source:telegram",
            "gateway_status": "ok",
            "catalog_candidate_count": 5,
            "memory_candidate_count": 8,
            "evidence_summary": {
                "source_unit_count": 12,
                "media_asset_count": 22,
            },
        },
        degraded=False,
        degraded_reasons=[],
    )

    projection = onboarding_runtime.build_onboarding_source_learning_projection(
        source_facts=[telegram_source, site_source],
        source_learning_projections=[telegram_projection],
    )

    by_ref = {source["source_ref"]: source for source in projection["sources"]}
    assert by_ref["onboarding:source:0"]["status"] == "needs_review"
    assert by_ref["onboarding:source:0"]["catalog_candidate_count"] == 5
    assert by_ref["onboarding:source:0"]["memory_candidate_count"] == 8
    assert by_ref["onboarding:source:0"]["source_media_count"] == 22
    assert by_ref["onboarding:source:1"]["status"] == "learning"
    assert by_ref["onboarding:source:1"]["catalog_candidate_count"] == 0
    assert by_ref["onboarding:source:1"]["memory_candidate_count"] == 0
    assert by_ref["onboarding:source:1"]["source_media_count"] == 0


def test_source_learning_projection_exposes_durable_stream_stage():
    source_fact = SimpleNamespace(
        fact_id="onboarding:77:source:001",
        fact_type="business_source_fact",
        entity_ref="workspace:source:001",
        value={
            "kind": "telegram_channel",
            "label": "@satstation",
            "processing": {"state": "queued"},
        },
        status="active",
        source_refs=["onboarding:source:1"],
    )
    learning_projection = SimpleNamespace(
        entity_ref="workspace:source:onboarding:source:1",
        state={
            "source_ref": "onboarding:source:1",
            "source_fact_id": "onboarding:77:source:001",
            "status": "learning",
            "stage": "fetching_telegram",
            "attempt_count": 2,
            "max_attempts": 3,
            "started_at": "2026-05-18T10:00:00+00:00",
            "updated_at": "2026-05-18T10:00:05+00:00",
        },
        degraded=False,
        degraded_reasons=[],
    )

    projection = onboarding_runtime.build_onboarding_source_learning_projection(
        source_facts=[source_fact],
        source_learning_projections=[learning_projection],
    )

    assert projection["status"] == "learning"
    assert projection["percent"] == 20
    assert projection["sources"][0]["stage"] == "fetching_telegram"
    assert projection["sources"][0]["attempt_count"] == 2
    assert projection["sources"][0]["max_attempts"] == 3
    assert projection["events"] == [
        {
            "event_ref": "source-learning:onboarding:source:1:learning",
            "source_ref": "onboarding:source:1",
            "kind": "telegram_channel",
            "status": "learning",
            "stage": "fetching_telegram",
            "source_unit_count": 0,
            "source_media_count": 0,
            "catalog_candidate_count": 0,
            "memory_candidate_count": 0,
            "rejected_candidate_count": 0,
            "attempt_count": 2,
            "max_attempts": 3,
            "input_cache_reused": False,
            "title_uz": "Telegramdan o‘qilmoqda: @satstation",
            "detail_uz": "Kanal postlari va media dalillar belgilangan sana bo‘yicha olinmoqda.",
        }
    ]


def test_source_learning_projection_uses_agent_copy_for_agent_sources():
    source_fact = SimpleNamespace(
        fact_id="onboarding:77:source:agent",
        fact_type="business_source_fact",
        entity_ref="workspace:source:agent",
        value={
            "kind": "text",
            "label": "AGENT.md qoidalari",
            "purpose": "agent_data",
            "processing": {"state": "queued"},
        },
        status="active",
        source_refs=["onboarding:source:agent"],
    )
    learning_projection = SimpleNamespace(
        entity_ref="workspace:source:onboarding:source:agent",
        state={
            "source_ref": "onboarding:source:agent",
            "source_fact_id": "onboarding:77:source:agent",
            "source_purpose": "agent_data",
            "status": "learning",
            "stage": "extracting",
            "source_unit_count": 2,
            "memory_candidate_count": 1,
            "attempt_count": 1,
            "max_attempts": 3,
        },
        degraded=False,
        degraded_reasons=[],
    )

    projection = onboarding_runtime.build_onboarding_source_learning_projection(
        source_facts=[source_fact],
        source_learning_projections=[learning_projection],
    )

    assert projection["sources"][0]["purpose"] == "agent_data"
    assert projection["events"] == [
        {
            "event_ref": "source-learning:onboarding:source:agent:learning",
            "source_ref": "onboarding:source:agent",
            "kind": "text",
            "status": "learning",
            "stage": "extracting",
            "source_unit_count": 2,
            "source_media_count": 0,
            "catalog_candidate_count": 0,
            "memory_candidate_count": 1,
            "rejected_candidate_count": 0,
            "attempt_count": 1,
            "max_attempts": 3,
            "input_cache_reused": False,
            "title_uz": "Agent sozlamasi ajratilmoqda: AGENT.md qoidalari",
            "detail_uz": "2 ta dalil tayyor. Endi AGENT.md, SKILL.md, qoidalar va yozish uslubi ajratilmoqda.",
        }
    ]


def test_source_learning_projection_retry_event_hides_raw_provider_error():
    source_fact = SimpleNamespace(
        fact_id="onboarding:77:source:002",
        fact_type="business_source_fact",
        entity_ref="workspace:source:002",
        value={
            "kind": "website",
            "label": "satstation.io",
            "processing": {"state": "queued"},
        },
        status="active",
        source_refs=["onboarding:source:2"],
    )
    learning_projection = SimpleNamespace(
        entity_ref="workspace:source:onboarding:source:2",
        state={
            "source_ref": "onboarding:source:2",
            "source_fact_id": "onboarding:77:source:002",
            "status": "retrying",
            "stage": "retrying",
            "attempt_count": 1,
            "max_attempts": 3,
        },
        degraded=True,
        degraded_reasons=["provider_429_rate_limit"],
    )

    projection = onboarding_runtime.build_onboarding_source_learning_projection(
        source_facts=[source_fact],
        source_learning_projections=[learning_projection],
    )

    assert projection["status"] == "retrying"
    assert projection["sources"][0]["retryable"] is True
    assert projection["events"] == [
        {
            "event_ref": "source-learning:onboarding:source:2:retrying",
            "source_ref": "onboarding:source:2",
            "kind": "website",
            "status": "retrying",
            "stage": "retrying",
            "source_unit_count": 0,
            "source_media_count": 0,
            "catalog_candidate_count": 0,
            "memory_candidate_count": 0,
            "rejected_candidate_count": 0,
            "attempt_count": 1,
            "max_attempts": 3,
            "input_cache_reused": False,
            "title_uz": "Qayta urinilmoqda: satstation.io",
            "detail_uz": "Provider band. OQIM keyinroq qayta urinishi mumkin. · 1/3-urinish",
        }
    ]


@pytest.mark.asyncio
async def test_start_ingestion_returns_active_progress_without_resetting_events():
    active_progress = {
        "workspace_id": 77,
        "phase": "reading_dialogs",
        "percent": 35,
        "completed": False,
        "errors": [],
    }
    active_runtime = SimpleNamespace(
        workspace_id=77,
        state=onboarding_runtime.ONBOARDING_RUNTIME_RUNNING,
        phase="reading_dialogs",
        percent=35,
        leased_until=datetime.now(UTC) + timedelta(seconds=30),
        progress_snapshot=active_progress,
    )
    replace_events = AsyncMock()
    store_progress = AsyncMock()
    mark_queued = AsyncMock()
    start_task = Mock()

    with patch.object(onboarding_runtime, "load_progress", new=AsyncMock(return_value=active_progress)), \
         patch.object(onboarding_runtime, "load_runtime", new=AsyncMock(return_value=active_runtime)), \
         patch.object(onboarding_runtime, "replace_events", new=replace_events), \
         patch.object(onboarding_runtime, "store_progress", new=store_progress), \
         patch.object(onboarding_runtime, "mark_runtime_queued", new=mark_queued), \
         patch.object(onboarding_runtime, "start_ingestion_task", new=start_task):
        result = await onboarding_runtime.start_ingestion(_workspace())

    assert result["status"] == "already_running"
    assert result["progress"] == active_progress
    replace_events.assert_not_awaited()
    store_progress.assert_not_awaited()
    start_task.assert_not_called()


@pytest.mark.asyncio
async def test_start_ingestion_restarts_stale_active_progress_without_runtime():
    stale_progress = {
        "workspace_id": 77,
        "phase": "classifying_contacts",
        "percent": 45,
        "completed": False,
        "errors": [],
    }
    load_progress = AsyncMock(side_effect=[stale_progress, stale_progress])
    acquire = AsyncMock(return_value=True)
    release = AsyncMock()
    replace_events = AsyncMock()
    store_progress = AsyncMock()
    mark_queued = AsyncMock()
    start_task = Mock()

    with patch.object(onboarding_runtime, "load_progress", new=load_progress), \
         patch.object(onboarding_runtime, "load_runtime", new=AsyncMock(return_value=None)), \
         patch.object(onboarding_runtime, "try_acquire_ingestion_start_lock", new=acquire), \
         patch.object(onboarding_runtime, "release_ingestion_start_lock", new=release), \
         patch.object(onboarding_runtime, "replace_events", new=replace_events), \
         patch.object(onboarding_runtime, "store_progress", new=store_progress), \
         patch.object(onboarding_runtime, "mark_runtime_queued", new=mark_queued), \
         patch.object(onboarding_runtime, "start_ingestion_task", new=start_task):
        result = await onboarding_runtime.start_ingestion(_workspace())

    assert result["status"] == "started"
    assert result["progress"]["phase"] == "starting"
    assert result["progress"]["percent"] == 1
    acquire.assert_awaited_once_with(77)
    release.assert_awaited_once_with(77)
    replace_events.assert_awaited_once_with(77, [])
    store_progress.assert_awaited_once()
    mark_queued.assert_awaited_once()
    start_task.assert_called_once()


@pytest.mark.asyncio
async def test_start_ingestion_returns_completed_progress_without_restart():
    completed_progress = {
        "workspace_id": 77,
        "phase": "done",
        "percent": 100,
        "completed": True,
        "errors": [],
    }
    start_task = Mock()

    with patch.object(onboarding_runtime, "load_progress", new=AsyncMock(return_value=completed_progress)), \
         patch.object(onboarding_runtime, "start_ingestion_task", new=start_task):
        result = await onboarding_runtime.start_ingestion(_workspace())

    assert result["status"] == "completed"
    assert result["progress"] == completed_progress
    start_task.assert_not_called()


@pytest.mark.asyncio
async def test_start_ingestion_uses_lock_and_creates_starting_progress_once():
    load_progress = AsyncMock(side_effect=[None, None])
    acquire = AsyncMock(return_value=True)
    release = AsyncMock()
    replace_events = AsyncMock()
    store_progress = AsyncMock()
    mark_queued = AsyncMock()
    start_task = Mock()

    with patch.object(onboarding_runtime, "load_progress", new=load_progress), \
         patch.object(onboarding_runtime, "load_runtime", new=AsyncMock(return_value=None)), \
         patch.object(onboarding_runtime, "try_acquire_ingestion_start_lock", new=acquire), \
         patch.object(onboarding_runtime, "release_ingestion_start_lock", new=release), \
         patch.object(onboarding_runtime, "replace_events", new=replace_events), \
         patch.object(onboarding_runtime, "store_progress", new=store_progress), \
         patch.object(onboarding_runtime, "mark_runtime_queued", new=mark_queued), \
         patch.object(onboarding_runtime, "start_ingestion_task", new=start_task):
        result = await onboarding_runtime.start_ingestion(_workspace())

    assert result["status"] == "started"
    assert result["progress"]["phase"] == "starting"
    assert result["progress"]["percent"] == 1
    acquire.assert_awaited_once_with(77)
    release.assert_awaited_once_with(77)
    replace_events.assert_awaited_once_with(77, [])
    store_progress.assert_awaited_once()
    mark_queued.assert_awaited_once()
    start_task.assert_called_once()


@pytest.mark.asyncio
async def test_ingestion_bridge_uses_event_spine_sync_factory_by_default():
    progress = {
        "workspace_id": 77,
        "phase": "starting",
        "percent": 1,
        "completed": False,
        "errors": [],
    }
    fake_redis = AsyncMock()
    event_append = AsyncMock()
    captured = {}

    class FakeEventSpine:
        def __init__(self, redis, **_kwargs):
            assert redis is fake_redis
            self.append = event_append

    class FakePipeline:
        def __init__(self, *, progress_update, notify_event, sync_factory):
            self.sync_factory = sync_factory

        async def run(self, workspace, _current_progress):
            sync = self.sync_factory()
            captured["event_append"] = sync._event_append

    source_learning = SimpleNamespace(
        processed_count=0,
        review_ready_count=0,
        retrying_count=0,
        failed_count=0,
        model_dump=lambda mode="json": {
            "processed_count": 0,
            "review_ready_count": 0,
            "retrying_count": 0,
            "failed_count": 0,
        },
    )
    source_learning_bridge = AsyncMock(return_value=source_learning)

    with patch.object(onboarding_runtime, "get_redis", new=AsyncMock(return_value=fake_redis)), \
         patch.object(onboarding_runtime, "EventSpine", new=FakeEventSpine), \
         patch.object(onboarding_runtime, "OnboardingIngestionPipeline", new=FakePipeline), \
         patch.object(onboarding_runtime, "run_source_learning_bridge", new=source_learning_bridge), \
         patch.object(onboarding_runtime, "store_progress", new=AsyncMock()), \
         patch.object(onboarding_runtime, "notify_progress", new=AsyncMock()), \
         patch.object(onboarding_runtime, "notify_event", new=AsyncMock()), \
         patch.object(onboarding_runtime, "set_progress", new=AsyncMock()), \
         patch.object(onboarding_runtime, "mark_runtime_completed", new=AsyncMock()):
        await onboarding_runtime.run_ingestion_bridge(
            _workspace(),
            initial_progress=progress,
        )

    assert captured["event_append"] is event_append
    source_learning_bridge.assert_awaited_once_with(
        77,
        correlation_id="onboarding:77:source_learning",
    )
    fake_redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_ingestion_reports_already_running_when_lock_is_busy():
    acquire = AsyncMock(return_value=False)
    replace_events = AsyncMock()
    start_task = Mock()

    with patch.object(onboarding_runtime, "load_progress", new=AsyncMock(return_value=None)), \
         patch.object(onboarding_runtime, "load_runtime", new=AsyncMock(return_value=None)), \
         patch.object(onboarding_runtime, "try_acquire_ingestion_start_lock", new=acquire), \
         patch.object(onboarding_runtime, "replace_events", new=replace_events), \
         patch.object(onboarding_runtime, "start_ingestion_task", new=start_task):
        result = await onboarding_runtime.start_ingestion(_workspace())

    assert result["status"] == "already_running"
    assert result["progress"]["phase"] == "not_started"
    replace_events.assert_not_awaited()
    start_task.assert_not_called()


@pytest.mark.asyncio
async def test_start_ingestion_uses_active_runtime_when_redis_progress_is_missing():
    runtime = SimpleNamespace(
        workspace_id=77,
        state=onboarding_runtime.ONBOARDING_RUNTIME_PENDING,
        phase="starting",
        percent=1,
        leased_until=None,
        progress_snapshot={
            "workspace_id": 77,
            "phase": "starting",
            "percent": 1,
            "completed": False,
        },
    )
    acquire = AsyncMock()
    start_task = Mock()

    with patch.object(onboarding_runtime, "load_progress", new=AsyncMock(return_value=None)), \
         patch.object(onboarding_runtime, "load_runtime", new=AsyncMock(return_value=runtime)), \
         patch.object(onboarding_runtime, "try_acquire_ingestion_start_lock", new=acquire), \
         patch.object(onboarding_runtime, "start_ingestion_task", new=start_task):
        result = await onboarding_runtime.start_ingestion(_workspace())

    assert result["status"] == "already_running"
    assert result["progress"]["phase"] == "starting"
    acquire.assert_not_awaited()
    start_task.assert_not_called()


@pytest.mark.asyncio
async def test_start_ingestion_uses_completed_runtime_when_redis_progress_is_missing():
    runtime = SimpleNamespace(
        workspace_id=77,
        state=onboarding_runtime.ONBOARDING_RUNTIME_COMPLETED,
        phase="done",
        percent=100,
        leased_until=None,
        progress_snapshot={
            "workspace_id": 77,
            "phase": "done",
            "percent": 100,
            "completed": True,
        },
    )
    acquire = AsyncMock()

    with patch.object(onboarding_runtime, "load_progress", new=AsyncMock(return_value=None)), \
         patch.object(onboarding_runtime, "load_runtime", new=AsyncMock(return_value=runtime)), \
         patch.object(onboarding_runtime, "try_acquire_ingestion_start_lock", new=acquire):
        result = await onboarding_runtime.start_ingestion(_workspace())

    assert result["status"] == "completed"
    assert result["progress"]["completed"] is True
    acquire.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_progress_response_reconciles_and_persists_stale_progress():
    stale_progress = {
        "workspace_id": 77,
        "phase": "awaiting_channels",
        "percent": 65,
        "completed": False,
    }
    reconciled = {
        **stale_progress,
        "phase": "reading_dialogs",
        "percent": 55,
    }
    store_progress = AsyncMock()

    with patch.object(onboarding_runtime, "load_progress", new=AsyncMock(return_value=stale_progress)), \
         patch.object(onboarding_runtime, "reconcile_progress_with_db", new=AsyncMock(return_value=reconciled)), \
         patch.object(onboarding_runtime, "store_progress", new=store_progress):
        result = await onboarding_runtime.get_progress_response(
            _workspace(),
            include_is_running=True,
        )

    assert result == {**reconciled, "is_running": True}
    store_progress.assert_awaited_once_with(77, reconciled)


@pytest.mark.asyncio
async def test_reconcile_progress_counts_only_classified_customers():
    class FakeSession:
        def __init__(self):
            self.values = [12, 9, None]
            self.statements = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def scalar(self, statement):
            self.statements.append(statement)
            return self.values.pop(0)

        async def execute(self, statement):
            self.statements.append(statement)
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    fake_session = FakeSession()
    progress = {
        "workspace_id": 77,
        "phase": "reading_dialogs",
        "percent": 35,
        "completed": False,
        "contacts_found": 0,
        "customers_identified": 0,
    }

    with patch.object(onboarding_runtime, "async_session", return_value=fake_session):
        result = await onboarding_runtime.reconcile_progress_with_db(77, progress)

    assert result["contacts_found"] == 12
    assert result["customers_identified"] == 9
    customer_count_sql = str(fake_session.statements[1])
    assert "contact_type" in customer_count_sql
    assert "classification_confidence" in customer_count_sql


@pytest.mark.asyncio
async def test_get_progress_response_completed_workspace_overrides_stale_redis():
    workspace = SimpleNamespace(id=77, onboarding_completed=True)
    stale_progress = {
        "workspace_id": 77,
        "phase": "awaiting_channels",
        "percent": 65,
        "completed": False,
    }
    store_progress = AsyncMock()

    with patch.object(onboarding_runtime, "load_progress", new=AsyncMock(return_value=stale_progress)), \
         patch.object(onboarding_runtime, "reconcile_progress_with_db", new=AsyncMock(return_value=stale_progress)), \
         patch.object(onboarding_runtime, "store_progress", new=store_progress):
        result = await onboarding_runtime.get_progress_response(
            workspace,
            include_is_running=True,
        )

    expected = {
        **stale_progress,
        "phase": "done",
        "percent": 100,
        "completed": True,
    }
    assert result == {**expected, "is_running": False}
    store_progress.assert_awaited_once_with(77, expected)


@pytest.mark.asyncio
async def test_get_progress_response_preserves_lightweight_not_started_shape():
    with patch.object(onboarding_runtime, "load_progress", new=AsyncMock(return_value=None)):
        result = await onboarding_runtime.get_progress_response(
            _workspace(),
            minimal_missing=True,
            include_is_running=True,
        )

    assert result == {
        "phase": "not_started",
        "percent": 0,
        "completed": False,
        "is_running": False,
    }


@pytest.mark.asyncio
async def test_mark_runtime_queued_creates_due_pending_row(
    db_session: AsyncSession,
    workspace: Workspace,
):
    now = datetime(2026, 5, 2, 8, 0, tzinfo=UTC)
    progress = {
        "workspace_id": workspace.id,
        "phase": "starting",
        "percent": 1,
        "completed": False,
    }

    runtime = await onboarding_runtime.mark_runtime_queued_in_session(
        db_session,
        workspace.id,
        progress,
        now=now,
    )

    assert runtime.state == onboarding_runtime.ONBOARDING_RUNTIME_PENDING
    assert runtime.phase == "starting"
    assert runtime.percent == 1
    assert runtime.attempt_count == 0
    assert runtime.lease_owner is None
    assert runtime.leased_until is None
    assert runtime.next_attempt_at == now
    assert runtime.progress_snapshot == progress


@pytest.mark.asyncio
async def test_mark_runtime_started_creates_leased_row(
    db_session: AsyncSession,
    workspace: Workspace,
):
    progress = {
        "workspace_id": workspace.id,
        "phase": "starting",
        "percent": 1,
        "completed": False,
    }

    runtime = await onboarding_runtime.mark_runtime_started_in_session(
        db_session,
        workspace.id,
        progress,
        lease_owner="test-worker",
        lease_seconds=30,
    )

    assert runtime.state == onboarding_runtime.ONBOARDING_RUNTIME_RUNNING
    assert runtime.phase == "starting"
    assert runtime.percent == 1
    assert runtime.attempt_count == 1
    assert runtime.lease_owner == "test-worker"
    assert runtime.leased_until is not None
    assert runtime.progress_snapshot == progress


@pytest.mark.asyncio
async def test_claim_due_onboarding_jobs_leases_pending_runtime(
    db_session: AsyncSession,
    workspace: Workspace,
):
    now = datetime(2026, 5, 2, 8, 0, tzinfo=UTC)
    progress = {
        "workspace_id": workspace.id,
        "phase": "starting",
        "percent": 1,
        "completed": False,
    }
    await onboarding_runtime.mark_runtime_queued_in_session(
        db_session,
        workspace.id,
        progress,
        now=now,
    )

    jobs = await onboarding_runtime.claim_due_onboarding_jobs(
        db_session,
        lease_owner="worker-1",
        lease_seconds=30,
        now=now,
    )

    assert len(jobs) == 1
    assert jobs[0].workspace_id == workspace.id
    assert jobs[0].state == onboarding_runtime.ONBOARDING_RUNTIME_RUNNING
    assert jobs[0].attempt_count == 1
    assert jobs[0].lease_owner == "worker-1"
    assert jobs[0].leased_until == now + timedelta(seconds=30)
    assert jobs[0].next_attempt_at is None


@pytest.mark.asyncio
async def test_claim_due_onboarding_jobs_reclaims_expired_running_runtime(
    db_session: AsyncSession,
    workspace: Workspace,
):
    now = datetime(2026, 5, 2, 8, 0, tzinfo=UTC)
    progress = {
        "workspace_id": workspace.id,
        "phase": "reading_dialogs",
        "percent": 35,
        "completed": False,
    }
    runtime = await onboarding_runtime.mark_runtime_started_in_session(
        db_session,
        workspace.id,
        progress,
        lease_owner="dead-worker",
        lease_seconds=30,
    )
    runtime.leased_until = now - timedelta(seconds=1)
    await db_session.flush()

    jobs = await onboarding_runtime.claim_due_onboarding_jobs(
        db_session,
        lease_owner="fresh-worker",
        lease_seconds=45,
        now=now,
    )

    assert len(jobs) == 1
    assert jobs[0].lease_owner == "fresh-worker"
    assert jobs[0].attempt_count == 2
    assert jobs[0].leased_until == now + timedelta(seconds=45)


@pytest.mark.asyncio
async def test_claim_due_onboarding_jobs_respects_retry_time_and_max_attempts(
    db_session: AsyncSession,
    workspace: Workspace,
):
    now = datetime(2026, 5, 2, 8, 0, tzinfo=UTC)
    progress = {
        "workspace_id": workspace.id,
        "phase": "error",
        "percent": 35,
        "completed": True,
    }
    runtime = await onboarding_runtime.mark_runtime_started_in_session(db_session, workspace.id, progress)
    runtime = await onboarding_runtime.mark_runtime_failed_in_session(
        db_session,
        workspace.id,
        progress,
        error="temporary outage",
    )
    runtime.next_attempt_at = now + timedelta(minutes=5)
    await db_session.flush()

    not_due = await onboarding_runtime.claim_due_onboarding_jobs(
        db_session,
        lease_owner="worker-1",
        now=now,
    )
    assert not_due == []

    runtime.next_attempt_at = now - timedelta(seconds=1)
    runtime.attempt_count = runtime.max_attempts
    await db_session.flush()

    exhausted = await onboarding_runtime.claim_due_onboarding_jobs(
        db_session,
        lease_owner="worker-1",
        now=now,
    )
    assert exhausted == []


@pytest.mark.asyncio
async def test_onboarding_runtime_worker_uses_canonical_due_source_learning_path():
    sync_factory = Mock()
    run_due = AsyncMock(return_value=2)
    worker = onboarding_runtime.OnboardingRuntimeWorker(
        db_factory=Mock(),
        redis=None,
        poll_interval_seconds=0.01,
        batch_size=2,
        sync_factory=sync_factory,
    )

    with patch.object(onboarding_runtime, "run_due_onboarding_once", new=run_due):
        processed = await worker.run_due_once()

    assert processed == 2
    run_due.assert_awaited_once_with(
        lease_owner=worker._consumer_name,
        limit=2,
        sync_factory=sync_factory,
    )


@pytest.mark.asyncio
async def test_record_runtime_progress_updates_snapshot(
    db_session: AsyncSession,
    workspace: Workspace,
):
    started = {
        "workspace_id": workspace.id,
        "phase": "starting",
        "percent": 1,
        "completed": False,
    }
    progress = {
        "workspace_id": workspace.id,
        "phase": "reading_dialogs",
        "percent": 35,
        "completed": False,
        "contacts_found": 12,
    }

    await onboarding_runtime.mark_runtime_started_in_session(db_session, workspace.id, started)
    runtime = await onboarding_runtime.record_runtime_progress_in_session(
        db_session,
        workspace.id,
        progress,
    )

    assert runtime is not None
    assert runtime.state == onboarding_runtime.ONBOARDING_RUNTIME_RUNNING
    assert runtime.phase == "reading_dialogs"
    assert runtime.percent == 35
    assert runtime.progress_snapshot == progress


@pytest.mark.asyncio
async def test_mark_runtime_completed_clears_lease(
    db_session: AsyncSession,
    workspace: Workspace,
):
    started = {
        "workspace_id": workspace.id,
        "phase": "starting",
        "percent": 1,
        "completed": False,
    }
    completed = {
        "workspace_id": workspace.id,
        "phase": "awaiting_channels",
        "percent": 85,
        "completed": False,
    }

    await onboarding_runtime.mark_runtime_started_in_session(
        db_session,
        workspace.id,
        started,
        lease_owner="test-worker",
    )
    runtime = await onboarding_runtime.mark_runtime_completed_in_session(
        db_session,
        workspace.id,
        completed,
    )

    assert runtime.state == onboarding_runtime.ONBOARDING_RUNTIME_COMPLETED
    assert runtime.phase == "done"
    assert runtime.percent == 100
    assert runtime.progress_snapshot["phase"] == "done"
    assert runtime.progress_snapshot["percent"] == 100
    assert runtime.progress_snapshot["completed"] is True
    assert runtime.lease_owner is None
    assert runtime.leased_until is None
    assert runtime.completed_at is not None
    assert runtime.last_error is None


@pytest.mark.asyncio
async def test_mark_runtime_failed_sets_retry_then_dlq(
    db_session: AsyncSession,
    workspace: Workspace,
):
    started = {
        "workspace_id": workspace.id,
        "phase": "starting",
        "percent": 1,
        "completed": False,
    }
    failed = {
        "workspace_id": workspace.id,
        "phase": "error",
        "percent": 35,
        "completed": True,
    }

    await onboarding_runtime.mark_runtime_started_in_session(db_session, workspace.id, started)
    runtime = await onboarding_runtime.mark_runtime_failed_in_session(
        db_session,
        workspace.id,
        failed,
        error="sidecar unavailable",
    )

    assert runtime.state == onboarding_runtime.ONBOARDING_RUNTIME_FAILED
    assert runtime.next_attempt_at is not None
    assert runtime.lease_owner is None
    assert runtime.leased_until is None
    assert runtime.last_error == "sidecar unavailable"

    runtime.attempt_count = runtime.max_attempts
    await db_session.flush()
    runtime = await onboarding_runtime.mark_runtime_failed_in_session(
        db_session,
        workspace.id,
        failed,
        error="still unavailable",
    )

    assert runtime.state == onboarding_runtime.ONBOARDING_RUNTIME_DLQ
    assert runtime.next_attempt_at is None
    assert runtime.last_error == "still unavailable"

    persisted = await db_session.scalar(
        select(OnboardingRuntime).where(OnboardingRuntime.workspace_id == workspace.id)
    )
    assert persisted is runtime
