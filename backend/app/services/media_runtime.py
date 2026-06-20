from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.media_runtime import MediaRuntime
from app.models.message import Message

MEDIA_ACTION_PENDING = "pending"
MEDIA_ACTION_DEFERRED = "deferred"
MEDIA_ACTION_LEASED = "leased"
MEDIA_ACTION_COMPLETED = "completed"
MEDIA_ACTION_FAILED = "failed"
MEDIA_ACTION_NOT_APPLICABLE = "not_applicable"

MEDIA_TERMINAL_ACTION_STATES = frozenset({
    MEDIA_ACTION_COMPLETED,
    MEDIA_ACTION_FAILED,
    MEDIA_ACTION_NOT_APPLICABLE,
})
MEDIA_RETRYABLE_ACTION_STATES = frozenset({
    MEDIA_ACTION_PENDING,
    MEDIA_ACTION_DEFERRED,
})

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 60.0
DEFAULT_RETRY_DELAY_SECONDS = 30.0
DEFAULT_PENDING_GRACE_SECONDS = 10.0
UNSUPPORTED_SEMANTIC_MEDIA_TYPES = frozenset({"gif", "sticker"})


@dataclass(frozen=True, slots=True)
class MediaRuntimeProjection:
    hydration_status: str
    asset_state: str
    semantic_state: str
    action_state: str
    ai_relevant: bool
    retry_after_seconds: float | None
    mime_type: str | None
    normalized_text: str | None
    commercial_semantics: dict | None


def media_ref_for_message(conversation: Conversation, message: Message) -> str:
    """Stable channel media identity used by the durable media action plane."""
    channel = message.channel or conversation.channel or "telegram_dm"
    chat_ref = (
        str(conversation.telegram_chat_id)
        if conversation.telegram_chat_id is not None
        else (conversation.external_chat_id or str(conversation.id))
    )
    message_ref = (
        str(message.telegram_message_id)
        if message.telegram_message_id is not None
        else (message.external_message_id or str(message.id))
    )
    return f"{channel}:{chat_ref}:{message_ref}"


def project_media_runtime_from_message(message: Message) -> MediaRuntimeProjection:
    metadata = message.media_metadata if isinstance(message.media_metadata, dict) else {}
    runtime = metadata.get("media_runtime") if isinstance(metadata.get("media_runtime"), dict) else {}
    media_type = str(message.media_type or "").strip().lower()
    ai_relevant = bool(metadata.get("ai_relevant")) if "ai_relevant" in metadata else True
    if media_type in UNSUPPORTED_SEMANTIC_MEDIA_TYPES:
        ai_relevant = False
    hydration_status = str(metadata.get("hydration_status") or "").strip().lower()
    if not hydration_status:
        hydration_status = (
            "unsupported"
            if media_type in UNSUPPORTED_SEMANTIC_MEDIA_TYPES
            else ("pending" if ai_relevant else "not_applicable")
        )

    asset_state = str(runtime.get("asset_state") or _asset_state_for_status(hydration_status))
    semantic_state = str(
        runtime.get("semantic_state")
        or _semantic_state_for_status(hydration_status=hydration_status, ai_relevant=ai_relevant)
    )
    return MediaRuntimeProjection(
        hydration_status=hydration_status,
        asset_state=asset_state,
        semantic_state=semantic_state,
        action_state=_action_state_for_status(hydration_status),
        ai_relevant=ai_relevant,
        retry_after_seconds=_safe_float(metadata.get("retry_after_seconds")),
        mime_type=_safe_str(metadata.get("mime_type")),
        normalized_text=_safe_str(metadata.get("normalized_text")),
        commercial_semantics=_safe_dict(
            metadata.get("media_evidence") or metadata.get("commercial_semantics")
        ),
    )


async def ensure_media_runtime_for_message(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation: Conversation,
    message: Message,
    now: datetime | None = None,
) -> MediaRuntime | None:
    """Create/update the durable media runtime projection for one message."""
    if not message.media_type:
        return None

    current_time = now or datetime.now(timezone.utc)
    projection = project_media_runtime_from_message(message)
    runtime = await session.scalar(
        select(MediaRuntime).where(MediaRuntime.message_id == message.id).limit(1)
    )
    if runtime is None:
        runtime = MediaRuntime(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            message_id=message.id,
            channel=message.channel or conversation.channel or "telegram_dm",
            media_type=message.media_type,
            media_ref=media_ref_for_message(conversation, message),
            attempt_count=0,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            created_at=current_time,
        )

    runtime.workspace_id = workspace_id
    runtime.conversation_id = conversation.id
    runtime.channel = message.channel or conversation.channel or runtime.channel
    runtime.media_type = message.media_type
    runtime.media_ref = media_ref_for_message(conversation, message)
    _apply_projection(runtime, projection, now=current_time, preserve_attempts=True)
    session.add(runtime)
    await session.flush()
    return runtime


async def update_media_runtime_after_hydration(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation: Conversation,
    message: Message,
    error: str | None = None,
    now: datetime | None = None,
) -> MediaRuntime | None:
    """Record one hydration attempt outcome on the durable action row."""
    if not message.media_type:
        return None

    current_time = now or datetime.now(timezone.utc)
    runtime = await ensure_media_runtime_for_message(
        session,
        workspace_id=workspace_id,
        conversation=conversation,
        message=message,
        now=current_time,
    )
    if runtime is None:
        return None

    runtime.attempt_count += 1
    runtime.last_attempt_at = current_time
    runtime.last_error = error
    projection = project_media_runtime_from_message(message)
    _apply_projection(runtime, projection, now=current_time, preserve_attempts=True)
    _mark_retry_exhausted_if_needed(runtime, message=message, now=current_time)
    session.add(runtime)
    await session.flush()
    return runtime


async def claim_due_media_hydration_jobs(
    session: AsyncSession,
    *,
    workspace_id: int,
    lease_owner: str,
    limit: int = 25,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    pending_grace_seconds: float = 0.0,
    now: datetime | None = None,
) -> list[MediaRuntime]:
    """Lease due media hydration jobs for one workspace.

    This is the durable action-plane primitive. Workers can restart safely
    because due work is represented in Postgres, not only in an in-process sleep.
    """
    current_time = now or datetime.now(timezone.utc)
    pending_ready_at = current_time - timedelta(seconds=max(pending_grace_seconds, 0.0))
    stmt = (
        select(MediaRuntime)
        .where(
            MediaRuntime.workspace_id == workspace_id,
            MediaRuntime.ai_relevant.is_(True),
            MediaRuntime.action_state.in_([
                MEDIA_ACTION_PENDING,
                MEDIA_ACTION_DEFERRED,
                MEDIA_ACTION_LEASED,
            ]),
            MediaRuntime.attempt_count < MediaRuntime.max_attempts,
            or_(
                MediaRuntime.action_state != MEDIA_ACTION_PENDING,
                MediaRuntime.next_attempt_at <= pending_ready_at,
                (MediaRuntime.next_attempt_at.is_(None) & (MediaRuntime.created_at <= pending_ready_at)),
            ),
            or_(MediaRuntime.next_attempt_at.is_(None), MediaRuntime.next_attempt_at <= current_time),
            or_(MediaRuntime.leased_until.is_(None), MediaRuntime.leased_until <= current_time),
        )
        .order_by(MediaRuntime.next_attempt_at.asc().nullsfirst(), MediaRuntime.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    jobs = list((await session.scalars(stmt)).all())
    leased_until = current_time + timedelta(seconds=lease_seconds)
    for job in jobs:
        job.action_state = MEDIA_ACTION_LEASED
        job.lease_owner = lease_owner
        job.leased_until = leased_until
        session.add(job)
    await session.flush()
    return jobs


async def claim_due_media_hydration_jobs_for_all_workspaces(
    session: AsyncSession,
    *,
    lease_owner: str,
    limit: int = 25,
    max_claims_per_workspace: int = 2,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    pending_grace_seconds: float = DEFAULT_PENDING_GRACE_SECONDS,
    now: datetime | None = None,
) -> list[MediaRuntime]:
    """Lease due media jobs across tenants without scanning message routes."""
    if limit <= 0:
        return []

    current_time = now or datetime.now(timezone.utc)
    pending_ready_at = current_time - timedelta(seconds=max(pending_grace_seconds, 0.0))
    workspace_rows = await session.execute(
        select(MediaRuntime.workspace_id)
        .where(
            MediaRuntime.ai_relevant.is_(True),
            MediaRuntime.action_state.in_([
                MEDIA_ACTION_PENDING,
                MEDIA_ACTION_DEFERRED,
                MEDIA_ACTION_LEASED,
            ]),
            MediaRuntime.attempt_count < MediaRuntime.max_attempts,
            or_(
                MediaRuntime.action_state != MEDIA_ACTION_PENDING,
                MediaRuntime.next_attempt_at <= pending_ready_at,
                (MediaRuntime.next_attempt_at.is_(None) & (MediaRuntime.created_at <= pending_ready_at)),
            ),
            or_(MediaRuntime.next_attempt_at.is_(None), MediaRuntime.next_attempt_at <= current_time),
            or_(MediaRuntime.leased_until.is_(None), MediaRuntime.leased_until <= current_time),
        )
        .distinct()
        .order_by(MediaRuntime.workspace_id.asc())
        .limit(limit)
    )
    jobs: list[MediaRuntime] = []
    for workspace_id in workspace_rows.scalars().all():
        remaining = limit - len(jobs)
        if remaining <= 0:
            break
        jobs.extend(
            await claim_due_media_hydration_jobs(
                session,
                workspace_id=int(workspace_id),
                lease_owner=lease_owner,
                limit=min(remaining, max(max_claims_per_workspace, 1)),
                lease_seconds=lease_seconds,
                pending_grace_seconds=pending_grace_seconds,
                now=current_time,
            )
        )
    return jobs


def _apply_projection(
    runtime: MediaRuntime,
    projection: MediaRuntimeProjection,
    *,
    now: datetime,
    preserve_attempts: bool,
) -> None:
    runtime.hydration_status = projection.hydration_status
    runtime.asset_state = projection.asset_state
    runtime.semantic_state = projection.semantic_state
    runtime.ai_relevant = projection.ai_relevant
    runtime.retry_after_seconds = projection.retry_after_seconds
    runtime.mime_type = projection.mime_type
    runtime.normalized_text = projection.normalized_text
    runtime.commercial_semantics = projection.commercial_semantics
    runtime.updated_at = now
    if not preserve_attempts:
        runtime.attempt_count = 0

    runtime.action_state = projection.action_state
    if runtime.action_state in MEDIA_TERMINAL_ACTION_STATES:
        runtime.next_attempt_at = None
        runtime.leased_until = None
        runtime.lease_owner = None
        runtime.completed_at = runtime.completed_at or now
        return

    runtime.completed_at = None
    runtime.leased_until = None
    runtime.lease_owner = None
    if runtime.action_state == MEDIA_ACTION_DEFERRED:
        delay = (
            projection.retry_after_seconds
            if projection.retry_after_seconds is not None
            else DEFAULT_RETRY_DELAY_SECONDS
        )
        runtime.next_attempt_at = now + timedelta(seconds=delay)
    elif runtime.next_attempt_at is None:
        runtime.next_attempt_at = now


def _mark_retry_exhausted_if_needed(
    runtime: MediaRuntime,
    *,
    message: Message,
    now: datetime,
) -> None:
    if (
        not runtime.ai_relevant
        or runtime.action_state not in MEDIA_RETRYABLE_ACTION_STATES
        or int(runtime.attempt_count or 0) < int(runtime.max_attempts or DEFAULT_MAX_ATTEMPTS)
    ):
        return

    runtime.hydration_status = "unavailable"
    runtime.asset_state = "unavailable"
    runtime.semantic_state = "unavailable"
    runtime.action_state = MEDIA_ACTION_FAILED
    runtime.next_attempt_at = None
    runtime.leased_until = None
    runtime.lease_owner = None
    runtime.completed_at = now
    runtime.last_error = runtime.last_error or "retry_exhausted"
    _sync_message_metadata_from_runtime(message, runtime)


def _sync_message_metadata_from_runtime(message: Message, runtime: MediaRuntime) -> None:
    metadata = dict(message.media_metadata or {})
    metadata["hydration_status"] = runtime.hydration_status
    metadata.pop("retry_after_seconds", None)
    metadata["media_runtime"] = {
        "asset_state": runtime.asset_state,
        "semantic_state": runtime.semantic_state,
        "ai_relevant": runtime.ai_relevant,
    }
    if runtime.commercial_semantics is not None:
        metadata["media_evidence"] = runtime.commercial_semantics
    else:
        metadata.pop("media_evidence", None)
    message.media_metadata = metadata


def _action_state_for_status(hydration_status: str) -> str:
    if hydration_status == "hydrated":
        return MEDIA_ACTION_COMPLETED
    if hydration_status in {"not_applicable", "unsupported"}:
        return MEDIA_ACTION_NOT_APPLICABLE
    if hydration_status in {"unavailable", "failed", "expired"}:
        return MEDIA_ACTION_FAILED
    if hydration_status in {"deferred", "retrying"}:
        return MEDIA_ACTION_DEFERRED
    return MEDIA_ACTION_PENDING


def _asset_state_for_status(hydration_status: str) -> str:
    if hydration_status == "unsupported":
        return "unsupported"
    if hydration_status in {"unavailable", "failed", "expired"}:
        return "unavailable"
    if hydration_status in {"deferred", "retrying"}:
        return "retrying"
    if hydration_status == "hydrated":
        return "stream_ready"
    return "metadata_only"


def _semantic_state_for_status(*, hydration_status: str, ai_relevant: bool) -> str:
    if not ai_relevant:
        return "not_applicable"
    if hydration_status == "hydrated":
        return "ready"
    if hydration_status in {"deferred", "retrying"}:
        return "retrying"
    if hydration_status in {"unavailable", "failed", "expired"}:
        return "unavailable"
    return "pending"


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _safe_dict(value: object) -> dict | None:
    return dict(value) if isinstance(value, dict) else None
