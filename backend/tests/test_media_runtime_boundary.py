"""Media runtime boundary tests.

These tests pin the durable Sprint 4 behaviors that should survive refactors:
- persisted AI-relevant media starts in descriptor-first pending state
- hydration can move the same message through deferred -> hydrated
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.media_runtime import MediaRuntime
from app.modules.conversation_core.service import PersistMessageInput, persist_message
from app.services.channel_media_access import ChannelMediaAccess
from app.services.channel_sync_runtime import ChannelSyncRateLimitError

pytestmark = pytest.mark.asyncio


class TestMediaRuntimeBoundary:
    async def test_voice_message_transitions_from_pending_to_deferred_to_hydrated(
        self,
        db_session,
        workspace,
    ):
        persisted = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                channel="telegram_dm",
                telegram_chat_id=321654,
                sender_id=99077,
                sender_name="Boundary Voice",
                text="[voice] Mijoz ovozli xabar yubordi",
                is_outgoing=False,
                telegram_message_id=8801,
                media_type="voice",
            ),
        )
        conversation = persisted.conversation
        message = persisted.message
        service = ChannelMediaAccess()

        assert message.media_metadata["hydration_status"] == "pending"
        assert message.media_metadata["ai_relevant"] is True
        assert message.media_metadata["media_runtime"] == {
            "asset_state": "metadata_only",
            "semantic_state": "pending",
            "ai_relevant": True,
        }

        async def deferred_fetch():
            raise ChannelSyncRateLimitError(
                retry_after_seconds=7.5,
                channel="telegram_dm",
                operation="media",
            )

        deferred = await service.hydrate_for_ai(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            message=message,
            fetch_media=deferred_fetch,
        )

        assert deferred.status == "deferred"
        assert deferred.retry_after_seconds == 7.5
        assert message.media_metadata["hydration_status"] == "deferred"
        assert message.media_metadata["retry_after_seconds"] == 7.5
        assert message.media_metadata["media_runtime"]["asset_state"] == "retrying"
        assert message.media_metadata["media_runtime"]["semantic_state"] == "retrying"
        assert message.content == "[voice] Mijoz ovozli xabar yubordi"

        async def hydrated_fetch():
            return b"voice-bytes", "audio/ogg"

        with patch(
            "app.modules.extraction_runtime.media_semantics.normalize_voice_message",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(text="Assalomu alaykum, narxi qancha?", confidence=0.91),
        ):
            hydrated = await service.hydrate_for_ai(
                session=db_session,
                workspace_id=workspace.id,
                conversation=conversation,
                message=message,
                fetch_media=hydrated_fetch,
            )

        assert hydrated.status == "hydrated"
        assert hydrated.normalized_text == "Assalomu alaykum, narxi qancha?"
        assert message.transcription == "Assalomu alaykum, narxi qancha?"
        assert message.transcription_confidence == 0.91
        assert message.content == "Assalomu alaykum, narxi qancha?"
        assert message.media_metadata["hydration_status"] == "hydrated"
        assert message.media_metadata["media_runtime"]["asset_state"] == "stream_ready"
        assert message.media_metadata["media_runtime"]["semantic_state"] == "ready"
        assert message.media_metadata["normalized_text"] == "Assalomu alaykum, narxi qancha?"
        assert message.media_metadata["mime_type"] == "audio/ogg"
        assert "retry_after_seconds" not in message.media_metadata

    async def test_rate_limited_media_exhausts_retry_budget_to_unavailable(
        self,
        db_session,
        workspace,
    ):
        persisted = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                channel="telegram_dm",
                telegram_chat_id=321656,
                sender_id=99079,
                sender_name="Retry Budget",
                text="[photo] Mijoz rasm yubordi",
                is_outgoing=False,
                telegram_message_id=8803,
                media_type="photo",
            ),
        )
        service = ChannelMediaAccess()

        async def rate_limited_fetch():
            raise ChannelSyncRateLimitError(
                retry_after_seconds=1.0,
                channel="telegram_dm",
                operation="media",
            )

        first = await service.hydrate_for_ai(
            session=db_session,
            workspace_id=workspace.id,
            conversation=persisted.conversation,
            message=persisted.message,
            fetch_media=rate_limited_fetch,
        )
        second = await service.hydrate_for_ai(
            session=db_session,
            workspace_id=workspace.id,
            conversation=persisted.conversation,
            message=persisted.message,
            fetch_media=rate_limited_fetch,
        )
        third = await service.hydrate_for_ai(
            session=db_session,
            workspace_id=workspace.id,
            conversation=persisted.conversation,
            message=persisted.message,
            fetch_media=rate_limited_fetch,
        )

        runtime = await db_session.scalar(
            select(MediaRuntime).where(MediaRuntime.message_id == persisted.message.id)
        )
        assert first.status == "deferred"
        assert second.status == "deferred"
        assert third.status == "unavailable"
        assert runtime is not None
        assert runtime.attempt_count == runtime.max_attempts == 3
        assert runtime.action_state == "failed"
        assert runtime.hydration_status == "unavailable"
        assert runtime.asset_state == "unavailable"
        assert runtime.semantic_state == "unavailable"
        assert runtime.next_attempt_at is None
        assert runtime.completed_at is not None
        assert persisted.message.media_metadata["hydration_status"] == "unavailable"
        assert persisted.message.media_metadata["media_runtime"] == {
            "asset_state": "unavailable",
            "semantic_state": "unavailable",
            "ai_relevant": True,
        }
        assert "retry_after_seconds" not in persisted.message.media_metadata

    async def test_unavailable_media_has_explicit_degraded_runtime_state(
        self,
        db_session,
        workspace,
    ):
        persisted = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                channel="telegram_dm",
                telegram_chat_id=321655,
                sender_id=99078,
                sender_name="Boundary Video",
                text="[video] Mijoz video yubordi",
                is_outgoing=False,
                telegram_message_id=8802,
                media_type="video",
            ),
        )

        async def unavailable_fetch():
            return None

        unavailable = await ChannelMediaAccess().hydrate_for_ai(
            session=db_session,
            workspace_id=workspace.id,
            conversation=persisted.conversation,
            message=persisted.message,
            fetch_media=unavailable_fetch,
        )

        assert unavailable.status == "unavailable"
        assert persisted.message.media_metadata["hydration_status"] == "unavailable"
        assert persisted.message.media_metadata["media_runtime"] == {
            "asset_state": "unavailable",
            "semantic_state": "unavailable",
            "ai_relevant": True,
        }
