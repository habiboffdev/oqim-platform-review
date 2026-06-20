from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.action_runtime import ActionRuntime
from app.models.conversation import Conversation
from app.models.message import Message, SenderType
from app.modules.commercial_spine.contracts import BusinessBrainProjection
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.services.action_runtime import (
    ACTION_DEGRADED,
    ACTION_RUNNING,
    ACTION_SUCCESS,
    record_action_state,
)
from app.services.worker_lease import WorkerLease

logger = get_logger("services.chat_memory_extraction_worker")

DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_BATCH_SIZE = 25
WORKER_LEASE_ROLE = "chat_memory_extraction"
CHAT_MEMORY_EXTRACT_ACTION = "chat_memory_extract"
SUMMARY_TAIL_LIMIT = 12
SUMMARY_DISPLAY_TURNS = 4

WorkspaceIdsProvider = Callable[[], Awaitable[list[int]] | list[int]]


@dataclass(frozen=True, slots=True)
class ChatMemoryExtractionClaim:
    workspace_id: int
    conversation_id: int
    message_id: int


class ChatMemoryExtractionWorker:
    """Supervised worker for conversation-level Chat Memory state extraction."""

    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        workspace_ids_provider: WorkspaceIdsProvider,
        redis: Any | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._db_factory = db_factory
        self._workspace_ids_provider = workspace_ids_provider
        self._redis = redis
        self._poll_interval_seconds = max(float(poll_interval_seconds), 0.1)
        self._batch_size = max(1, int(batch_size))
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None
        self._lease = (
            WorkerLease(redis, role=WORKER_LEASE_ROLE, ttl_seconds=30)
            if redis is not None
            else None
        )

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._heartbeat_callback = callback

    async def start(self) -> None:
        self._stopping = False
        has_lease = False
        while not self._stopping:
            try:
                if self._lease is not None:
                    has_lease = (
                        await self._lease.renew()
                        if has_lease
                        else await self._lease.acquire()
                    )
                    if not has_lease:
                        self._beat()
                        await asyncio.sleep(self._poll_interval_seconds)
                        continue
                processed = await self.run_once()
                self._beat()
                if processed == 0:
                    await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                raise
            except Exception:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                has_lease = False
                logger.exception("chat_memory_extraction_worker.tick_failed")
                await asyncio.sleep(min(self._poll_interval_seconds * 2, 10.0))
        if has_lease and self._lease is not None:
            await self._lease.release()

    async def stop(self) -> None:
        self._stopping = True

    async def run_once(self) -> int:
        workspace_ids = await self._load_workspace_ids()
        if not workspace_ids:
            return 0
        processed = 0
        for workspace_id in workspace_ids:
            self._beat()
            async with self._db_factory() as session:
                claims = await claim_due_chat_memory_extractions(
                    session,
                    workspace_ids=[workspace_id],
                    limit=self._batch_size,
                )
            for claim in claims:
                self._beat()
                await self._process_claim(claim)
                processed += 1
        return processed

    async def _process_claim(self, claim: ChatMemoryExtractionClaim) -> None:
        async with self._db_factory() as session:
            try:
                await record_action_state(
                    session,
                    workspace_id=claim.workspace_id,
                    conversation_id=claim.conversation_id,
                    message_id=claim.message_id,
                    action=CHAT_MEMORY_EXTRACT_ACTION,
                    state=ACTION_RUNNING,
                    source=WORKER_LEASE_ROLE,
                )
                await session.flush()
                await project_chat_memory_conversation_tail(
                    session,
                    workspace_id=claim.workspace_id,
                    conversation_id=claim.conversation_id,
                    message_id=claim.message_id,
                    source=WORKER_LEASE_ROLE,
                )
                await record_action_state(
                    session,
                    workspace_id=claim.workspace_id,
                    conversation_id=claim.conversation_id,
                    message_id=claim.message_id,
                    action=CHAT_MEMORY_EXTRACT_ACTION,
                    state=ACTION_SUCCESS,
                    source=WORKER_LEASE_ROLE,
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                await record_action_state(
                    session,
                    workspace_id=claim.workspace_id,
                    conversation_id=claim.conversation_id,
                    message_id=claim.message_id,
                    action=CHAT_MEMORY_EXTRACT_ACTION,
                    state=ACTION_DEGRADED,
                    source=WORKER_LEASE_ROLE,
                    error=str(exc),
                )
                await session.commit()
                logger.warning(
                    "chat_memory_extraction_worker.degraded",
                    extra={
                        "workspace_id": claim.workspace_id,
                        "conversation_id": claim.conversation_id,
                        "message_id": claim.message_id,
                        "error": str(exc),
                    },
                    exc_info=exc,
                )

    async def _load_workspace_ids(self) -> list[int]:
        value = self._workspace_ids_provider()
        if inspect.isawaitable(value):
            value = await value
        return [int(workspace_id) for workspace_id in value]

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()


async def claim_due_chat_memory_extractions(
    session: AsyncSession,
    *,
    workspace_ids: list[int],
    limit: int = DEFAULT_BATCH_SIZE,
) -> list[ChatMemoryExtractionClaim]:
    if limit <= 0 or not workspace_ids:
        return []
    latest_messages = (
        select(
            Message.conversation_id.label("conversation_id"),
            func.max(Message.id).label("message_id"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )
    done_action = exists().where(
        ActionRuntime.workspace_id == Conversation.workspace_id,
        ActionRuntime.conversation_id == Conversation.id,
        ActionRuntime.message_id == latest_messages.c.message_id,
        ActionRuntime.action == CHAT_MEMORY_EXTRACT_ACTION,
        ActionRuntime.state.in_([ACTION_SUCCESS, ACTION_DEGRADED]),
    )
    rows = await session.execute(
        select(
            Conversation.workspace_id,
            Conversation.id,
            latest_messages.c.message_id,
        )
        .join(latest_messages, latest_messages.c.conversation_id == Conversation.id)
        .where(
            Conversation.workspace_id.in_([int(workspace_id) for workspace_id in workspace_ids]),
            latest_messages.c.message_id.is_not(None),
            ~done_action,
        )
        .order_by(latest_messages.c.message_id.asc())
        .limit(max(1, int(limit)))
    )
    return [
        ChatMemoryExtractionClaim(
            workspace_id=int(row.workspace_id),
            conversation_id=int(row.id),
            message_id=int(row.message_id),
        )
        for row in rows
    ]


async def project_chat_memory_conversation_tail(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation_id: int,
    message_id: int,
    source: str,
) -> None:
    conversation = await session.get(Conversation, conversation_id)
    message = await session.get(Message, message_id)
    if (
        conversation is None
        or message is None
        or conversation.workspace_id != workspace_id
        or message.conversation_id != conversation_id
    ):
        return

    messages = await _recent_messages(session, conversation_id=conversation_id)
    summary = _conversation_summary(messages)
    if summary:
        conversation.summary = summary
        conversation.summary_updated_at = datetime.now(UTC)
    projection = BusinessBrainProjection(
        projection_ref=f"chat_memory:conversation:{conversation_id}",
        workspace_id=workspace_id,
        projection_type="chat_memory_conversation",
        entity_ref=f"conversation:{conversation_id}",
        state={
            "conversation_id": conversation_id,
            "customer_id": conversation.customer_id,
            "summary": summary,
            "tags": _conversation_tags(messages),
            "message_count": len(messages),
            "latest_message_ref": f"message:{message_id}",
            "latest_sender_type": message.sender_type,
            "updated_by": source,
        },
        source_refs=_message_refs(messages) or [f"message:{message_id}"],
    )
    await CommercialSpineRepository(session).upsert_projection(projection)
    await session.flush()


async def _recent_messages(
    session: AsyncSession,
    *,
    conversation_id: int,
) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(SUMMARY_TAIL_LIMIT)
    )
    return list(reversed(rows.all()))


def _conversation_summary(messages: list[Message]) -> str:
    snippets: list[str] = []
    for message in messages[-SUMMARY_DISPLAY_TURNS:]:
        text = _message_text(message)
        if not text:
            continue
        snippets.append(f"{_sender_label(message.sender_type)}: {text}")
    return " / ".join(snippets)[:900]


def _message_text(message: Message) -> str:
    text = (message.content or "").strip()
    if text:
        return " ".join(text.split())
    if message.transcription:
        return " ".join(message.transcription.split())
    if message.media_description:
        return " ".join(message.media_description.split())
    if message.media_type:
        return f"[{message.media_type}]"
    return ""


def _conversation_tags(messages: list[Message]) -> list[str]:
    tags: list[str] = []
    if messages:
        latest = messages[-1]
        if latest.sender_type == SenderType.CUSTOMER.value:
            tags.append("customer_waiting")
        elif latest.sender_type in {SenderType.SELLER.value, SenderType.AI.value}:
            tags.append("seller_replied")
    if any(message.media_type for message in messages):
        tags.append("has_media")
    if any(message.transcription for message in messages):
        tags.append("has_transcription")
    return _unique(tags)


def _message_refs(messages: list[Message]) -> list[str]:
    return [f"message:{message.id}" for message in messages if message.id is not None]


def _sender_label(sender_type: str) -> str:
    if sender_type == SenderType.CUSTOMER.value:
        return "Mijoz"
    if sender_type == SenderType.SELLER.value:
        return "Sotuvchi"
    if sender_type == SenderType.AI.value:
        return "Agent"
    return "Xabar"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
