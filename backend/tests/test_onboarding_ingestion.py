from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.workspace import Workspace
from app.services.onboarding_ingestion import (
    CONTACT_CLASSIFICATION_DEGRADED,
    VOICE_PROFILE_DEGRADED,
    OnboardingIngestionPipeline,
)


class _SessionContext:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return None


def _base_progress() -> dict:
    return {
        "workspace_id": 77,
        "phase": "reading_dialogs",
        "percent": 42,
        "contacts_found": 10,
        "customers_identified": 0,
        "products_extracted": 0,
        "knowledge_items": 0,
        "voice_profile_ready": False,
        "voice_profile_degraded": False,
        "voice_profile_error": None,
        "contact_classification_degraded": False,
        "ai_learning_degraded": False,
        "ai_learning_error": None,
        "voice_discoveries": [],
        "completed": False,
        "errors": [],
    }


@pytest.mark.asyncio
async def test_hydrate_history_imports_recent_history_without_legacy_replay(
    db_session: AsyncSession,
):
    progress = _base_progress()
    progress_updates: list[dict] = []
    events: list[dict] = []

    async def progress_update(current, **updates):
        current.update(updates)
        progress_updates.append(dict(updates))
        return current

    async def notify_event(_workspace_id, event):
        events.append(event)

    class _Sync:
        async def bootstrap_inbox(self, **kwargs):
            self.bootstrap_kwargs = kwargs
            return SimpleNamespace(synced_count=50)

        async def prefetch_recent_history(self, **kwargs):
            self.prefetch_kwargs = kwargs
            return SimpleNamespace(
                prefetched_conversations=50,
                persisted_messages=600,
                deferred=False,
            )

    sync = _Sync()
    pipeline = OnboardingIngestionPipeline(
        progress_update=progress_update,
        notify_event=notify_event,
        session_factory=lambda: _SessionContext(db_session),
        sync_factory=lambda: sync,
    )

    await pipeline.hydrate_history(SimpleNamespace(id=77), progress)

    assert sync.bootstrap_kwargs["visible_limit"] == 50
    assert sync.prefetch_kwargs["max_conversations"] == 50
    assert sync.prefetch_kwargs["page_limit"] == 12
    assert progress["visible_dialog_limit"] == 50
    assert progress["history_learning_conversation_limit"] == 50
    assert progress["history_learning_message_limit"] == 12
    assert progress["history_prefetched_conversations"] == 50
    assert progress["history_replayed_conversations"] == 0
    assert progress["history_replayed_messages"] == 0
    assert any(event["kind"] == "history_prefetched" for event in events)
    history_event = next(event for event in events if event["kind"] == "history_prefetched")
    assert history_event["replayed_conversations"] == 0
    assert history_event["replayed_messages"] == 0
    assert progress["contacts_found"] == 50


@pytest.mark.asyncio
async def test_classify_workspace_contacts_uses_latest_50_conversations(
    db_session: AsyncSession,
    workspace: Workspace,
):
    progress = _base_progress()
    classified_names: list[str] = []

    async def progress_update(current, **updates):
        current.update(updates)
        return current

    async def notify_event(_workspace_id, _event):
        return None

    now = datetime.now(timezone.utc)
    for index in range(60):
        customer = Customer(
            workspace_id=workspace.id,
            telegram_id=910000 + index,
            display_name=f"Customer {index:02d}",
        )
        db_session.add(customer)
        await db_session.flush()
        db_session.add(
            Conversation(
                workspace_id=workspace.id,
                customer_id=customer.id,
                channel="telegram_dm",
                telegram_chat_id=910000 + index,
                external_chat_id=str(910000 + index),
                last_message_at=now - timedelta(minutes=index),
            )
        )
    await db_session.flush()

    async def classify_contacts(contacts):
        classified_names.extend(item["display_name"] for item in contacts)
        return [
            SimpleNamespace(contact_type="customer", confidence=0.91)
            for _ in contacts
        ]

    pipeline = OnboardingIngestionPipeline(
        progress_update=progress_update,
        notify_event=notify_event,
        session_factory=lambda: _SessionContext(db_session),
    )
    with patch(
        "app.services.contact_classifier.classify_contacts_batch_v2",
        new=classify_contacts,
    ):
        await pipeline.classify_workspace_contacts(workspace.id, progress)

    assert len(classified_names) == 50
    assert classified_names[:3] == ["Customer 00", "Customer 01", "Customer 02"]
    assert "Customer 50" not in classified_names
    assert progress["customers_identified"] == 50


@pytest.mark.asyncio
async def test_pipeline_degrades_contact_classification_but_still_builds_voice_profile():
    progress = _base_progress()
    progress_snapshots = []
    events = []

    async def progress_update(current, **updates):
        current.update(updates)
        progress_snapshots.append(dict(current))
        return current

    async def notify_event(workspace_id, event):
        events.append((workspace_id, event))

    profile = SimpleNamespace(
        message_count_analyzed=8,
        voice_card={"primary_language": "uz", "script": "latin"},
        quality_score="weak",
        message_pattern="splitter",
        burst_count=2,
    )
    pipeline = OnboardingIngestionPipeline(
        progress_update=progress_update,
        notify_event=notify_event,
    )
    pipeline.hydrate_history = AsyncMock()
    pipeline.classify_workspace_contacts = AsyncMock(side_effect=RuntimeError("llm down"))
    pipeline.generate_voice_profile = AsyncMock(return_value=profile)

    await pipeline.run(SimpleNamespace(id=77), progress)

    pipeline.hydrate_history.assert_awaited_once()
    pipeline.classify_workspace_contacts.assert_awaited_once_with(77, progress)
    pipeline.generate_voice_profile.assert_awaited_once()
    assert any(event["kind"] == "contact_classification_degraded" for _, event in events)
    assert any(event["kind"] == "voice_done" for _, event in events)
    assert progress["phase"] == "awaiting_channels"
    assert progress["percent"] == 65
    assert progress["voice_profile_ready"] is True
    assert progress["voice_profile_degraded"] is False
    assert progress["contact_classification_degraded"] is True
    assert progress["ai_learning_degraded"] is True
    assert progress["ai_learning_error"] == CONTACT_CLASSIFICATION_DEGRADED
    assert progress["errors"] == [CONTACT_CLASSIFICATION_DEGRADED]
    assert progress["voice_discoveries"][1]["label"] == "8 ta xabar tahlil qilindi"
    assert progress_snapshots[-1]["voice_profile_ready"] is True


@pytest.mark.asyncio
async def test_pipeline_marks_voice_profile_degraded_when_generation_fails():
    progress = _base_progress()
    progress_snapshots = []
    events = []

    async def progress_update(current, **updates):
        current.update(updates)
        progress_snapshots.append(dict(current))
        return current

    async def notify_event(workspace_id, event):
        events.append((workspace_id, event))

    pipeline = OnboardingIngestionPipeline(
        progress_update=progress_update,
        notify_event=notify_event,
    )
    pipeline.hydrate_history = AsyncMock()
    pipeline.classify_workspace_contacts = AsyncMock()
    pipeline.generate_voice_profile = AsyncMock(side_effect=RuntimeError("quota exhausted"))

    await pipeline.run(SimpleNamespace(id=77), progress)

    pipeline.generate_voice_profile.assert_awaited_once()
    assert any(event["kind"] == "voice_start" for _, event in events)
    degraded_events = [event for _, event in events if event["kind"] == "voice_profile_degraded"]
    assert degraded_events
    assert degraded_events[0]["retryable"] is True
    assert degraded_events[0]["reason"] == "generation_failed"
    assert not any(event["kind"] == "voice_done" for _, event in events)
    assert progress["phase"] == "reading_dialogs"
    assert progress["percent"] == 55
    assert progress["voice_profile_ready"] is False
    assert progress["voice_profile_degraded"] is True
    assert progress["voice_profile_error"] == VOICE_PROFILE_DEGRADED
    assert progress["ai_learning_degraded"] is True
    assert progress["ai_learning_error"] == VOICE_PROFILE_DEGRADED
    assert progress["errors"] == [VOICE_PROFILE_DEGRADED]
    assert progress_snapshots[-1]["voice_profile_degraded"] is True


@pytest.mark.asyncio
async def test_pipeline_marks_voice_profile_degraded_when_profile_has_no_signal():
    progress = _base_progress()
    events = []

    async def progress_update(current, **updates):
        current.update(updates)
        return current

    async def notify_event(workspace_id, event):
        events.append((workspace_id, event))

    profile = SimpleNamespace(
        message_count_analyzed=0,
        voice_card={},
        quality_score="weak",
        message_pattern="one_shot",
        burst_count=1,
    )
    pipeline = OnboardingIngestionPipeline(
        progress_update=progress_update,
        notify_event=notify_event,
    )
    pipeline.hydrate_history = AsyncMock()
    pipeline.classify_workspace_contacts = AsyncMock()
    pipeline.generate_voice_profile = AsyncMock(return_value=profile)

    await pipeline.run(SimpleNamespace(id=77), progress)

    voice_done = [event for _, event in events if event["kind"] == "voice_done"]
    assert voice_done and voice_done[0]["ready"] is False
    assert any(event["kind"] == "voice_profile_degraded" for _, event in events)
    assert progress["phase"] == "reading_dialogs"
    assert progress["percent"] == 55
    assert progress["voice_profile_ready"] is False
    assert progress["voice_profile_degraded"] is True
    assert progress["errors"] == [VOICE_PROFILE_DEGRADED]


@pytest.mark.asyncio
async def test_generate_voice_profile_uses_business_brain_voice_learning(
    db_session: AsyncSession,
    workspace: Workspace,
):
    snapshot = SimpleNamespace(
        workspace_id=workspace.id,
        voice_card={"primary_language": "uz", "script": "latin"},
        message_pattern="one_shot",
        burst_count=1,
        quality_score="weak",
        message_count_analyzed=3,
        accepted_observations=1,
        degraded_reasons=[],
    )
    pipeline = OnboardingIngestionPipeline(
        progress_update=AsyncMock(),
        notify_event=AsyncMock(),
        session_factory=lambda: _SessionContext(db_session),
    )

    with patch(
        "app.modules.business_brain.BusinessVoiceLearningService.learn_from_history",
        new=AsyncMock(return_value=snapshot),
    ) as learn_from_history:
        result = await pipeline.generate_voice_profile(workspace)

    assert result is snapshot
    learn_from_history.assert_awaited_once_with(
        workspace_id=workspace.id,
        correlation_id=f"onboarding:voice_profile:{workspace.id}",
        idempotency_key=f"onboarding:voice_profile:{workspace.id}",
        limit=50,
    )
