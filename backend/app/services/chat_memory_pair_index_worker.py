from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.action_runtime import ActionRuntime
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message, SenderType
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import (
    ConversationPairMiningInput,
    SourceUnitRebuildRequest,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.services.action_runtime import (
    ACTION_DEGRADED,
    ACTION_RUNNING,
    ACTION_SUCCESS,
    record_action_state,
)
from app.services.worker_lease import WorkerLease

logger = get_logger("services.chat_memory_pair_index_worker")

DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_BATCH_SIZE = 50
WORKER_LEASE_ROLE = "chat_memory_pair_index"
PAIR_INDEX_ACTION = "pair_index"

WorkspaceIdsProvider = Callable[[], Awaitable[list[int]] | list[int]]


@dataclass(frozen=True, slots=True)
class ChatMemoryPairIndexClaim:
    workspace_id: int
    conversation_id: int
    message_id: int


@dataclass(frozen=True, slots=True)
class ChatMemoryPairIndexResult:
    indexed_pair_count: int = 0
    indexed_source_unit_count: int = 0


class ChatMemoryPairIndexWorker:
    """Supervised worker for searchable Chat Memory conversation-pair indexing."""

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
                logger.exception("chat_memory_pair_index_worker.tick_failed")
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
                claims = await claim_due_pair_index_messages(
                    session,
                    workspace_ids=[workspace_id],
                    limit=self._batch_size,
                )
            for claim in claims:
                self._beat()
                await self._process_claim(claim)
                processed += 1
        return processed

    async def _process_claim(self, claim: ChatMemoryPairIndexClaim) -> None:
        async with self._db_factory() as session:
            try:
                await record_action_state(
                    session,
                    workspace_id=claim.workspace_id,
                    conversation_id=claim.conversation_id,
                    message_id=claim.message_id,
                    action=PAIR_INDEX_ACTION,
                    state=ACTION_RUNNING,
                    source=WORKER_LEASE_ROLE,
                )
                await session.flush()
                await index_conversation_pairs_for_chat_memory(
                    session,
                    workspace_id=claim.workspace_id,
                    conversation_id=claim.conversation_id,
                    trigger_message_id=claim.message_id,
                )
                await record_action_state(
                    session,
                    workspace_id=claim.workspace_id,
                    conversation_id=claim.conversation_id,
                    message_id=claim.message_id,
                    action=PAIR_INDEX_ACTION,
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
                    action=PAIR_INDEX_ACTION,
                    state=ACTION_DEGRADED,
                    source=WORKER_LEASE_ROLE,
                    error=str(exc),
                )
                await session.commit()
                logger.warning(
                    "chat_memory_pair_index_worker.degraded",
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


async def claim_due_pair_index_messages(
    session: AsyncSession,
    *,
    workspace_ids: list[int],
    limit: int = DEFAULT_BATCH_SIZE,
) -> list[ChatMemoryPairIndexClaim]:
    if limit <= 0 or not workspace_ids:
        return []
    done_action = exists().where(
        ActionRuntime.workspace_id == Conversation.workspace_id,
        ActionRuntime.conversation_id == Message.conversation_id,
        ActionRuntime.message_id == Message.id,
        ActionRuntime.action == PAIR_INDEX_ACTION,
        ActionRuntime.state.in_([ACTION_SUCCESS, ACTION_DEGRADED]),
    )
    rows = await session.execute(
        select(
            Conversation.workspace_id,
            Message.conversation_id,
            Message.id,
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(Customer, Customer.id == Conversation.customer_id)
        .where(
            Conversation.workspace_id.in_([int(workspace_id) for workspace_id in workspace_ids]),
            Message.sender_type == SenderType.SELLER.value,
            Customer.channel != "eval_replay",
            or_(
                Conversation.external_chat_id.is_(None),
                ~Conversation.external_chat_id.like("eval:%"),
            ),
            or_(
                Message.external_message_id.is_(None),
                ~Message.external_message_id.like("shadow:%"),
            ),
            ~done_action,
        )
        .order_by(
            Message.telegram_timestamp.asc().nullslast(),
            Message.created_at.asc(),
            Message.id.asc(),
        )
        .limit(max(1, int(limit)))
    )
    return [
        ChatMemoryPairIndexClaim(
            workspace_id=int(row.workspace_id),
            conversation_id=int(row.conversation_id),
            message_id=int(row.id),
        )
        for row in rows
    ]


async def index_conversation_pairs_for_chat_memory(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation_id: int,
    trigger_message_id: int | None = None,
) -> ChatMemoryPairIndexResult:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.workspace_id != workspace_id:
        return ChatMemoryPairIndexResult()
    if await _conversation_excluded_from_pair_index(session, conversation):
        return ChatMemoryPairIndexResult()

    rows = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(
            Message.telegram_timestamp.asc().nullslast(),
            Message.created_at.asc(),
            Message.id.asc(),
        )
    )
    turns = [
        {
            "message_ref": f"message:{message.id}",
            "sender_type": message.sender_type,
            "content": message.content or "",
            "created_at": (
                message.telegram_timestamp or message.created_at
            ).isoformat()
            if (message.telegram_timestamp or message.created_at)
            else None,
            "media_semantics": (
                {
                    "media_type": message.media_type,
                    "media_url": getattr(message, "media_url", None),
                }
                if message.media_type or getattr(message, "media_url", None)
                else {}
            ),
        }
        for message in rows.scalars()
        if message.id is not None
    ]
    if not turns:
        return ChatMemoryPairIndexResult()

    memory = BusinessBrainMemoryService(repository=CommercialSpineRepository(session))
    mined = await memory.mine_conversation_pairs(
        ConversationPairMiningInput(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            source_refs=[f"conversation:{conversation_id}:messages"],
            turns=turns,
            correlation_id=(
                f"chat-memory:{workspace_id}:{conversation_id}:pair-mining"
            ),
            trigger_message_ref=(
                f"message:{trigger_message_id}"
                if trigger_message_id is not None
                else None
            ),
        )
    )
    indexed_pair_count = sum(1 for pair in mined.pairs if pair.fact_created)
    fact_ids = [pair.fact.fact_id for pair in mined.pairs]
    indexed_source_unit_count = 0
    if fact_ids:
        indexed = await memory.rebuild_contextual_source_units(
            SourceUnitRebuildRequest(
                workspace_id=workspace_id,
                fact_types=["conversation_pair_fact"],
                candidate_fact_ids=fact_ids,
                embed_source_units=True,
            )
        )
        indexed_source_unit_count = len(indexed.source_units)
    return ChatMemoryPairIndexResult(
        indexed_pair_count=indexed_pair_count,
        indexed_source_unit_count=indexed_source_unit_count,
    )


async def _conversation_excluded_from_pair_index(
    session: AsyncSession,
    conversation: Conversation,
) -> bool:
    if str(conversation.external_chat_id or "").startswith("eval:"):
        return True
    customer = await session.get(Customer, conversation.customer_id)
    return customer is not None and str(customer.channel or "") == "eval_replay"
