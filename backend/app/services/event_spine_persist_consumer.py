"""Event Spine persistence consumer.

Consumes canonical EventSpine entries and writes them through Conversation Core.
This is the default authoritative persistence path for canonical channel
events. Route-time repair and legacy consumers must remain safety tooling, not
the owner of message truth.
"""

from __future__ import annotations

import asyncio
import logging
import time
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import func, select, update

from app.core.async_tasks import spawn_guarded_task
from app.core.config import get_settings
from app.core.consumer_names import make_consumer_name
from app.core.event_spine import (
    BackfillWindowApplied,
    CrmUpdated,
    DeliveryConfirmed,
    DeliveryFailed,
    DeliveryUnknown,
    Event,
    EventSpine,
    FollowUpScheduled,
    MediaHydrationStateChanged,
    MsgDeleted,
    MsgEdited,
    MsgInbound,
    MsgMediaSent,
    MsgSent,
    ReadReceipt,
)
from app.core.redis_streams import reclaim_stale_pending_entries
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.delivery_runtime import DeliveryRuntime
from app.models.media_runtime import MediaRuntime
from app.models.message import Message, SenderType
from app.models.workspace import Workspace
from app.modules.agent_sessions.hot_path import AgentSessionHotPathService
from app.modules.agent_talking.presence import TalkPresenceService
from app.modules.conversation_core.service import (
    PersistMessageInput,
    PersistMessageResult,
    bump_conversation_revision,
    create_seller_placeholder_message,
    persist_message,
)
from app.modules.conversation_turns.service import ConversationTurnSessionService
from app.modules.hermes_runtime.lane_limiter import HermesLaneLimiter
from app.modules.message_intake.classifier import classify_local
from app.services.action_runtime import ACTION_SUCCESS, record_action_state
from app.services.conversation_state import (
    ConversationDialogState,
    ConversationFollowUpState,
    ConversationSyncState,
    ConversationSyncWatermarks,
    get_customer_conversation_state,
    refresh_customer_conversation_state,
    resolved_pipeline_stage,
    set_customer_conversation_state,
)
from app.services.delivery_runtime import (
    DELIVERY_CONFIRMED,
    DELIVERY_FAILED,
    DELIVERY_RECONCILED,
    DELIVERY_REQUESTED,
    DELIVERY_UNKNOWN,
    record_delivery_state,
)
from app.services.inbound_pipeline import recover_catch_up_window
from app.services.media_types import normalize_media_type
from app.services.message_response_projection import build_delivery_runtime_response

logger = logging.getLogger("oqim_business.event_spine_persist_consumer")
HOT_INBOUND_PRESENCE_TIMEOUT_SECONDS = 0.8

GROUP_NAME = "persist"
STREAM_KEY_PREFIX = "oqim:events:"
BLOCK_MS = 1000
READ_COUNT = 100
PERSIST_TIMEOUT_SECONDS = 2.0
WORKSPACE_LIST_REFRESH_SECONDS = 30.0
UNSUPPORTED_COUNTER_PREFIX = "oqim:event_spine:persist:unsupported:"
PROCESSED_COUNTER_PREFIX = "oqim:event_spine:persist:processed:"

# Telegram system peers that must never become customers/conversations.
SYSTEM_TELEGRAM_PEER_IDS: frozenset[str] = frozenset({"93372553", "777000"})  # BotFather, Telegram service
MISSING_COUNTER_PREFIX = "oqim:event_spine:persist:missing:"
SHADOW_COUNTER_PREFIX = "oqim:event_spine:persist:shadow:"

_event_adapter: TypeAdapter[Event] = TypeAdapter(Event)


@dataclass(slots=True)
class EventReplayResult:
    events_seen: int
    events_applied: int
    events_missing_projection: int
    events_unsupported: int


class EventSpinePersistConsumer:
    """Supervised consumer that persists canonical message events."""

    def __init__(
        self,
        *,
        redis: Any,
        db_factory: Callable[[], Any],
        workspace_ids_provider: Callable[[], list[int]] | None = None,
        conversation_turn_runner: Any | None = None,
        mode: str = "shadow",
        background_side_effects: bool = True,
    ) -> None:
        self._redis = redis
        self._db_factory = db_factory
        self._workspace_ids_provider = workspace_ids_provider or self._scan_workspace_ids
        self._consumer_name = make_consumer_name("event_spine_persist")
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None
        self._workspace_ids_cache: list[int] = []
        self._cache_refreshed_at = 0.0
        self._conversation_turn_runner = conversation_turn_runner
        self._mode = mode.strip().lower()
        self._background_side_effects = background_side_effects
        self._background_tasks: set[asyncio.Task] = set()
        self._lane_limiter = HermesLaneLimiter()

    @property
    def _authoritative(self) -> bool:
        return self._mode == "authoritative"

    # --- Supervisor protocol ------------------------------------------------

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._heartbeat_callback = callback

    async def start(self) -> None:
        self._stopping = False
        await self._ensure_groups()
        while not self._stopping:
            try:
                await self._reclaim_stale()
                count = await self._run_once(block_ms=BLOCK_MS)
                self._beat()
                if count == 0:
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("event_spine.persist_consumer_tick_failed", exc_info=exc)
                await asyncio.sleep(2.0)

    async def stop(self) -> None:
        self._stopping = True

    # --- Stream handling ----------------------------------------------------

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()

    async def _scan_workspace_ids(self) -> list[int]:
        ids: set[int] = set()
        async for key in self._redis.scan_iter(match=f"{STREAM_KEY_PREFIX}*"):
            try:
                ids.add(int(key.rsplit(":", 1)[-1]))
            except ValueError:
                continue
        return sorted(ids)

    async def _workspace_ids(self) -> list[int]:
        if time_module.monotonic() - self._cache_refreshed_at > WORKSPACE_LIST_REFRESH_SECONDS:
            result = self._workspace_ids_provider()
            if asyncio.iscoroutine(result):
                result = await result
            self._workspace_ids_cache = list(result)
            self._cache_refreshed_at = time_module.monotonic()
        return self._workspace_ids_cache

    async def observe_workspace(self, workspace_id: int) -> None:
        """Make a freshly active workspace stream visible without waiting for cache refresh."""
        if workspace_id <= 0:
            return
        if workspace_id not in self._workspace_ids_cache:
            self._workspace_ids_cache = sorted([*self._workspace_ids_cache, workspace_id])
        if self._cache_refreshed_at <= 0:
            self._cache_refreshed_at = time_module.monotonic()
        await self._ensure_group_for_workspace(workspace_id)

    async def _ensure_group_for_workspace(self, ws_id: int) -> None:
        key = f"{STREAM_KEY_PREFIX}{ws_id}"
        try:
            await self._redis.xgroup_create(key, GROUP_NAME, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                logger.warning("xgroup_create failed for %s: %s", key, exc)

    async def _ensure_groups(self) -> None:
        for ws_id in await self._workspace_ids():
            await self._ensure_group_for_workspace(ws_id)

    async def _reclaim_stale(self) -> int:
        processed = 0
        for ws_id in await self._workspace_ids():
            key = f"{STREAM_KEY_PREFIX}{ws_id}"
            entries = await reclaim_stale_pending_entries(
                self._redis,
                stream_key=key,
                group_name=GROUP_NAME,
                consumer_name=self._consumer_name,
                count=READ_COUNT,
            )
            for stream_id, fields in entries:
                if await self._handle_entry(key, stream_id, fields):
                    processed += 1
        return processed

    async def _run_once(self, block_ms: int = BLOCK_MS) -> int:
        ws_ids = await self._workspace_ids()
        if not ws_ids:
            return 0
        streams = {f"{STREAM_KEY_PREFIX}{ws_id}": ">" for ws_id in ws_ids}
        try:
            response = await self._redis.xreadgroup(
                GROUP_NAME,
                self._consumer_name,
                streams,
                count=READ_COUNT,
                block=block_ms,
            )
        except Exception as exc:
            if "NOGROUP" not in str(exc):
                raise
            logger.warning("event_spine.persist_consumer_missing_group_recovered", exc_info=exc)
            self._cache_refreshed_at = 0.0
            await self._ensure_groups()
            response = await self._redis.xreadgroup(
                GROUP_NAME,
                self._consumer_name,
                streams,
                count=READ_COUNT,
                block=block_ms,
            )

        processed = 0
        for stream_key, entries in response:
            for stream_id, fields in entries:
                if await self._handle_entry(stream_key, stream_id, fields):
                    processed += 1
        return processed

    async def _handle_entry(self, stream_key: str, stream_id: str, fields: dict) -> bool:
        try:
            await asyncio.wait_for(
                self._dispatch(stream_key, stream_id, fields),
                timeout=PERSIST_TIMEOUT_SECONDS,
            )
            await self._redis.xack(stream_key, GROUP_NAME, stream_id)
            return True
        except TimeoutError:
            logger.warning(
                "event_spine.persist_timeout",
                extra={"stream_key": stream_key, "stream_id": stream_id},
            )
        except Exception as exc:
            logger.error(
                "event_spine.persist_error",
                extra={"stream_key": stream_key, "stream_id": stream_id},
                exc_info=exc,
            )
        return False

    async def _dispatch(self, stream_key: str, stream_id: str, fields: dict) -> None:
        try:
            event = _event_adapter.validate_json(fields["payload"])
        except Exception as exc:
            logger.warning(
                "event_spine.persist_event_malformed",
                extra={"stream_key": stream_key, "stream_id": stream_id, "error": str(exc)},
            )
            return

        async with self._db_factory() as session:
            await self._apply_event(session, event, run_side_effects=True)

    async def _apply_event(
        self,
        session: Any,
        event: Event,
        *,
        run_side_effects: bool,
    ) -> bool | None:
        if event.schema_version != 1:
            await self._redis.incr(f"{UNSUPPORTED_COUNTER_PREFIX}{event.type}.v{event.schema_version}")
            return None
        if isinstance(event, MsgInbound):
            return await self._persist_inbound(
                session,
                event,
                run_side_effects=run_side_effects,
            )
        if isinstance(event, MsgEdited):
            return await self._persist_edited(
                session,
                event,
                run_side_effects=run_side_effects,
            )
        if isinstance(event, MsgDeleted):
            return await self._persist_deleted(
                session,
                event,
                run_side_effects=run_side_effects,
            )
        if isinstance(event, MsgSent):
            return await self._persist_sent(session, event)
        if isinstance(event, MsgMediaSent):
            return await self._persist_media_sent(session, event)
        if isinstance(event, DeliveryConfirmed):
            return await self._persist_delivery_confirmed(session, event)
        if isinstance(event, DeliveryUnknown):
            return await self._persist_delivery_unknown(session, event)
        if isinstance(event, DeliveryFailed):
            return await self._persist_delivery_failed(session, event)
        if isinstance(event, ReadReceipt):
            return await self._persist_read_receipt(session, event)
        if isinstance(event, FollowUpScheduled):
            return await self._persist_follow_up_scheduled(session, event)
        if isinstance(event, CrmUpdated):
            return await self._persist_crm_updated(session, event)
        if isinstance(event, BackfillWindowApplied):
            return await self._persist_backfill_window(session, event)
        if isinstance(event, MediaHydrationStateChanged):
            return await self._persist_media_hydration_state(session, event)

        await self._redis.incr(f"{UNSUPPORTED_COUNTER_PREFIX}{event.type}")
        return None

    async def _is_system_or_control_bot_peer(self, session: Any, event: MsgInbound) -> bool:
        chat_id = str(event.telegram_chat_id or "")
        if not chat_id:
            return False
        if chat_id in SYSTEM_TELEGRAM_PEER_IDS:
            return True
        cache: dict[int, tuple[float, int | None]] = getattr(
            self, "_control_bot_id_cache", None
        ) or {}
        self._control_bot_id_cache = cache
        now = time.monotonic()
        cached = cache.get(int(event.workspace_id))
        if cached is not None and now - cached[0] < 60.0:
            control_bot_id = cached[1]
        else:
            from app.models.workspace import Workspace

            control_bot_id = await session.scalar(
                select(Workspace.control_bot_user_id).where(Workspace.id == event.workspace_id)
            )
            cache[int(event.workspace_id)] = (now, control_bot_id)
        return control_bot_id is not None and chat_id == str(control_bot_id)

    async def replay_conversation(
        self,
        *,
        workspace_id: int,
        channel: str,
        channel_conversation_id: str,
    ) -> EventReplayResult:
        """Rebuild one conversation projection from canonical events only."""
        events = await EventSpine(
            self._redis,
            db_factory=self._db_factory,
        ).replay_conversation(
            workspace_id=workspace_id,
            channel=channel,
            channel_conversation_id=channel_conversation_id,
        )
        applied = 0
        missing = 0
        unsupported = 0
        async with self._db_factory() as session:
            for event in events:
                outcome = await self._apply_event(
                    session,
                    event,
                    run_side_effects=False,
                )
                if outcome is True:
                    applied += 1
                elif outcome is False:
                    missing += 1
                else:
                    unsupported += 1
        return EventReplayResult(
            events_seen=len(events),
            events_applied=applied,
            events_missing_projection=missing,
            events_unsupported=unsupported,
        )

    async def _persist_inbound(
        self,
        session: Any,
        event: MsgInbound,
        *,
        run_side_effects: bool = True,
    ) -> bool:
        if await self._is_system_or_control_bot_peer(session, event):
            # Bots are never customers. The sidecar's hot-path bot filter only
            # works on cached entities, so a fresh bot peer (e.g. the
            # workspace's own self-provisioned control bot) can leak through —
            # live incident: agent<->control-bot infinite loop in conv 4
            # (2026-06-10). This is the authoritative backstop.
            await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.inbound.system_peer_skipped")
            return True
        payload = _inbound_to_persist_input(event)
        result = await persist_message(session, payload)
        event_spine_committed_at = datetime.now(UTC).timestamp()
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.inbound")
        if result.is_duplicate:
            await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.inbound.duplicate")
            if not self._authoritative:
                await self._redis.incr(f"{SHADOW_COUNTER_PREFIX}msg.inbound.legacy_duplicate")
        elif not self._authoritative:
            await self._redis.incr(f"{SHADOW_COUNTER_PREFIX}msg.inbound.inserted_without_legacy")

        await self._reconcile_outgoing_echo_delivery_runtime(session, event, result)

        if (
            run_side_effects
            and self._authoritative
            and not result.is_duplicate
            and not event.is_historical
            and not event.is_outgoing
        ):
            await _pulse_hot_inbound_presence(
                session=session,
                redis=self._redis,
                event=event,
                conversation=result.conversation,
                message=result.message,
                customer=result.customer,
            )

        if (
            run_side_effects
            and self._authoritative
            and self._conversation_turn_runner is not None
            and not event.is_historical
        ):
            if self._background_side_effects:
                self._spawn_inbound_side_effects(
                    event,
                    result,
                    event_spine_committed_at=event_spine_committed_at,
                )
            else:
                await self._run_inbound_side_effects(
                    event=event,
                    event_spine_committed_at=event_spine_committed_at,
                    message_id=result.message.id,
                    conversation_id=result.conversation.id,
                    customer_id=result.customer.id,
                    is_duplicate=result.is_duplicate,
                )
        return True

    async def _reconcile_outgoing_echo_delivery_runtime(
        self,
        session: Any,
        event: MsgInbound,
        result: PersistMessageResult,
    ) -> None:
        if not event.is_outgoing:
            return
        client_key = result.message.client_message_uuid
        if not client_key or result.message.delivery_state != "confirmed":
            return
        external_message_id = (
            result.message.external_message_id
            or str(result.message.telegram_message_id or event.telegram_message_id)
        )
        await record_delivery_state(
            session,
            workspace_id=event.workspace_id,
            conversation_id=result.conversation.id,
            message_id=result.message.id,
            channel=event.channel or result.conversation.channel or "telegram_dm",
            channel_conversation_id=event.channel_conversation_id,
            client_idempotency_key=client_key,
            state=DELIVERY_RECONCILED,
            external_message_id=external_message_id,
        )
        await session.commit()

    def _spawn_inbound_side_effects(
        self,
        event: MsgInbound,
        result: PersistMessageResult,
        *,
        event_spine_committed_at: float,
    ) -> None:
        spawn_guarded_task(
            self._run_inbound_side_effects(
                event=event,
                event_spine_committed_at=event_spine_committed_at,
                message_id=result.message.id,
                conversation_id=result.conversation.id,
                customer_id=result.customer.id,
                is_duplicate=result.is_duplicate,
            ),
            logger=logger,
            name=f"event-spine-inbound-side-effects:{event.workspace_id}:{event.telegram_chat_id}:{event.telegram_message_id}",
            registry=self._background_tasks,
        )

    async def _run_inbound_side_effects(
        self,
        *,
        event: MsgInbound,
        event_spine_committed_at: float,
        message_id: int,
        conversation_id: int,
        customer_id: int,
        is_duplicate: bool,
    ) -> None:
        async with self._lane_limiter.limit(
            lane="background",
            workspace_id=event.workspace_id,
            wait_timeout_seconds=5,
        ) as acquisition:
            if not acquisition.acquired:
                logger.warning(
                    "event_spine.inbound_side_effects_lane_timeout",
                    extra={
                        "workspace_id": event.workspace_id,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "waited_ms": acquisition.waited_ms,
                        "reason": acquisition.reason,
                    },
                )
                return
            async with self._db_factory() as session:
                workspace = await session.get(Workspace, event.workspace_id)
                conversation = await session.get(Conversation, conversation_id)
                customer = await session.get(Customer, customer_id)
                message = await session.get(Message, message_id)
                if workspace is None or conversation is None or customer is None or message is None:
                    await self._redis.incr(f"{MISSING_COUNTER_PREFIX}side_effect_projection")
                    return
                await _process_inbound_message_actions(
                    session=session,
                    redis=self._redis,
                    event=event,
                    event_spine_committed_at=event_spine_committed_at,
                    workspace=workspace,
                    conversation=conversation,
                    customer=customer,
                    message=message,
                    is_duplicate=is_duplicate,
                    conversation_turn_runner=self._conversation_turn_runner,
                )

    async def _persist_edited(
        self,
        session: Any,
        event: MsgEdited,
        *,
        run_side_effects: bool = True,
    ) -> bool:
        row = await _find_message(
            session,
            event.workspace_id,
            event.telegram_chat_id,
            event.telegram_message_id,
        )
        if row is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}msg.edited")
            await self._redis.incr(f"{SHADOW_COUNTER_PREFIX}msg.edited.missing_legacy")
            return False
        message, conversation = row
        edited_at = datetime.fromtimestamp(event.edited_at, tz=UTC)
        text_entities_changed = (
            event.text_entities is not None
            and (message.text_entities or []) != event.text_entities
        )
        if (
            message.content == event.new_text
            and not text_entities_changed
            and _same_datetime(message.edited_at, edited_at)
        ):
            await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.edited")
            if not self._authoritative:
                await self._redis.incr(f"{SHADOW_COUNTER_PREFIX}msg.edited.matched_legacy")
            return True
        previous_sender_type = message.sender_type
        message.content = event.new_text
        if event.text_entities is not None:
            message.text_entities = event.text_entities
        message.edited_at = edited_at
        _project_dialog_message_update(
            conversation,
            message,
            text=event.new_text,
            message_ts=message.telegram_timestamp or message.created_at,
        )
        session.add(message)
        expired_ids: list[int] = []
        if run_side_effects and self._authoritative:
            expired_ids = await _expire_trigger_replies(session, message_id=message.id)
        await bump_conversation_revision(session, conversation)
        await session.commit()
        if run_side_effects and self._authoritative:
            await _broadcast_message_edited(
                workspace_id=event.workspace_id,
                conversation=conversation,
                message=message,
            )
            if (
                expired_ids
                and self._conversation_turn_runner is not None
                and previous_sender_type == SenderType.CUSTOMER.value
            ):
                await self._conversation_turn_runner.enqueue_message(
                    workspace_id=event.workspace_id,
                    conversation_id=conversation.id,
                    message_id=message.id,
                )
            await _broadcast_expired_replies(
                workspace_id=event.workspace_id,
                conversation_id=conversation.id,
                reply_ids=expired_ids,
            )
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.edited")
        if not self._authoritative:
            await self._redis.incr(f"{SHADOW_COUNTER_PREFIX}msg.edited.matched_legacy")
        return True

    async def _persist_deleted(
        self,
        session: Any,
        event: MsgDeleted,
        *,
        run_side_effects: bool = True,
    ) -> bool:
        result = await session.execute(
            select(Message, Conversation)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.workspace_id == event.workspace_id,
                Conversation.telegram_chat_id == event.telegram_chat_id,
                Message.telegram_message_id.in_(event.telegram_message_ids),
            )
        )
        rows = result.all()
        if not rows:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}msg.deleted")
            await self._redis.incr(f"{SHADOW_COUNTER_PREFIX}msg.deleted.missing_legacy")
            return False

        touched_conversations: dict[int, Conversation] = {}
        expired_by_conversation: dict[int, list[int]] = {}
        deleted_messages: list[Message] = []
        for message, conversation in rows:
            if message.is_deleted and message.content == "[deleted]":
                continue
            message.content = "[deleted]"
            message.is_deleted = True
            _project_dialog_message_update(
                conversation,
                message,
                text="[deleted]",
                message_ts=message.telegram_timestamp or message.created_at,
            )
            session.add(message)
            deleted_messages.append(message)
            touched_conversations[conversation.id] = conversation
            if run_side_effects and self._authoritative:
                expired_by_conversation.setdefault(conversation.id, []).extend(
                    await _expire_trigger_replies(session, message_id=message.id)
                )
        for conversation in touched_conversations.values():
            await bump_conversation_revision(session, conversation)
        if touched_conversations:
            await session.commit()
        if run_side_effects and self._authoritative:
            for message in deleted_messages:
                await _broadcast_message_deleted(
                    workspace_id=event.workspace_id,
                    conversation_id=message.conversation_id,
                    message_id=message.id,
                    conversation_revision=touched_conversations[
                        message.conversation_id
                    ].message_revision,
                )
            for conversation_id, reply_ids in expired_by_conversation.items():
                await _broadcast_expired_replies(
                    workspace_id=event.workspace_id,
                    conversation_id=conversation_id,
                    reply_ids=reply_ids,
                )
        await self._redis.incrby(f"{PROCESSED_COUNTER_PREFIX}msg.deleted", len(rows))
        if not self._authoritative:
            await self._redis.incrby(f"{SHADOW_COUNTER_PREFIX}msg.deleted.matched_legacy", len(rows))
        return True

    async def _persist_sent(self, session: Any, event: MsgSent) -> bool:
        conversation = await _find_delivery_conversation(session, event)
        if conversation is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}msg.sent")
            return False

        runtime = await session.scalar(
            select(DeliveryRuntime).where(
                DeliveryRuntime.workspace_id == event.workspace_id,
                DeliveryRuntime.client_idempotency_key == event.idempotency_key,
            )
        )
        if runtime is not None and runtime.state in {
            DELIVERY_CONFIRMED,
            DELIVERY_RECONCILED,
        }:
            await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.sent")
            return True

        existing = await _find_placeholder_by_client_key(
            session,
            conversation_id=conversation.id,
            client_message_uuid=event.idempotency_key,
        )
        if existing is None:
            message = await create_seller_placeholder_message(
                session,
                conversation=conversation,
                content=event.text,
                client_message_uuid=event.idempotency_key,
                delivery_state="pending",
            )
        else:
            message = existing
        await record_delivery_state(
            session,
            workspace_id=event.workspace_id,
            conversation_id=conversation.id,
            message_id=message.id,
            action_record_id=event.action_record_id,
            channel=event.channel or conversation.channel or "telegram_dm",
            channel_conversation_id=event.channel_conversation_id,
            client_idempotency_key=event.idempotency_key,
            state=DELIVERY_REQUESTED,
        )
        await session.commit()
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.sent")
        return True

    async def _persist_media_sent(self, session: Any, event: MsgMediaSent) -> bool:
        conversation = await _find_delivery_conversation(session, event)
        if conversation is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}msg.media_sent")
            return False

        runtime = await session.scalar(
            select(DeliveryRuntime).where(
                DeliveryRuntime.workspace_id == event.workspace_id,
                DeliveryRuntime.client_idempotency_key == event.idempotency_key,
            )
        )
        if runtime is not None and runtime.state in {
            DELIVERY_CONFIRMED,
            DELIVERY_RECONCILED,
        }:
            await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.media_sent")
            return True

        media_metadata = _media_sent_metadata(event)
        media_type = normalize_media_type(event.media_type, media_metadata)
        existing = await _find_placeholder_by_client_key(
            session,
            conversation_id=conversation.id,
            client_message_uuid=event.idempotency_key,
        )
        if existing is None:
            message = await create_seller_placeholder_message(
                session,
                conversation=conversation,
                content=event.caption or "",
                client_message_uuid=event.idempotency_key,
                delivery_state="pending",
                media_type=media_type,
                media_metadata=media_metadata,
            )
        else:
            existing.content = event.caption or existing.content or ""
            existing.media_type = media_type
            existing.media_metadata = media_metadata
            session.add(existing)
            message = existing
        await record_delivery_state(
            session,
            workspace_id=event.workspace_id,
            conversation_id=conversation.id,
            message_id=message.id,
            action_record_id=event.action_record_id,
            channel=event.channel or conversation.channel or "telegram_dm",
            channel_conversation_id=event.channel_conversation_id,
            client_idempotency_key=event.idempotency_key,
            state=DELIVERY_REQUESTED,
        )
        await session.commit()
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.media_sent")
        return True

    async def _persist_delivery_confirmed(
        self,
        session: Any,
        event: DeliveryConfirmed,
    ) -> bool:
        conversation = await _find_delivery_conversation(session, event)
        if conversation is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}delivery.confirmed")
            return False

        client_key = event.causation_id or event.idempotency_key
        message = await _find_placeholder_by_client_key(
            session,
            conversation_id=conversation.id,
            client_message_uuid=client_key,
        )
        if message is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}delivery.confirmed")
            return False

        delivered_at = datetime.fromtimestamp(
            event.delivered_at,
            tz=UTC,
        )
        telegram_message_id = _safe_int(event.external_message_id)
        if (
            message.external_message_id == event.external_message_id
            and message.delivery_state == "confirmed"
            and (
                telegram_message_id is None
                or message.telegram_message_id == telegram_message_id
            )
            and _same_datetime(message.telegram_timestamp, delivered_at)
        ):
            await record_delivery_state(
                session,
                workspace_id=event.workspace_id,
                conversation_id=conversation.id,
                message_id=message.id,
                action_record_id=event.action_record_id,
                channel=event.channel or conversation.channel or "telegram_dm",
                channel_conversation_id=event.channel_conversation_id,
                client_idempotency_key=client_key,
                state=DELIVERY_CONFIRMED,
                external_message_id=event.external_message_id,
            )
            await session.commit()
            await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}delivery.confirmed")
            return True

        message.external_message_id = event.external_message_id
        message.delivery_state = "confirmed"
        if event.channel in {"telegram_dm", "dm"} and telegram_message_id is not None:
            message.telegram_message_id = telegram_message_id
        message.telegram_timestamp = delivered_at
        session.add(message)
        await record_delivery_state(
            session,
            workspace_id=event.workspace_id,
            conversation_id=conversation.id,
            message_id=message.id,
            action_record_id=event.action_record_id,
            channel=event.channel or conversation.channel or "telegram_dm",
            channel_conversation_id=event.channel_conversation_id,
            client_idempotency_key=client_key,
            state=DELIVERY_CONFIRMED,
            external_message_id=event.external_message_id,
        )
        await bump_conversation_revision(session, conversation)
        await session.commit()
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}delivery.confirmed")
        return True

    async def _persist_delivery_unknown(self, session: Any, event: DeliveryUnknown) -> bool:
        conversation = await _find_delivery_conversation(session, event)
        if conversation is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}delivery.unknown")
            return False
        message = await _find_placeholder_by_client_key(
            session,
            conversation_id=conversation.id,
            client_message_uuid=event.client_idempotency_key,
        )
        if message is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}delivery.unknown")
            return False
        if message.delivery_state == "confirmed":
            await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}delivery.unknown")
            return True
        if message.delivery_state != "unknown":
            message.delivery_state = "unknown"
            session.add(message)
            await bump_conversation_revision(session, conversation)
        await record_delivery_state(
            session,
            workspace_id=event.workspace_id,
            conversation_id=conversation.id,
            message_id=message.id,
            action_record_id=event.action_record_id,
            channel=event.channel or conversation.channel or "telegram_dm",
            channel_conversation_id=event.channel_conversation_id,
            client_idempotency_key=event.client_idempotency_key,
            state=DELIVERY_UNKNOWN,
            error=event.reason,
        )
        await session.commit()
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}delivery.unknown")
        return True

    async def _persist_delivery_failed(self, session: Any, event: DeliveryFailed) -> bool:
        conversation = await _find_delivery_conversation(session, event)
        if conversation is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}delivery.failed")
            return False
        message = await _find_placeholder_by_client_key(
            session,
            conversation_id=conversation.id,
            client_message_uuid=event.client_idempotency_key,
        )
        if message is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}delivery.failed")
            return False
        if message.delivery_state == "confirmed":
            await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}delivery.failed")
            return True
        if message.delivery_state != "failed":
            message.delivery_state = "failed"
            session.add(message)
            await bump_conversation_revision(session, conversation)
        await record_delivery_state(
            session,
            workspace_id=event.workspace_id,
            conversation_id=conversation.id,
            message_id=message.id,
            action_record_id=event.action_record_id,
            channel=event.channel or conversation.channel or "telegram_dm",
            channel_conversation_id=event.channel_conversation_id,
            client_idempotency_key=event.client_idempotency_key,
            state=DELIVERY_FAILED,
            error=event.error,
        )
        await session.commit()
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}delivery.failed")
        return True

    async def _persist_read_receipt(self, session: Any, event: ReadReceipt) -> bool:
        conversation = await _find_conversation_by_telegram_chat(
            session,
            workspace_id=event.workspace_id,
            telegram_chat_id=event.telegram_chat_id,
        )
        if conversation is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}read.receipt")
            return False

        query = select(Message).where(
            Message.conversation_id == conversation.id,
            Message.sender_type == SenderType.CUSTOMER.value,
            Message.is_read.is_(False),
        )
        if event.max_telegram_message_id is not None:
            query = query.where(Message.telegram_message_id <= event.max_telegram_message_id)
        messages = (await session.execute(query)).scalars().all()

        state = get_customer_conversation_state(conversation)
        sync_state = state.sync or ConversationSyncState()
        dialog = sync_state.dialog or ConversationDialogState()
        unread_count = max(int(event.unread_count or 0), 0)
        changed = bool(messages) or dialog.telegram_unread_count != unread_count
        for message in messages:
            message.is_read = True
            session.add(message)
        dialog.telegram_unread_count = unread_count
        sync_state.dialog = dialog
        state.sync = sync_state
        set_customer_conversation_state(conversation, state)
        session.add(conversation)
        if changed:
            await bump_conversation_revision(session, conversation)
        await session.commit()
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}read.receipt")
        return True

    async def _persist_follow_up_scheduled(self, session: Any, event: FollowUpScheduled) -> bool:
        conversation = await _find_conversation_by_telegram_chat(
            session,
            workspace_id=event.workspace_id,
            telegram_chat_id=event.telegram_chat_id,
        )
        if conversation is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}follow_up.scheduled")
            return False
        root_message_id = None
        root_message = None
        if event.root_telegram_message_id is not None:
            root = await _find_message(
                session,
                event.workspace_id,
                event.telegram_chat_id,
                event.root_telegram_message_id,
            )
            if root is not None:
                root_message, _conversation = root
                root_message_id = root_message.id
        due_at = datetime.fromtimestamp(event.due_at, tz=UTC)
        action_proposal_id = await _persist_follow_up_action_proposal(
            session=session,
            event=event,
            conversation=conversation,
            root_message_id=root_message_id,
            due_at=due_at,
        )
        state = get_customer_conversation_state(conversation)
        state.follow_up = ConversationFollowUpState(
            status="proposed",
            kind=event.kind,
            due_at=due_at.isoformat(),
            reason_code=event.reason_code,
            waiting_for=event.waiting_for,
            source_evidence_ref=(
                f"message:{root_message_id}" if root_message_id is not None else None
            ),
            action_proposal_id=action_proposal_id,
            action_type="schedule_sales_follow_up",
        )
        set_customer_conversation_state(conversation, state)
        session.add(conversation)
        if root_message is not None:
            await _record_lifecycle_action_success(
                session,
                workspace_id=event.workspace_id,
                conversation=conversation,
                message=root_message,
                action="follow_up_scheduled",
                source="event_spine",
            )
        await session.commit()
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}follow_up.scheduled")
        return True

    async def _persist_crm_updated(self, session: Any, event: CrmUpdated) -> bool:
        conversation = await _find_conversation_by_telegram_chat(
            session,
            workspace_id=event.workspace_id,
            telegram_chat_id=event.telegram_chat_id,
        )
        if conversation is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}crm.updated")
            return False
        state = get_customer_conversation_state(conversation)
        before = state.model_dump(mode="json")
        if event.pipeline_stage is not None:
            state.pipeline_stage = event.pipeline_stage
        if event.last_intent is not None:
            state.last_intent = event.last_intent
        if event.products_interested is not None:
            state.products_interested = list(event.products_interested)
        if event.urgency is not None:
            state.urgency = event.urgency
        if event.lead_score is not None:
            if state.model_extra is None:
                state.__pydantic_extra__ = {}
            state.model_extra["lead_score"] = event.lead_score
        state.last_updated = datetime.fromtimestamp(event.updated_at, tz=UTC).isoformat()
        set_customer_conversation_state(conversation, state)
        session.add(conversation)
        if state.model_dump(mode="json") != before:
            await bump_conversation_revision(session, conversation)
        latest_message = await _find_latest_conversation_message(session, conversation.id)
        if latest_message is not None:
            await _record_lifecycle_action_success(
                session,
                workspace_id=event.workspace_id,
                conversation=conversation,
                message=latest_message,
                action="crm_projection",
                source="event_spine",
            )
        await session.commit()
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}crm.updated")
        return True

    async def _persist_backfill_window(self, session: Any, event: BackfillWindowApplied) -> bool:
        conversation = await _find_conversation_by_telegram_chat(
            session,
            workspace_id=event.workspace_id,
            telegram_chat_id=event.telegram_chat_id,
        )
        if conversation is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}backfill.window_applied")
            return False
        state = get_customer_conversation_state(conversation)
        sync_state = state.sync or ConversationSyncState()
        watermarks = sync_state.watermarks or ConversationSyncWatermarks()
        before = watermarks.model_dump(mode="json")
        watermarks.oldest_external_message_id = event.oldest_external_message_id
        watermarks.latest_external_message_id = event.latest_external_message_id
        watermarks.oldest_complete = event.oldest_complete
        watermarks.latest_complete = event.latest_complete
        sync_state.watermarks = watermarks
        state.sync = sync_state
        set_customer_conversation_state(conversation, state)
        session.add(conversation)
        if watermarks.model_dump(mode="json") != before:
            await bump_conversation_revision(session, conversation)
        await session.commit()
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}backfill.window_applied")
        return True

    async def _persist_media_hydration_state(
        self,
        session: Any,
        event: MediaHydrationStateChanged,
    ) -> bool:
        row = await _find_message(
            session,
            event.workspace_id,
            event.telegram_chat_id,
            event.telegram_message_id,
        )
        if row is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}media.hydration_state_changed")
            return False
        message, conversation = row
        runtime = await session.scalar(
            select(MediaRuntime).where(MediaRuntime.message_id == message.id).limit(1)
        )
        if runtime is None:
            await self._redis.incr(f"{MISSING_COUNTER_PREFIX}media.hydration_state_changed")
            return False

        changed_at = datetime.fromtimestamp(event.changed_at, tz=UTC)
        before = {
            "hydration_status": runtime.hydration_status,
            "asset_state": runtime.asset_state,
            "semantic_state": runtime.semantic_state,
            "action_state": runtime.action_state,
            "ai_relevant": runtime.ai_relevant,
            "mime_type": runtime.mime_type,
            "normalized_text": runtime.normalized_text,
            "commercial_semantics": runtime.commercial_semantics,
            "last_error": runtime.last_error,
        }
        metadata = message.media_metadata if isinstance(message.media_metadata, dict) else {}
        metadata["hydration_status"] = event.hydration_status
        metadata["ai_relevant"] = event.ai_relevant
        metadata["mime_type"] = event.mime_type
        metadata["normalized_text"] = event.normalized_text
        media_evidence = event.media_evidence or event.commercial_semantics
        if isinstance(media_evidence, dict):
            metadata["media_evidence"] = media_evidence
        else:
            metadata.pop("media_evidence", None)
        metadata["media_runtime"] = {
            **(metadata.get("media_runtime") if isinstance(metadata.get("media_runtime"), dict) else {}),
            "asset_state": event.asset_state,
            "semantic_state": event.semantic_state,
        }
        message.media_metadata = metadata
        message.transcription = event.normalized_text
        runtime.hydration_status = event.hydration_status
        runtime.asset_state = event.asset_state
        runtime.semantic_state = event.semantic_state
        runtime.action_state = event.action_state
        runtime.ai_relevant = event.ai_relevant
        runtime.mime_type = event.mime_type
        runtime.normalized_text = event.normalized_text
        runtime.commercial_semantics = media_evidence
        runtime.last_error = event.last_error
        runtime.last_attempt_at = changed_at
        if event.action_state == "completed":
            runtime.completed_at = changed_at
        session.add(message)
        session.add(runtime)
        after = {
            "hydration_status": runtime.hydration_status,
            "asset_state": runtime.asset_state,
            "semantic_state": runtime.semantic_state,
            "action_state": runtime.action_state,
            "ai_relevant": runtime.ai_relevant,
            "mime_type": runtime.mime_type,
            "normalized_text": runtime.normalized_text,
            "commercial_semantics": runtime.commercial_semantics,
            "last_error": runtime.last_error,
        }
        if after != before:
            await bump_conversation_revision(session, conversation)
        await session.commit()
        await self._process_media_evidence(
            session,
            event=event,
            conversation=conversation,
            message=message,
            runtime=runtime,
        )
        await self._redis.incr(f"{PROCESSED_COUNTER_PREFIX}media.hydration_state_changed")
        return True

    async def _process_media_evidence(
        self,
        session: Any,
        *,
        event: MediaHydrationStateChanged,
        conversation: Conversation,
        message: Message,
        runtime: MediaRuntime,
    ) -> None:
        if event.semantic_state != "ready":
            return
        if not isinstance(runtime.commercial_semantics, dict):
            return
        if conversation.customer_id is None:
            return

        await self._process_media_evidence_into_business_brain(
            session,
            event=event,
            conversation=conversation,
            message=message,
            runtime=runtime,
        )

    async def _process_media_evidence_into_business_brain(
        self,
        session: Any,
        *,
        event: MediaHydrationStateChanged,
        conversation: Conversation,
        message: Message,
        runtime: MediaRuntime,
    ) -> None:
        from app.modules.business_brain.media_evidence import (
            persist_media_evidence_fact,
        )
        from app.modules.commercial_spine.repository import CommercialSpineRepository

        try:
            await persist_media_evidence_fact(
                repository=CommercialSpineRepository(session),
                workspace_id=event.workspace_id,
                conversation=conversation,
                message=message,
                runtime=runtime,
                media_evidence=runtime.commercial_semantics,
                occurred_at=datetime.fromtimestamp(event.changed_at, tz=UTC),
                correlation_id=(
                    f"media-evidence:{event.workspace_id}:"
                    f"{event.telegram_chat_id}:{event.telegram_message_id}"
                ),
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "event_spine.media_evidence_business_brain_processing_failed",
                extra={
                    "workspace_id": event.workspace_id,
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                    "media_ref": runtime.media_ref,
                },
            )

def _inbound_to_persist_input(event: MsgInbound) -> PersistMessageInput:
    channel = event.channel or "telegram_dm"
    is_telegram = channel in {"telegram_dm", "dm"}
    return PersistMessageInput(
        workspace_id=event.workspace_id,
        sender_id=event.sender_telegram_id if is_telegram else None,
        sender_external_id=(
            None
            if is_telegram
            else (event.channel_sender_id or str(event.sender_telegram_id))
        ),
        sender_name=event.sender_name or "",
        sender_username=event.sender_username,
        text=event.text or "",
        is_outgoing=event.is_outgoing,
        channel=channel,
        telegram_chat_id=event.telegram_chat_id if is_telegram else None,
        external_chat_id=(
            None
            if is_telegram
            else (event.channel_conversation_id or str(event.telegram_chat_id))
        ),
        media_type=normalize_media_type(event.media_type, event.media_metadata),
        telegram_message_id=event.telegram_message_id if is_telegram else None,
        external_message_id=event.channel_message_id or str(event.telegram_message_id),
        reply_to_msg_id=event.reply_to_msg_id,
        forward_from_name=event.forward_from_name,
        forward_date=(
            datetime.fromtimestamp(event.forward_date, tz=UTC)
            if event.forward_date
            else None
        ),
        media_metadata=event.media_metadata,
        text_entities=event.text_entities,
        message_ts=datetime.fromtimestamp(event.sent_at, tz=UTC),
        grouped_id=event.grouped_id,
        is_read=True if event.is_historical else None,
    )


_TELEGRAM_GATEWAY_TELEMETRY_FIELDS = (
    "telegram_update_received_at",
    "telegram_state_applied_at",
    "hot_event_built_at",
    "outbox_enqueued_at",
    "backend_webhook_received_at",
)


def _trigger_telemetry_from_event(
    event: MsgInbound,
    *,
    event_spine_committed_at: float,
    trigger_matched_at: float,
) -> dict[str, float]:
    telemetry: dict[str, float] = {}
    for field in _TELEGRAM_GATEWAY_TELEMETRY_FIELDS:
        value = getattr(event, field, None)
        if isinstance(value, (int, float)):
            telemetry[field] = float(value)
    telemetry["event_spine_committed_at"] = float(event_spine_committed_at)
    telemetry["trigger_matched_at"] = float(trigger_matched_at)
    return telemetry


async def _pulse_hot_inbound_presence(
    *,
    session: Any,
    redis: Any,
    event: MsgInbound,
    conversation: Conversation,
    message: Message,
    customer: Customer,
) -> None:
    if getattr(customer, "opted_out", False):
        # DNC / Bog'lanmaslik: true silence. Do NOT pulse online presence or mark
        # the message read — these fire on receipt, before the reply dispatch gate,
        # so a do-not-contact lead would otherwise see the account come online and
        # 'seen' their message even though the agent never replies.
        return
    if (event.channel or "telegram_dm") != "telegram_dm":
        return
    chat_id = conversation.external_chat_id or (
        str(conversation.telegram_chat_id)
        if conversation.telegram_chat_id is not None
        else str(event.telegram_chat_id or "")
    )
    if not chat_id:
        await redis.incr(f"{MISSING_COUNTER_PREFIX}hot_presence.chat_id")
        return

    max_message_id = int(event.telegram_message_id or message.telegram_message_id or 0)
    settings = get_settings()
    result = await TalkPresenceService(
        sidecar_url=settings.sidecar_url,
        sidecar_api_key=settings.sidecar_api_key,
        timeout_seconds=HOT_INBOUND_PRESENCE_TIMEOUT_SECONDS,
    ).pulse(
        workspace_id=conversation.workspace_id,
        chat_id=chat_id,
        max_message_id=max_message_id,
        online=settings.telegram_presence_online_enabled,
        read=settings.telegram_presence_read_enabled,
        typing=False,
    )
    await redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.inbound.hot_presence")
    if result.online:
        await redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.inbound.hot_presence.online")
    if result.read:
        await redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.inbound.hot_presence.read")
        await _mark_local_inbox_read_without_commit(
            session,
            conversation=conversation,
            message=message,
            max_message_id=max_message_id,
        )
        await session.commit()
    if result.warnings:
        logger.warning(
            "event_spine.hot_inbound_presence_degraded",
            extra={
                "workspace_id": conversation.workspace_id,
                "conversation_id": conversation.id,
                "message_id": message.id,
                "warnings": list(result.warnings),
            },
        )


async def _mark_local_inbox_read_without_commit(
    session: Any,
    *,
    conversation: Conversation,
    message: Message,
    max_message_id: int,
) -> None:
    if max_message_id <= 0:
        return
    await session.execute(
        update(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.telegram_message_id <= max_message_id,
            Message.sender_type == SenderType.CUSTOMER.value,
            Message.is_read.is_(False),
        )
        .values(is_read=True)
    )
    if (
        message.telegram_message_id is not None
        and int(message.telegram_message_id) <= max_message_id
        and message.sender_type == SenderType.CUSTOMER.value
    ):
        message.is_read = True

    state = get_customer_conversation_state(conversation)
    if state.sync is not None and state.sync.dialog is not None:
        state.sync.dialog.telegram_unread_count = 0
        set_customer_conversation_state(conversation, state)


REPLY_STATE_TAIL_WINDOW = 50


async def _process_inbound_message_actions(
    *,
    session: Any,
    redis: Any,
    event: MsgInbound,
    event_spine_committed_at: float,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    message: Message,
    is_duplicate: bool,
    conversation_turn_runner: Any,
) -> None:
    if is_duplicate:
        if event.source == "live_recovery" and not event.is_outgoing:
            await _process_live_recovery_duplicate_window(
                session=session,
                redis=redis,
                event=event,
                workspace=workspace,
                conversation=conversation,
                customer=customer,
                conversation_turn_runner=conversation_turn_runner,
            )
        return

    expired_reply_ids: list[int] = []
    if event.is_outgoing:
        expired_reply_ids = await _process_seller_message_actions(
            session=session,
            workspace=workspace,
            conversation=conversation,
            message=message,
            conversation_turn_runner=conversation_turn_runner,
        )
    else:
        await _process_customer_message_actions(
            session=session,
            event=event,
            event_spine_committed_at=event_spine_committed_at,
            workspace=workspace,
            conversation=conversation,
            customer=customer,
            message=message,
            conversation_turn_runner=conversation_turn_runner,
        )

    await _refresh_reply_state(session=session, conversation=conversation)
    await session.commit()
    await _broadcast_expired_replies(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        reply_ids=expired_reply_ids,
    )
    await _broadcast_new_message(
        session=session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        message=message,
    )
    await redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.inbound.actions")


async def _process_seller_message_actions(
    *,
    session: Any,
    workspace: Workspace,
    conversation: Conversation,
    message: Message,
    conversation_turn_runner: Any,
) -> list[int]:
    await ConversationTurnSessionService(session).complete_active_turns_for_agent_message(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
    )
    await conversation_turn_runner.record_agent_message(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        message_id=message.id,
    )
    return []


async def _process_customer_message_actions(
    *,
    session: Any,
    event: MsgInbound,
    event_spine_committed_at: float,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    message: Message,
    conversation_turn_runner: Any,
) -> None:
    # Inbound-message consumers (CRM lead capture, promoter reply-retire) run on
    # every customer message BEFORE the reply-lifecycle gate, so capture never
    # depends on the agent replying. Each is independently non-fatal (a registry
    # guarantee). New inbound reactions register in turn_consumers.py.
    from app.modules.agent_runtime_v2.turn_consumers import (
        InboundContext,
        on_inbound_message,
    )

    await on_inbound_message(
        InboundContext(
            db=session,
            workspace=workspace,
            conversation=conversation,
            customer=customer,
        )
    )

    classification = classify_local(
        message.content or event.text or "",
        media_type=message.media_type,
    )
    if not classification.should_enter_reply_lifecycle:
        return

    trigger_telemetry = _trigger_telemetry_from_event(
        event,
        event_spine_committed_at=event_spine_committed_at,
        trigger_matched_at=datetime.now(UTC).timestamp(),
    )
    hot_path = await AgentSessionHotPathService(session).record_customer_message_and_prepare_run(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=getattr(customer, "id", None),
        channel=getattr(conversation, "channel", None) or message.channel or event.channel or "telegram_dm",
        message_id=message.id,
        text=message.content or event.text or "",
        trigger_telemetry=trigger_telemetry,
        payload={
            "telegram_message_id": message.telegram_message_id,
            "channel_message_id": event.channel_message_id,
            "media_type": message.media_type,
            "source": "event_spine",
        },
    )
    turn = await ConversationTurnSessionService(session).append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=message,
        agent_id=hot_path.agent_id if hot_path is not None else None,
    )
    if int(turn.latest_customer_message_id) != int(message.id):
        return
    await session.flush()
    await conversation_turn_runner.enqueue_message(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        message_id=message.id,
        trigger_telemetry=trigger_telemetry,
    )


async def _process_live_recovery_duplicate_window(
    *,
    session: Any,
    redis: Any,
    event: MsgInbound,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    conversation_turn_runner: Any,
) -> bool:
    if event.is_historical or event.is_outgoing:
        return False
    recent_messages = (
        await session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.is_deleted.is_(False),
            )
            .order_by(
                Message.telegram_timestamp.desc().nullslast(),
                Message.telegram_message_id.desc().nullslast(),
                Message.created_at.desc(),
                Message.id.desc(),
            )
            .limit(REPLY_STATE_TAIL_WINDOW)
        )
    ).scalars().all()
    if not recent_messages:
        return False

    results = await recover_catch_up_window(
        session=session,
        workspace=workspace,
        conversation=conversation,
        messages=sorted(recent_messages, key=_message_recovery_order_key),
        customer=customer,
        conversation_turn_runner=conversation_turn_runner,
    )
    triggered = any(result.reply_generation_triggered for result in results)
    if results:
        await session.commit()
    if triggered:
        await redis.incr(f"{PROCESSED_COUNTER_PREFIX}msg.inbound.actions")
    return triggered


def _message_recovery_order_key(message: Message) -> tuple[datetime, int, int]:
    return (
        message.telegram_timestamp or message.created_at,
        int(message.telegram_message_id or 0),
        int(message.id or 0),
    )


async def _refresh_reply_state(
    *,
    session: Any,
    conversation: Conversation,
) -> None:
    recent = (
        await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(
                Message.telegram_timestamp.desc().nullslast(),
                Message.telegram_message_id.desc().nullslast(),
                Message.created_at.desc(),
                Message.id.desc(),
            )
            .limit(REPLY_STATE_TAIL_WINDOW)
        )
    ).scalars().all()
    await refresh_customer_conversation_state(
        conversation,
        messages=list(reversed(recent)),
    )
    session.add(conversation)


async def _find_message(
    session: Any,
    workspace_id: int,
    telegram_chat_id: int,
    telegram_message_id: int,
) -> tuple[Message, Conversation] | None:
    result = await session.execute(
        select(Message, Conversation)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.telegram_chat_id == telegram_chat_id,
            Message.telegram_message_id == telegram_message_id,
        )
        .order_by(Message.id.desc())
        .limit(1)
    )
    row = result.one_or_none()
    if row is None:
        return None
    message, conversation = row
    return message, conversation


async def _find_latest_conversation_message(session: Any, conversation_id: int) -> Message | None:
    return await session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(
            Message.telegram_timestamp.desc().nullslast(),
            Message.conversation_seq.desc().nullslast(),
            Message.created_at.desc(),
            Message.id.desc(),
        )
        .limit(1)
    )


async def _record_lifecycle_action_success(
    session: Any,
    *,
    workspace_id: int,
    conversation: Conversation,
    message: Message,
    action: str,
    source: str,
) -> None:
    try:
        async with session.begin_nested():
            await record_action_state(
                session,
                workspace_id=workspace_id,
                conversation_id=conversation.id,
                message_id=message.id,
                action=action,
                state=ACTION_SUCCESS,
                source=source,
            )
    except Exception:
        logger.warning(
            "event_spine.lifecycle_action_runtime_failed",
            extra={
                "workspace_id": workspace_id,
                "conversation_id": conversation.id,
                "message_id": message.id,
                "action": action,
                "source": source,
            },
            exc_info=True,
        )


async def _persist_follow_up_action_proposal(
    *,
    session: Any,
    event: FollowUpScheduled,
    conversation: Conversation,
    root_message_id: int | None,
    due_at: datetime,
) -> str:
    from app.modules.commercial_spine.contracts import CommercialActionProposal
    from app.modules.commercial_spine.repository import CommercialSpineRepository

    source_refs = [f"event_spine:{event.event_id}"]
    if root_message_id is not None:
        source_refs.append(f"message:{root_message_id}")

    payload = {
        "follow_up_kind": event.kind,
        "waiting_for": event.waiting_for,
        "title": event.title,
        "suggested_message": event.suggested_message,
        "due_at": due_at.isoformat(),
        "root_message_id": root_message_id,
        "root_telegram_message_id": event.root_telegram_message_id,
        "source": "event_spine",
    }
    proposal = CommercialActionProposal(
        proposal_id=f"event-spine-follow-up:{event.event_id}",
        workspace_id=event.workspace_id,
        conversation_id=conversation.id,
        customer_id=int(conversation.customer_id),
        action_type="schedule_sales_follow_up",
        lifecycle_state="proposed",
        execution_mode="auto_execute_if_policy_allows",
        risk_level="low",
        requires_approval=False,
        executor_runtime="sales_follow_up_runtime",
        priority=_proposal_priority(event.priority),
        confidence=0.86,
        reason_code=event.reason_code,
        source_refs=source_refs,
        payload=payload,
        idempotency_key=f"event_spine:follow_up:{event.event_id}",
        correlation_id=event.correlation_id or f"event_spine:follow_up:{event.event_id}",
        trace_id=f"event_spine:follow_up:{event.event_id}",
    )
    await CommercialSpineRepository(session).persist_action_proposal(proposal)
    return proposal.proposal_id


def _proposal_priority(priority: str | None) -> str:
    if priority in {"low", "medium", "high", "urgent"}:
        return priority
    return "medium"


async def _find_delivery_conversation(
    session: Any,
    event: MsgSent | MsgMediaSent | DeliveryConfirmed | DeliveryUnknown | DeliveryFailed,
) -> Conversation | None:
    if event.conversation_id:
        conversation = await session.get(Conversation, event.conversation_id)
        if conversation is not None and conversation.workspace_id == event.workspace_id:
            return conversation

    channel = event.channel or "telegram_dm"
    channel_conversation_id = event.channel_conversation_id
    if not channel_conversation_id:
        return None

    filters = [
        Conversation.workspace_id == event.workspace_id,
        Conversation.channel == channel,
    ]
    if channel in {"telegram_dm", "dm"}:
        telegram_chat_id = _safe_int(channel_conversation_id)
        if telegram_chat_id is None:
            return None
        filters.append(Conversation.telegram_chat_id == telegram_chat_id)
    else:
        filters.append(Conversation.external_chat_id == channel_conversation_id)

    return await session.scalar(select(Conversation).where(*filters).limit(1))


def _media_sent_metadata(event: MsgMediaSent) -> dict:
    metadata = {
        "url": event.media_url,
        "outbound": True,
    }
    if event.media_asset_id:
        metadata["assetId"] = event.media_asset_id
    return metadata


async def _find_conversation_by_telegram_chat(
    session: Any,
    *,
    workspace_id: int,
    telegram_chat_id: int,
) -> Conversation | None:
    return await session.scalar(
        select(Conversation)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.telegram_chat_id == telegram_chat_id,
        )
        .limit(1)
    )


async def _expire_trigger_replies(session: Any, *, message_id: int) -> list[int]:
    _ = session, message_id
    return []


async def _expire_conversation_drafts(
    session: Any,
    *,
    conversation_id: int,
) -> list[int]:
    _ = session, conversation_id
    return []


async def _broadcast_expired_replies(
    *,
    workspace_id: int,
    conversation_id: int,
    reply_ids: list[int],
) -> None:
    if not reply_ids:
        return
    from app.api.routes.ws import manager as ws_manager

    for reply_id in reply_ids:
        await ws_manager.broadcast(
            workspace_id,
            {
                "type": "agent_action_updated",
                "data": {
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "reply_id": reply_id,
                    "status": "expired",
                    "action": "expired",
                },
            },
        )


async def _broadcast_new_message(
    *,
    session: Any,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    message: Message,
) -> None:
    from app.api.routes.ws import manager as ws_manager
    from app.services.media_urls import (
        build_message_media_preview_url,
        canonicalize_message_media_url,
    )

    unread_count_result = await session.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id == conversation.id,
            Message.is_read.is_(False),
            Message.sender_type == SenderType.CUSTOMER.value,
        )
    )
    unread_count = int(unread_count_result.scalar() or 0)
    media_full_url = canonicalize_message_media_url(
        media_url=message.media_url,
        telegram_chat_id=conversation.telegram_chat_id,
        telegram_message_id=message.telegram_message_id,
        media_type=message.media_type,
    )
    media_preview_url = build_message_media_preview_url(
        telegram_chat_id=conversation.telegram_chat_id,
        telegram_message_id=message.telegram_message_id,
        media_type=message.media_type,
    )
    delivery_runtime = await session.scalar(
        select(DeliveryRuntime).where(
            DeliveryRuntime.workspace_id == workspace.id,
            DeliveryRuntime.message_id == message.id,
        )
    )
    delivery_runtime_payload = build_delivery_runtime_response(delivery_runtime)

    await ws_manager.broadcast(
        workspace.id,
        {
            "type": "new_message",
            "data": {
                "conversation_id": conversation.id,
                "conversation": {
                    "id": conversation.id,
                    "customer_id": conversation.customer_id,
                    "customer_name": customer.display_name,
                    "channel": conversation.channel,
                    "telegram_chat_id": conversation.telegram_chat_id,
                    "external_chat_id": conversation.external_chat_id,
                    "external_thread_id": conversation.external_thread_id,
                    "pipeline_stage": resolved_pipeline_stage(conversation),
                    "override_mode": conversation.override_mode,
                    "summary": conversation.summary,
                    "needs_attention": conversation.needs_attention,
                    "read_outbox_max_id": conversation.read_outbox_max_id,
                    "last_message_at": (
                        conversation.last_message_at.isoformat()
                        if conversation.last_message_at
                        else None
                    ),
                    "unread_count": unread_count,
                    "created_at": conversation.created_at.isoformat(),
                    "last_message_text": (message.content or "")[:100],
                    "contact_type": customer.contact_type,
                    "has_pending_reply": False,
                    "latest_reply_confidence": None,
                },
                "message": {
                    "id": message.id,
                    "conversation_id": conversation.id,
                    "channel": conversation.channel,
                    "sender_type": message.sender_type,
                    "content": message.content,
                    "media_type": message.media_type,
                    "media_url": media_full_url,
                    "media_full_url": media_full_url,
                    "media_preview_url": media_preview_url,
                    "telegram_message_id": message.telegram_message_id,
                    "is_read": message.is_read,
                    "created_at": message.created_at.isoformat(),
                    "reply_to_msg_id": message.reply_to_msg_id,
                    "forward_from_name": message.forward_from_name,
                    "forward_date": (
                        message.forward_date.isoformat()
                        if message.forward_date
                        else None
                    ),
                    "edited_at": (
                        message.edited_at.isoformat()
                        if message.edited_at
                        else None
                    ),
                    "is_deleted": message.is_deleted,
                    "media_metadata": message.media_metadata,
                    "text_entities": message.text_entities,
                    "reactions": message.reactions,
                    "external_message_id": message.external_message_id,
                    "external_author_id": message.external_author_id,
                    "external_parent_id": message.external_parent_id,
                    "client_message_uuid": message.client_message_uuid,
                    "delivery_state": message.delivery_state,
                    "delivery_runtime": (
                        delivery_runtime_payload.model_dump(mode="json")
                        if delivery_runtime_payload else None
                    ),
                    "conversation_seq": message.conversation_seq,
                    "conversation_revision": conversation.message_revision,
                    "grouped_id": message.grouped_id,
                    "telegram_timestamp": (
                        message.telegram_timestamp.isoformat()
                        if message.telegram_timestamp
                        else None
                    ),
                    "telegram_chat_id": conversation.telegram_chat_id,
                },
            },
        },
    )


async def _broadcast_message_edited(
    *,
    workspace_id: int,
    conversation: Conversation,
    message: Message,
) -> None:
    from app.api.routes.ws import manager as ws_manager

    await ws_manager.broadcast(
        workspace_id,
        {
            "type": "message_edited",
            "data": {
                "message_id": message.id,
                "conversation_id": conversation.id,
                "content": message.content,
                "text_entities": message.text_entities,
                "edited_at": message.edited_at.isoformat() if message.edited_at else None,
                "conversation_revision": conversation.message_revision,
            },
        },
    )


async def _broadcast_message_deleted(
    *,
    workspace_id: int,
    conversation_id: int,
    message_id: int,
    conversation_revision: int,
) -> None:
    from app.api.routes.ws import manager as ws_manager

    await ws_manager.broadcast(
        workspace_id,
        {
            "type": "message_deleted",
            "data": {
                "message_id": message_id,
                "conversation_id": conversation_id,
                "conversation_revision": conversation_revision,
            },
        },
    )


async def _find_placeholder_by_client_key(
    session: Any,
    *,
    conversation_id: int,
    client_message_uuid: str | None,
) -> Message | None:
    if not client_message_uuid:
        return None
    return await session.scalar(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.client_message_uuid == client_message_uuid,
            Message.sender_type == SenderType.SELLER.value,
        )
        .order_by(Message.id.desc())
        .limit(1)
    )


def _safe_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _project_dialog_message_update(
    conversation: Conversation,
    message: Message,
    *,
    text: str,
    message_ts: datetime | None,
) -> None:
    state = get_customer_conversation_state(conversation)
    sync_state = state.sync or ConversationSyncState()
    dialog = sync_state.dialog or ConversationDialogState()
    current_top = _safe_int(dialog.top_message_id)
    message_top = message.telegram_message_id
    if current_top is not None and message_top is not None and current_top != message_top:
        return

    current_at = _safe_datetime(dialog.last_message_date)
    effective_ts = _ensure_aware_utc(message_ts or datetime.now(UTC))
    if current_top is None and current_at is not None and current_at > effective_ts:
        return

    dialog.top_message_id = message_top or dialog.top_message_id
    dialog.last_message_text = (text or "")[:200]
    dialog.last_message_is_outgoing = message.sender_type == SenderType.SELLER.value
    dialog.last_message_date = effective_ts.isoformat()
    sync_state.dialog = dialog
    state.sync = sync_state
    set_customer_conversation_state(conversation, state)


def _safe_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _ensure_aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _same_datetime(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return _ensure_aware_utc(left) == _ensure_aware_utc(right)
