"""Onboarding ingestion pipeline.

The HTTP route owns auth and task spawning. This module owns the onboarding
runtime sequence so LLM-heavy phases can degrade without blocking message
visibility or turning route code into the source of truth.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import async_session
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message
from app.models.workspace import Workspace
from app.services.channel_conversation_sync import ChannelConversationSync

logger = get_logger("services.onboarding_ingestion")

VOICE_PROFILE_MIN_MESSAGES = 1
ONBOARDING_VISIBLE_DIALOG_LIMIT = 50
ONBOARDING_HISTORY_LEARNING_CONVERSATION_LIMIT = 50
ONBOARDING_HISTORY_LEARNING_MESSAGE_LIMIT = 12
CONTACT_CLASSIFICATION_DEGRADED = "contact_classification_degraded"
VOICE_PROFILE_DEGRADED = "voice_profile_degraded"


class ProgressUpdater(Protocol):
    async def __call__(self, progress: dict[str, Any], **updates: Any) -> dict[str, Any]:
        ...


EventNotifier = Callable[[int, dict[str, Any]], Awaitable[None]]


def voice_profile_has_real_signal(profile: Any | None) -> bool:
    return bool(profile and (profile.message_count_analyzed or 0) >= VOICE_PROFILE_MIN_MESSAGES)


def voice_discoveries_from_profile(profile: Any) -> list[dict[str, str | int]]:
    voice_card = profile.voice_card or {}
    discoveries: list[dict[str, str | int]] = []
    language = voice_card.get("primary_language")
    script = voice_card.get("script")
    if language:
        discoveries.append({
            "icon": "globe",
            "label": "Til",
            "subtitle": f"{language.upper()}" + (f" · {script}" if script else ""),
        })
    discoveries.append({
        "icon": "chat",
        "label": f"{profile.message_count_analyzed} ta xabar tahlil qilindi",
        "subtitle": f"Sifat: {profile.quality_score}",
    })
    if profile.message_pattern:
        discoveries.append({
            "icon": "sparkle",
            "label": f"Yozish usuli: {profile.message_pattern}",
            "subtitle": f"burst={profile.burst_count}",
        })
    return discoveries


class OnboardingIngestionPipeline:
    """Deep module for onboarding history + AI bootstrap."""

    def __init__(
        self,
        *,
        progress_update: ProgressUpdater,
        notify_event: EventNotifier,
        session_factory=async_session,
        sync_factory=ChannelConversationSync,
    ) -> None:
        self._progress_update = progress_update
        self._notify_event = notify_event
        self._session_factory = session_factory
        self._sync_factory = sync_factory

    async def run(self, workspace: Workspace, progress: dict[str, Any]) -> None:
        await self.hydrate_history(workspace, progress)

        try:
            await self.classify_workspace_contacts(workspace.id, progress)
        except Exception as exc:
            logger.warning(
                "onboarding contact classification degraded workspace=%d error=%s",
                workspace.id,
                exc,
                exc_info=True,
            )
            errors = list(progress.get("errors") or [])
            if CONTACT_CLASSIFICATION_DEGRADED not in errors:
                errors.append(CONTACT_CLASSIFICATION_DEGRADED)
            await self._progress_update(
                progress,
                contact_classification_degraded=True,
                ai_learning_degraded=True,
                ai_learning_error=CONTACT_CLASSIFICATION_DEGRADED,
                errors=errors,
            )
            await self._notify_event(workspace.id, {
                "kind": "contact_classification_degraded",
                "error": str(exc),
                "retryable": True,
            })

        await self._notify_event(workspace.id, {
            "kind": "voice_start",
            "customers": progress["customers_identified"],
        })
        await self._progress_update(progress, phase="generating_voice_profile", percent=60)

        try:
            profile = await self.generate_voice_profile(workspace)
        except Exception as exc:
            logger.warning(
                "onboarding voice profile degraded workspace=%d error=%s",
                workspace.id,
                exc,
                exc_info=True,
            )
            errors = list(progress.get("errors") or [])
            if VOICE_PROFILE_DEGRADED not in errors:
                errors.append(VOICE_PROFILE_DEGRADED)
            await self._notify_event(workspace.id, {
                "kind": "voice_profile_degraded",
                "reason": "generation_failed",
                "error": str(exc),
                "retryable": True,
                "messages_analyzed": 0,
            })
            await self._progress_update(
                progress,
                phase="reading_dialogs",
                percent=55,
                voice_profile_ready=False,
                voice_profile_degraded=True,
                voice_profile_error=VOICE_PROFILE_DEGRADED,
                ai_learning_degraded=True,
                ai_learning_error=VOICE_PROFILE_DEGRADED,
                completed=False,
                errors=errors,
            )
            return

        discoveries = voice_discoveries_from_profile(profile)
        voice_ready = voice_profile_has_real_signal(profile)
        await self._notify_event(workspace.id, {
            "kind": "voice_done",
            "discoveries": discoveries,
            "messages_analyzed": profile.message_count_analyzed,
            "pattern": profile.message_pattern,
            "burst_count": profile.burst_count,
            "ready": voice_ready,
        })
        if not voice_ready:
            errors = list(progress.get("errors") or [])
            if VOICE_PROFILE_DEGRADED not in errors:
                errors.append(VOICE_PROFILE_DEGRADED)
            await self._notify_event(workspace.id, {
                "kind": "voice_profile_degraded",
                "reason": "not_enough_signal",
                "retryable": True,
                "messages_analyzed": profile.message_count_analyzed,
            })
        else:
            errors = list(progress.get("errors") or [])
        await self._progress_update(
            progress,
            phase="awaiting_channels" if voice_ready else "reading_dialogs",
            percent=65 if voice_ready else 55,
            voice_profile_ready=voice_ready,
            voice_profile_degraded=not voice_ready,
            voice_profile_error=None if voice_ready else VOICE_PROFILE_DEGRADED,
            voice_discoveries=discoveries,
            ai_learning_degraded=bool(progress.get("ai_learning_degraded")) or not voice_ready,
            ai_learning_error=progress.get("ai_learning_error") or (None if voice_ready else VOICE_PROFILE_DEGRADED),
            completed=False,
            errors=errors,
        )

    async def hydrate_history(self, workspace: Workspace, progress: dict[str, Any]) -> None:
        sync = self._sync_factory()
        async with self._session_factory() as db:
            result = await sync.bootstrap_inbox(
                session=db,
                workspace_id=workspace.id,
                channel="telegram_dm",
                # Dialog shells keep the inbox visible. Expensive learning below
                # is intentionally capped to the latest 50 conversations.
                visible_limit=ONBOARDING_VISIBLE_DIALOG_LIMIT,
            )
            await self._progress_update(
                progress,
                phase="reading_dialogs",
                percent=35,
                contacts_found=result.synced_count,
                visible_dialog_limit=ONBOARDING_VISIBLE_DIALOG_LIMIT,
                history_learning_conversation_limit=ONBOARDING_HISTORY_LEARNING_CONVERSATION_LIMIT,
                history_learning_message_limit=ONBOARDING_HISTORY_LEARNING_MESSAGE_LIMIT,
            )
            prefetch = await sync.prefetch_recent_history(
                session=db,
                workspace_id=workspace.id,
                channel="telegram_dm",
                max_conversations=ONBOARDING_HISTORY_LEARNING_CONVERSATION_LIMIT,
                page_limit=ONBOARDING_HISTORY_LEARNING_MESSAGE_LIMIT,
            )
            await self._progress_update(
                progress,
                phase="reading_dialogs",
                percent=42,
                contacts_found=result.synced_count,
                visible_dialog_limit=ONBOARDING_VISIBLE_DIALOG_LIMIT,
                history_learning_conversation_limit=ONBOARDING_HISTORY_LEARNING_CONVERSATION_LIMIT,
                history_learning_message_limit=ONBOARDING_HISTORY_LEARNING_MESSAGE_LIMIT,
                history_prefetched_conversations=prefetch.prefetched_conversations,
                history_replayed_conversations=0,
                history_replayed_messages=0,
            )

        await self._notify_event(workspace.id, {
            "kind": "dialogs_loaded",
            "contacts": result.synced_count,
            "channels": 0,
        })
        await self._notify_event(workspace.id, {
            "kind": "history_prefetched",
            "conversations": prefetch.prefetched_conversations,
            "messages": prefetch.persisted_messages,
            "replayed_conversations": 0,
            "replayed_messages": 0,
            "deferred": prefetch.deferred,
        })
        await self._progress_update(
            progress,
            phase="reading_dialogs",
            percent=42,
            contacts_found=result.synced_count,
            visible_dialog_limit=ONBOARDING_VISIBLE_DIALOG_LIMIT,
            history_learning_conversation_limit=ONBOARDING_HISTORY_LEARNING_CONVERSATION_LIMIT,
            history_learning_message_limit=ONBOARDING_HISTORY_LEARNING_MESSAGE_LIMIT,
            history_prefetched_conversations=prefetch.prefetched_conversations,
            history_replayed_conversations=0,
            history_replayed_messages=0,
        )

    async def classify_workspace_contacts(self, workspace_id: int, progress: dict[str, Any]) -> None:
        from app.services.contact_classifier import classify_contacts_batch_v2

        await self._progress_update(progress, phase="classifying_contacts", percent=45)

        async with self._session_factory() as db:
            result = await db.execute(
                select(Conversation, Customer)
                .join(Customer, Customer.id == Conversation.customer_id)
                .where(Conversation.workspace_id == workspace_id)
                .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
                .limit(ONBOARDING_HISTORY_LEARNING_CONVERSATION_LIMIT)
            )
            rows = result.all()
            if not rows:
                return

            contacts: list[dict[str, Any]] = []
            customers: list[Customer] = []
            for conversation, customer in rows:
                contacts.append({
                    "display_name": customer.display_name or "Unknown",
                    "is_group": False,
                    "is_bot": False,
                    "messages": await self._load_messages_for_conversation(db, conversation.id),
                })
                customers.append(customer)

            classifications = await classify_contacts_batch_v2(contacts)
            customer_count = 0

            for index, (customer, classification) in enumerate(zip(customers, classifications), start=1):
                customer.contact_type = classification.contact_type
                customer.classification_confidence = classification.confidence
                if classification.contact_type == "customer":
                    customer_count += 1
                await self._notify_event(workspace_id, {
                    "kind": "contact_classified",
                    "name": customer.display_name or "Unknown",
                    "type": classification.contact_type,
                    "is_customer": classification.contact_type == "customer",
                    "confidence": classification.confidence,
                    "index": index,
                    "total": len(customers),
                })

            await db.commit()
            await self._progress_update(progress, customers_identified=customer_count)

    async def generate_voice_profile(self, workspace: Workspace):
        from app.modules.business_brain.voice_learning import BusinessVoiceLearningService
        from app.modules.commercial_spine.repository import CommercialSpineRepository

        async with self._session_factory() as db:
            service = BusinessVoiceLearningService(
                repository=CommercialSpineRepository(db)
            )
            profile = await service.learn_from_history(
                workspace_id=workspace.id,
                correlation_id=f"onboarding:voice_profile:{workspace.id}",
                idempotency_key=f"onboarding:voice_profile:{workspace.id}",
                limit=ONBOARDING_HISTORY_LEARNING_CONVERSATION_LIMIT,
            )
            await db.commit()
            return profile

    async def _load_messages_for_conversation(
        self,
        db,
        conversation_id: int,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.telegram_timestamp.desc().nullslast(), Message.id.desc())
            .limit(limit)
        )
        rows = list(result.scalars())
        rows.reverse()
        return [
            {
                "text": msg.content or "",
                "is_outgoing": msg.sender_type == "seller",
                "media_type": msg.media_type,
            }
            for msg in rows
        ]
