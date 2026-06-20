from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_hydration_runtime import ConversationHydrationRuntime
from app.models.message import Message
from app.services.conversation_state import get_customer_conversation_state, message_effective_time

CONVERSATION_HYDRATION_IDLE = "idle"
CONVERSATION_HYDRATION_QUEUED = "queued"
CONVERSATION_HYDRATION_RUNNING = "running"
CONVERSATION_HYDRATION_READY = "ready"
CONVERSATION_HYDRATION_EMPTY = "empty"
CONVERSATION_HYDRATION_FAILED = "failed"
CONVERSATION_HYDRATION_DEFERRED = "deferred"

CONVERSATION_HYDRATION_RETRYABLE_STATES = frozenset({
    CONVERSATION_HYDRATION_IDLE,
    CONVERSATION_HYDRATION_QUEUED,
    CONVERSATION_HYDRATION_DEFERRED,
    CONVERSATION_HYDRATION_FAILED,
})

DEFAULT_REQUEST_LIMIT = 50
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 30.0
DEFAULT_RETRY_DELAY_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class ConversationHydrationProjection:
    schema_version: str
    state: str
    reason: str
    needed: bool
    can_retry: bool
    attempt_count: int
    max_attempts: int
    requested_count: int
    persisted_count: int
    duplicate_count: int
    last_error: str | None
    next_attempt_at: datetime | None
    requested_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    updated_at: datetime | None

    def to_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "state": self.state,
            "reason": self.reason,
            "needed": self.needed,
            "can_retry": self.can_retry,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "requested_count": self.requested_count,
            "persisted_count": self.persisted_count,
            "duplicate_count": self.duplicate_count,
            "last_error": self.last_error,
            "next_attempt_at": self.next_attempt_at,
            "requested_at": self.requested_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "failed_at": self.failed_at,
            "updated_at": self.updated_at,
        }


def conversation_needs_hydration(
    conversation: Conversation,
    *,
    latest_local_message: Message | None,
) -> bool:
    """Return true when dialog projection is ahead of canonical local rows."""
    if not _is_hydratable_channel(conversation):
        return False
    state = get_customer_conversation_state(conversation)
    dialog = state.sync.dialog if state.sync else None
    if dialog is None:
        return False
    has_dialog_tail = bool(
        dialog.top_message_id
        or dialog.last_message_date
        or dialog.last_message_text
        or conversation.last_message_at
        or conversation.summary
    )
    if not has_dialog_tail:
        return False
    if latest_local_message is None:
        return True
    local_at = message_effective_time(latest_local_message)
    dialog_at = _parse_datetime(dialog.last_message_date)
    if dialog_at is None:
        return False
    if local_at is None:
        return True
    return dialog_at > _ensure_aware_utc(local_at)


async def latest_local_message_for_conversation(
    session: AsyncSession,
    *,
    conversation_id: int,
) -> Message | None:
    latest_ts = func.coalesce(Message.telegram_timestamp, Message.created_at)
    return await session.scalar(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.is_deleted.is_(False),
        )
        .order_by(
            latest_ts.desc().nullslast(),
            Message.id.desc(),
        )
        .limit(1)
    )


async def get_conversation_hydration_runtime(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation_id: int,
) -> ConversationHydrationRuntime | None:
    return await session.scalar(
        select(ConversationHydrationRuntime).where(
            ConversationHydrationRuntime.workspace_id == workspace_id,
            ConversationHydrationRuntime.conversation_id == conversation_id,
        )
    )


async def enqueue_conversation_hydration(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation: Conversation,
    reason: str = "chat_open",
    requested_limit: int = DEFAULT_REQUEST_LIMIT,
    force: bool = False,
    now: datetime | None = None,
) -> ConversationHydrationRuntime | None:
    latest = await latest_local_message_for_conversation(
        session,
        conversation_id=conversation.id,
    )
    needed = force or conversation_needs_hydration(
        conversation,
        latest_local_message=latest,
    )
    if not needed:
        return await _mark_not_needed(
            session,
            workspace_id=workspace_id,
            conversation=conversation,
            reason=reason,
            requested_limit=requested_limit,
            now=now,
        )

    current_time = now or datetime.now(UTC)
    row = await get_conversation_hydration_runtime(
        session,
        workspace_id=workspace_id,
        conversation_id=conversation.id,
    )
    if row is None:
        row = ConversationHydrationRuntime(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            attempt_count=0,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            created_at=current_time,
        )

    if (
        not force
        and row.state in {CONVERSATION_HYDRATION_QUEUED, CONVERSATION_HYDRATION_RUNNING}
        and (row.leased_until is None or row.leased_until > current_time)
    ):
        return row

    row.state = CONVERSATION_HYDRATION_QUEUED
    row.reason = reason[:80]
    row.requested_limit = max(1, min(int(requested_limit or DEFAULT_REQUEST_LIMIT), 200))
    row.next_attempt_at = current_time
    row.requested_at = current_time
    row.failed_at = None
    row.completed_at = None
    row.last_error = None
    row.updated_at = current_time
    session.add(row)
    await session.flush()
    return row


async def claim_due_conversation_hydration_jobs(
    session: AsyncSession,
    *,
    lease_owner: str,
    limit: int = 25,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> list[ConversationHydrationRuntime]:
    current_time = now or datetime.now(UTC)
    stmt = (
        select(ConversationHydrationRuntime)
        .where(
            ConversationHydrationRuntime.state.in_([
                CONVERSATION_HYDRATION_QUEUED,
                CONVERSATION_HYDRATION_DEFERRED,
                CONVERSATION_HYDRATION_RUNNING,
            ]),
            ConversationHydrationRuntime.attempt_count < ConversationHydrationRuntime.max_attempts,
            or_(
                ConversationHydrationRuntime.next_attempt_at.is_(None),
                ConversationHydrationRuntime.next_attempt_at <= current_time,
            ),
            or_(
                ConversationHydrationRuntime.leased_until.is_(None),
                ConversationHydrationRuntime.leased_until <= current_time,
            ),
        )
        .order_by(
            ConversationHydrationRuntime.next_attempt_at.asc().nullsfirst(),
            ConversationHydrationRuntime.id.asc(),
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    jobs = list((await session.scalars(stmt)).all())
    leased_until = current_time + timedelta(seconds=max(lease_seconds, 1.0))
    for job in jobs:
        job.state = CONVERSATION_HYDRATION_RUNNING
        job.lease_owner = lease_owner
        job.leased_until = leased_until
        job.started_at = current_time
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.updated_at = current_time
        session.add(job)
    await session.flush()
    return jobs


async def mark_conversation_hydration_success(
    session: AsyncSession,
    *,
    runtime: ConversationHydrationRuntime,
    requested: int,
    persisted: int,
    duplicates: int,
    now: datetime | None = None,
) -> ConversationHydrationRuntime:
    current_time = now or datetime.now(UTC)
    runtime.requested_count = int(requested or 0)
    runtime.persisted_count = int(persisted or 0)
    runtime.duplicate_count = int(duplicates or 0)
    runtime.state = (
        CONVERSATION_HYDRATION_READY
        if persisted > 0 or duplicates > 0
        else CONVERSATION_HYDRATION_EMPTY
    )
    runtime.completed_at = current_time
    runtime.last_synced_at = current_time
    runtime.failed_at = None
    runtime.last_error = None
    runtime.next_attempt_at = None
    runtime.leased_until = None
    runtime.lease_owner = None
    runtime.updated_at = current_time
    session.add(runtime)
    await session.flush()
    return runtime


async def mark_conversation_hydration_failed(
    session: AsyncSession,
    *,
    runtime: ConversationHydrationRuntime,
    error: str,
    now: datetime | None = None,
) -> ConversationHydrationRuntime:
    current_time = now or datetime.now(UTC)
    runtime.last_error = error[:2000]
    runtime.failed_at = current_time
    runtime.leased_until = None
    runtime.lease_owner = None
    if int(runtime.attempt_count or 0) >= int(runtime.max_attempts or DEFAULT_MAX_ATTEMPTS):
        runtime.state = CONVERSATION_HYDRATION_FAILED
        runtime.next_attempt_at = None
    else:
        runtime.state = CONVERSATION_HYDRATION_DEFERRED
        runtime.next_attempt_at = current_time + timedelta(seconds=DEFAULT_RETRY_DELAY_SECONDS)
    runtime.updated_at = current_time
    session.add(runtime)
    await session.flush()
    return runtime


def project_conversation_hydration_runtime(
    runtime: ConversationHydrationRuntime | None,
    *,
    needed: bool,
) -> ConversationHydrationProjection:
    if runtime is None:
        return ConversationHydrationProjection(
            schema_version="conversation_hydration_runtime.v1",
            state=CONVERSATION_HYDRATION_IDLE,
            reason="chat_open",
            needed=needed,
            can_retry=needed,
            attempt_count=0,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            requested_count=0,
            persisted_count=0,
            duplicate_count=0,
            last_error=None,
            next_attempt_at=None,
            requested_at=None,
            started_at=None,
            completed_at=None,
            failed_at=None,
            updated_at=None,
        )
    can_retry = (
        runtime.state in CONVERSATION_HYDRATION_RETRYABLE_STATES
        and int(runtime.attempt_count or 0) < int(runtime.max_attempts or DEFAULT_MAX_ATTEMPTS)
    )
    return ConversationHydrationProjection(
        schema_version="conversation_hydration_runtime.v1",
        state=runtime.state,
        reason=runtime.reason,
        needed=needed,
        can_retry=can_retry,
        attempt_count=int(runtime.attempt_count or 0),
        max_attempts=int(runtime.max_attempts or DEFAULT_MAX_ATTEMPTS),
        requested_count=int(runtime.requested_count or 0),
        persisted_count=int(runtime.persisted_count or 0),
        duplicate_count=int(runtime.duplicate_count or 0),
        last_error=runtime.last_error,
        next_attempt_at=runtime.next_attempt_at,
        requested_at=runtime.requested_at,
        started_at=runtime.started_at,
        completed_at=runtime.completed_at,
        failed_at=runtime.failed_at,
        updated_at=runtime.updated_at,
    )


async def _mark_not_needed(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation: Conversation,
    reason: str,
    requested_limit: int,
    now: datetime | None,
) -> ConversationHydrationRuntime | None:
    row = await get_conversation_hydration_runtime(
        session,
        workspace_id=workspace_id,
        conversation_id=conversation.id,
    )
    if row is None:
        return None
    current_time = now or datetime.now(UTC)
    if row.state in {CONVERSATION_HYDRATION_QUEUED, CONVERSATION_HYDRATION_DEFERRED}:
        row.state = CONVERSATION_HYDRATION_IDLE
    row.reason = reason[:80]
    row.requested_limit = max(1, min(int(requested_limit or DEFAULT_REQUEST_LIMIT), 200))
    row.updated_at = current_time
    session.add(row)
    await session.flush()
    return row


def _is_hydratable_channel(conversation: Conversation) -> bool:
    return (conversation.channel or "").strip().lower() in {"telegram_dm", "telegram", "dm"}


def _parse_datetime(value: str | None) -> datetime | None:
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
