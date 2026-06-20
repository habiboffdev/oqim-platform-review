"""Onboarding runtime state and background execution.

Telegram auth routes decide whether a workspace is allowed to start onboarding.
This module owns progress, events, idempotent start, and the ingestion bridge so
the route does not become runtime authority.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.async_tasks import spawn_guarded_task
from app.core.config import get_settings
from app.core.consumer_names import make_consumer_name
from app.core.event_spine import EventSpine
from app.core.logging import get_logger
from app.db.session import async_session
from app.models.onboarding_runtime import OnboardingRuntime
from app.models.workspace import Workspace
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.learned_review import (
    build_onboarding_learned_review_projection,
)
from app.modules.onboarding_learning.source_progress import (
    build_onboarding_source_learning_projection,
)
from app.modules.onboarding_learning.source_runtime import (
    OnboardingSourceLearningRuntimeService,
    OnboardingSourceRuntimeItem,
    OnboardingSourceRuntimeResult,
)
from app.services.channel_conversation_sync import ChannelConversationSync
from app.services.onboarding_ingestion import (
    OnboardingIngestionPipeline,
)
from app.services.worker_lease import WorkerLease

logger = get_logger("services.onboarding_runtime")

_bg_tasks: set = set()
ONBOARDING_RUNTIME_IDLE = "idle"
ONBOARDING_RUNTIME_PENDING = "pending"
ONBOARDING_RUNTIME_RUNNING = "running"
ONBOARDING_RUNTIME_COMPLETED = "completed"
ONBOARDING_RUNTIME_FAILED = "failed"
ONBOARDING_RUNTIME_DLQ = "dlq"
ONBOARDING_RUNTIME_TERMINAL_STATES = {
    ONBOARDING_RUNTIME_COMPLETED,
    ONBOARDING_RUNTIME_DLQ,
}
ONBOARDING_RUNTIME_RETRYABLE_STATES = {
    ONBOARDING_RUNTIME_FAILED,
    ONBOARDING_RUNTIME_DLQ,
}
DEFAULT_RUNTIME_LEASE_SECONDS = 15 * 60
DEFAULT_RUNTIME_MAX_ATTEMPTS = 3
DEFAULT_RUNTIME_RETRY_DELAY_SECONDS = 60
DEFAULT_RUNTIME_LEASE_OWNER = "onboarding-ingestion"
DEFAULT_RUNTIME_BATCH_SIZE = 2
DEFAULT_RUNTIME_POLL_INTERVAL_SECONDS = 2.0
_PHASE_STAGE_MAP = {
    "not_started": "auth_linked",
    "starting": "auth_linked",
    "reading_dialogs": "dialogs_scanned",
    "classifying_contacts": "contacts_classified",
    "generating_voice_profile": "voice_profile_ready",
    "review_learnings": "knowledge_extracted",
    "awaiting_channels": "completed",
    "done": "completed",
    "error": "completed",
}


def default_ingestion_progress(workspace_id: int) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "phase": "not_started",
        "percent": 0,
        "contacts_found": 0,
        "customers_identified": 0,
        "visible_dialog_limit": 50,
        "history_learning_conversation_limit": 50,
        "history_learning_message_limit": 12,
        "history_prefetched_conversations": 0,
        "history_replayed_conversations": 0,
        "history_replayed_messages": 0,
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


async def get_redis() -> aioredis.Redis:
    settings = get_settings()
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def store_progress(workspace_id: int, progress: dict[str, Any]) -> None:
    r = await get_redis()
    try:
        await r.set(
            f"ingestion:progress:{workspace_id}",
            json.dumps(progress, default=str),
            ex=3600,
        )
    finally:
        await r.aclose()


async def load_progress(workspace_id: int) -> dict[str, Any] | None:
    r = await get_redis()
    try:
        data = await r.get(f"ingestion:progress:{workspace_id}")
    finally:
        await r.aclose()
    return json.loads(data) if data else None


def ingestion_progress_is_active(progress: dict[str, Any] | None) -> bool:
    if not progress:
        return False
    if progress.get("completed"):
        return False
    return progress.get("phase") not in {None, "not_started", "error"}


def onboarding_runtime_is_active(
    runtime: OnboardingRuntime | None,
    *,
    now: datetime | None = None,
) -> bool:
    if runtime is None:
        return False
    if runtime.state == ONBOARDING_RUNTIME_PENDING:
        return True
    if runtime.state != ONBOARDING_RUNTIME_RUNNING:
        return False
    if runtime.leased_until is None:
        return True
    current_time = now or datetime.now(UTC)
    leased_until = runtime.leased_until
    if leased_until.tzinfo is None:
        leased_until = leased_until.replace(tzinfo=UTC)
    return leased_until > current_time


def onboarding_runtime_can_requeue(runtime: OnboardingRuntime | None) -> bool:
    if runtime is None:
        return False
    return runtime.state in ONBOARDING_RUNTIME_RETRYABLE_STATES


def _onboarding_stage(
    stage_id: str,
    *,
    label: str,
    status: str,
    percent: int,
    detail: str | None = None,
    retryable: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "id": stage_id,
        "label": label,
        "status": status,
        "percent": max(0, min(int(percent), 100)),
        "detail": detail,
        "retryable": retryable,
        "error": error,
    }


def _merge_progress_floor(base: dict[str, Any], latest: dict[str, Any]) -> dict[str, Any]:
    """Merge newer volatile progress into durable runtime progress without regressing."""
    if not latest:
        return base

    merged = dict(base)
    base_percent = int(merged.get("percent") or 0)
    latest_percent = int(latest.get("percent") or 0)
    if latest_percent >= base_percent:
        merged["phase"] = latest.get("phase") or merged.get("phase")
    merged["percent"] = max(base_percent, latest_percent)

    for key in (
        "contacts_found",
        "customers_identified",
        "history_prefetched_conversations",
        "history_replayed_conversations",
        "history_replayed_messages",
        "products_extracted",
        "knowledge_items",
    ):
        merged[key] = max(int(merged.get(key) or 0), int(latest.get(key) or 0))

    for key in (
        "visible_dialog_limit",
        "history_learning_conversation_limit",
        "history_learning_message_limit",
    ):
        merged[key] = int(latest.get(key) or merged.get(key) or 0)

    for key in (
        "completed",
        "voice_profile_ready",
        "voice_profile_degraded",
        "contact_classification_degraded",
        "ai_learning_degraded",
    ):
        merged[key] = bool(merged.get(key)) or bool(latest.get(key))

    for key in ("voice_profile_error", "ai_learning_error"):
        merged[key] = latest.get(key) or merged.get(key)

    latest_discoveries = latest.get("voice_discoveries")
    if latest_discoveries:
        merged["voice_discoveries"] = latest_discoveries
    else:
        merged.setdefault("voice_discoveries", [])

    errors = []
    for value in [*(merged.get("errors") or []), *(latest.get("errors") or [])]:
        if value not in errors:
            errors.append(value)
    merged["errors"] = errors
    return merged


def build_onboarding_stage_projection(
    *,
    workspace: Workspace,
    runtime: OnboardingRuntime | None,
    progress: dict[str, Any],
    source_learning: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    phase = str(
        (runtime.phase if runtime is not None else None)
        or progress.get("phase")
        or "not_started"
    )
    current_stage_id = _PHASE_STAGE_MAP.get(phase, "completed")
    runtime_failed = bool(runtime and runtime.state in {ONBOARDING_RUNTIME_FAILED, ONBOARDING_RUNTIME_DLQ})
    failed_status = "dlq" if runtime and runtime.state == ONBOARDING_RUNTIME_DLQ else "failed"
    can_requeue = onboarding_runtime_can_requeue(runtime)

    contacts_found = int(progress.get("contacts_found") or 0)
    customers_identified = int(progress.get("customers_identified") or 0)
    voice_ready = bool(progress.get("voice_profile_ready"))
    voice_degraded = bool(progress.get("voice_profile_degraded"))
    contact_degraded = bool(progress.get("contact_classification_degraded"))

    def failed_here(stage_id: str) -> bool:
        return runtime_failed and current_stage_id == stage_id

    def failed_error(stage_id: str) -> str | None:
        return runtime.last_error if runtime is not None and failed_here(stage_id) else None

    dialogs_done = contacts_found > 0 or phase not in {"not_started", "starting", "reading_dialogs"}
    if failed_here("dialogs_scanned"):
        dialogs_status = failed_status
    elif dialogs_done:
        dialogs_status = "completed"
    elif phase == "reading_dialogs":
        dialogs_status = "running"
    else:
        dialogs_status = "pending"

    if failed_here("contacts_classified"):
        contacts_status = failed_status
    elif contact_degraded:
        contacts_status = "degraded"
    elif customers_identified > 0:
        contacts_status = "completed"
    elif phase == "classifying_contacts":
        contacts_status = "running"
    else:
        contacts_status = "pending"

    if failed_here("voice_profile_ready"):
        voice_status = failed_status
    elif voice_ready:
        voice_status = "completed"
    elif voice_degraded:
        voice_status = "degraded"
    elif phase == "generating_voice_profile":
        voice_status = "running"
    else:
        voice_status = "pending"

    completed = bool(
        getattr(workspace, "onboarding_completed", False)
        or (runtime and runtime.state == ONBOARDING_RUNTIME_COMPLETED)
    )
    completed_status = failed_status if failed_here("completed") else "completed" if completed else "pending"

    not_owned_detail = "Bu bosqich hali boshlanmagan."
    source_stage_status = _source_learning_stage_status(source_learning)
    source_stage_percent = int((source_learning or {}).get("percent") or 0)
    source_stage_detail = _source_learning_stage_detail(source_learning) if source_learning else not_owned_detail
    stages = [
        _onboarding_stage(
            "auth_linked",
            label="Telegram ulandi",
            status="completed" if getattr(workspace, "telegram_connected", False) else "pending",
            percent=100 if getattr(workspace, "telegram_connected", False) else 0,
        ),
        _onboarding_stage(
            "dialogs_scanned",
            label="Suhbatlar o‘qildi",
            status=dialogs_status,
            percent=100 if contacts_found > 0 else 35 if phase == "reading_dialogs" else 0,
            detail=f"{contacts_found} ta suhbat ko‘rildi" if contacts_found else None,
            retryable=failed_here("dialogs_scanned") and can_requeue,
            error=failed_error("dialogs_scanned"),
        ),
        _onboarding_stage(
            "contacts_classified",
            label="Suhbatlar saralandi",
            status=contacts_status,
            percent=100 if customers_identified > 0 else 45 if phase == "classifying_contacts" else 0,
            detail=(
                f"{contacts_found} ta suhbatdan {customers_identified} tasi savdo mijoziga o‘xshaydi"
                if customers_identified and contacts_found
                else f"{customers_identified} ta savdo mijoziga o‘xshash suhbat ajratildi"
                if customers_identified
                else None
            ),
            retryable=(failed_here("contacts_classified") and can_requeue) or contact_degraded,
            error=failed_error("contacts_classified") or (
                progress.get("ai_learning_error") if contact_degraded else None
            ),
        ),
        _onboarding_stage(
            "catalog_extracted",
            label="Katalog o‘rganildi",
            status=source_stage_status,
            percent=source_stage_percent,
            detail=source_stage_detail,
        ),
        _onboarding_stage(
            "knowledge_extracted",
            label="Bilimlar o‘rganildi",
            status=source_stage_status,
            percent=source_stage_percent,
            detail=source_stage_detail,
        ),
        _onboarding_stage(
            "voice_profile_ready",
            label="Sotuvchi uslubi tayyor",
            status=voice_status,
            percent=100 if voice_ready else 60 if phase == "generating_voice_profile" else 0,
            retryable=(failed_here("voice_profile_ready") and can_requeue) or voice_degraded,
            error=failed_error("voice_profile_ready") or (
                progress.get("voice_profile_error") if voice_degraded else None
            ),
        ),
        _onboarding_stage(
            "embeddings_ready",
            label="Qidiruv tayyor",
            status=source_stage_status,
            percent=source_stage_percent,
            detail=source_stage_detail,
        ),
        _onboarding_stage(
            "completed",
            label="Sozlash tugadi",
            status=completed_status,
            percent=100 if completed else 0,
            retryable=failed_here("completed") and can_requeue,
            error=failed_error("completed"),
        ),
    ]
    return current_stage_id, stages


def _source_learning_stage_status(source_learning: dict[str, Any] | None) -> str:
    if not source_learning or int(source_learning.get("summary", {}).get("total") or 0) == 0:
        return "not_applicable"
    status = str(source_learning.get("status") or "")
    if status == "learning":
        return "running"
    if status == "retrying":
        return "retryable"
    if status == "learned":
        return "completed"
    if status in {"needs_review", "conflict"}:
        return status
    if status in {"failed", "missing"}:
        return "failed"
    return "pending"


def _source_learning_stage_detail(source_learning: dict[str, Any] | None) -> str | None:
    if not source_learning:
        return None
    summary = dict(source_learning.get("summary") or {})
    total = int(summary.get("total") or 0)
    if total == 0:
        return "Manbalar hali qo‘shilmagan."
    return (
        f"{int(summary.get('learning') or 0)} ta o‘rganilyapti, "
        f"{int(summary.get('needs_review') or 0)} ta tasdiqlashga tayyor, "
        f"{int(summary.get('learned') or 0)} ta o‘rganildi, "
        f"{int(summary.get('failed') or 0) + int(summary.get('missing') or 0) + int(summary.get('conflict') or 0)} ta yordam kerak."
    )


def build_onboarding_runtime_projection(
    *,
    workspace: Workspace,
    runtime: OnboardingRuntime | None,
    progress: dict[str, Any],
    now: datetime | None = None,
    source_learning: dict[str, Any] | None = None,
    learned_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_time = now or datetime.now(UTC)
    state = runtime.state if runtime is not None else (
        ONBOARDING_RUNTIME_COMPLETED
        if getattr(workspace, "onboarding_completed", False)
        else ONBOARDING_RUNTIME_IDLE
    )
    phase = (
        runtime.phase
        if runtime is not None
        else str(progress.get("phase") or "not_started")
    )
    percent = (
        int(runtime.percent or 0)
        if runtime is not None
        else int(progress.get("percent") or 0)
    )
    progress_snapshot = (
        _merge_progress_floor(_progress_snapshot_for_runtime(runtime), dict(progress))
        if runtime is not None
        else dict(progress)
    )
    completed = bool(
        getattr(workspace, "onboarding_completed", False)
        or state == ONBOARDING_RUNTIME_COMPLETED
    )
    if completed:
        progress_snapshot["phase"] = "done"
        progress_snapshot["percent"] = 100
        progress_snapshot["completed"] = True
    if source_learning:
        progress_snapshot["source_learning"] = source_learning
    if learned_review:
        progress_snapshot["learned_review"] = learned_review
    current_stage_id, stages = build_onboarding_stage_projection(
        workspace=workspace,
        runtime=runtime,
        progress=progress_snapshot,
        source_learning=source_learning,
    )
    lease_expired = bool(
        runtime
        and runtime.leased_until is not None
        and (
            runtime.leased_until.replace(tzinfo=UTC)
            if runtime.leased_until.tzinfo is None
            else runtime.leased_until
        ) <= current_time
    )

    return {
        "schema_version": "onboarding_runtime.v1",
        "workspace_id": workspace.id,
        "state": state,
        "phase": str(progress_snapshot.get("phase") or phase),
        "percent": max(percent, int(progress_snapshot.get("percent") or 0)),
        "current_stage_id": current_stage_id,
        "stages": stages,
        "is_running": onboarding_runtime_is_active(runtime, now=current_time),
        "is_terminal": state in ONBOARDING_RUNTIME_TERMINAL_STATES,
        "is_retryable": state in ONBOARDING_RUNTIME_RETRYABLE_STATES,
        "is_dlq": state == ONBOARDING_RUNTIME_DLQ,
        "can_requeue": onboarding_runtime_can_requeue(runtime),
        "lease_expired": lease_expired,
        "attempt_count": int(runtime.attempt_count or 0) if runtime else 0,
        "max_attempts": int(runtime.max_attempts or DEFAULT_RUNTIME_MAX_ATTEMPTS)
        if runtime
        else DEFAULT_RUNTIME_MAX_ATTEMPTS,
        "lease_owner": runtime.lease_owner if runtime else None,
        "leased_until": runtime.leased_until if runtime else None,
        "next_attempt_at": runtime.next_attempt_at if runtime else None,
        "started_at": runtime.started_at if runtime else None,
        "completed_at": runtime.completed_at if runtime else None,
        "failed_at": runtime.failed_at if runtime else None,
        "last_error": runtime.last_error if runtime else None,
        "progress": progress_snapshot,
        "source_learning": source_learning
        or build_onboarding_source_learning_projection(source_facts=()),
        "learned_review": learned_review
        or build_onboarding_learned_review_projection(facts=()),
    }


async def try_acquire_ingestion_start_lock(workspace_id: int) -> bool:
    r = await get_redis()
    try:
        return bool(await r.set(f"ingestion:start-lock:{workspace_id}", "1", ex=30, nx=True))
    finally:
        await r.aclose()


async def release_ingestion_start_lock(workspace_id: int) -> None:
    r = await get_redis()
    try:
        await r.delete(f"ingestion:start-lock:{workspace_id}")
    finally:
        await r.aclose()


def apply_progress_db_floor(
    progress: dict[str, Any],
    *,
    contact_count: int,
    customer_count: int,
    voice_projection: Any | None = None,
) -> dict[str, Any]:
    """Advance stale Redis progress to the minimum state already proven by DB."""
    reconciled = dict(progress)
    reconciled.setdefault("voice_profile_degraded", False)
    reconciled.setdefault("voice_profile_error", None)
    reconciled.setdefault("contact_classification_degraded", False)
    reconciled.setdefault("ai_learning_degraded", False)
    reconciled.setdefault("ai_learning_error", None)
    reconciled["contacts_found"] = max(int(reconciled.get("contacts_found") or 0), int(contact_count))
    reconciled["customers_identified"] = max(
        int(reconciled.get("customers_identified") or 0),
        int(customer_count),
    )

    percent = int(reconciled.get("percent") or 0)
    if int(contact_count) > 0 and percent < 35:
        reconciled["phase"] = "reading_dialogs"
        reconciled["percent"] = 35
        percent = 35
    if int(customer_count) > 0 and percent < 45:
        reconciled["phase"] = "classifying_contacts"
        reconciled["percent"] = 45
        percent = 45

    if voice_projection_has_real_signal(voice_projection):
        reconciled["voice_profile_ready"] = True
        reconciled["voice_profile_degraded"] = False
        reconciled["voice_profile_error"] = None
        if not reconciled.get("completed") and percent < 65:
            reconciled["phase"] = "awaiting_channels"
            reconciled["percent"] = 65
    else:
        reconciled["voice_profile_ready"] = False
        if reconciled.get("phase") == "awaiting_channels":
            reconciled["phase"] = "reading_dialogs"
            reconciled["percent"] = min(percent, 55)

    return reconciled


def voice_projection_has_real_signal(projection: Any | None) -> bool:
    if projection is None or getattr(projection, "degraded", False):
        return False
    state = getattr(projection, "state", None)
    if not isinstance(state, dict):
        return False
    traits = state.get("traits")
    if not isinstance(traits, list):
        return False
    for trait in traits:
        if not isinstance(trait, dict):
            continue
        if int(trait.get("message_count_analyzed") or 0) >= 1:
            return True
        if trait.get("voice_card") or trait.get("profile_text") or trait.get("guidance"):
            return True
    return False


async def reconcile_progress_with_db(
    workspace_id: int,
    progress: dict[str, Any],
    *,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """Keep onboarding progress honest when Redis lags behind DB truth."""
    from app.models.conversation import Conversation
    from app.models.customer import Customer
    from app.modules.commercial_spine.repository import CommercialSpineRepository

    async def _read_counts(
        db: AsyncSession,
    ) -> tuple[int, int, Any | None]:
        from app.models.message import Message

        contact_count = await db.scalar(
            select(func.count(func.distinct(Conversation.id)))
            .join(Message, Message.conversation_id == Conversation.id)
            .where(Conversation.workspace_id == workspace_id)
        ) or 0
        customer_count = await db.scalar(
            select(func.count(func.distinct(Customer.id)))
            .join(Conversation, Conversation.customer_id == Customer.id)
            .join(Message, Message.conversation_id == Conversation.id)
            .where(
                Customer.workspace_id == workspace_id,
                Customer.contact_type == "customer",
                Customer.classification_confidence.is_not(None),
            )
        ) or 0
        voice_projection = await CommercialSpineRepository(db).get_projection(
            workspace_id=workspace_id,
            projection_ref="voice_profile:seller_voice",
        )
        return int(contact_count), int(customer_count), voice_projection

    if session is not None:
        contact_count, customer_count, voice_projection = await _read_counts(session)
    else:
        async with async_session() as db:
            contact_count, customer_count, voice_projection = await _read_counts(db)

    return apply_progress_db_floor(
        progress,
        contact_count=contact_count,
        customer_count=customer_count,
        voice_projection=voice_projection,
    )


async def load_events(workspace_id: int) -> list[dict[str, Any]]:
    r = await get_redis()
    try:
        data = await r.get(f"ingestion:events:{workspace_id}")
    finally:
        await r.aclose()
    if not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


async def replace_events(workspace_id: int, events: list[dict[str, Any]]) -> None:
    r = await get_redis()
    try:
        await r.set(
            f"ingestion:events:{workspace_id}",
            json.dumps(events[-80:], default=str),
            ex=3600,
        )
    finally:
        await r.aclose()


async def append_event(workspace_id: int, event: dict[str, Any]) -> None:
    events = await load_events(workspace_id)
    events.append(event)
    await replace_events(workspace_id, events)


async def notify_progress(progress: dict[str, Any]) -> None:
    from app.api.routes.ws import manager

    await manager.broadcast(progress["workspace_id"], {
        "type": "ingestion_progress",
        "data": progress,
    })


async def notify_event(workspace_id: int, event: dict[str, Any]) -> None:
    from app.api.routes.ws import manager

    await append_event(workspace_id, event)
    await manager.broadcast(workspace_id, {
        "type": "ingestion_event",
        "data": event,
    })


async def set_progress(progress: dict[str, Any], **updates: Any) -> dict[str, Any]:
    progress.update(updates)
    try:
        reconciled = await reconcile_progress_with_db(progress["workspace_id"], progress)
        progress.clear()
        progress.update(reconciled)
    except Exception:
        logger.warning(
            "Failed to reconcile onboarding progress for workspace %s",
            progress.get("workspace_id"),
            exc_info=True,
        )
    await store_progress(progress["workspace_id"], progress)
    try:
        await record_runtime_progress(progress["workspace_id"], progress)
    except Exception:
        logger.warning(
            "Failed to persist onboarding runtime progress for workspace %s",
            progress.get("workspace_id"),
            exc_info=True,
        )
    await notify_progress(progress)
    return progress


async def load_runtime(workspace_id: int) -> OnboardingRuntime | None:
    async with async_session() as db:
        return await db.scalar(
            select(OnboardingRuntime).where(OnboardingRuntime.workspace_id == workspace_id)
        )


async def get_or_create_runtime_row(
    db: AsyncSession,
    workspace_id: int,
) -> OnboardingRuntime:
    runtime = await db.scalar(
        select(OnboardingRuntime).where(OnboardingRuntime.workspace_id == workspace_id)
    )
    if runtime is None:
        runtime = OnboardingRuntime(
            workspace_id=workspace_id,
            max_attempts=DEFAULT_RUNTIME_MAX_ATTEMPTS,
        )
        db.add(runtime)
        await db.flush()
    return runtime


async def mark_runtime_queued_in_session(
    db: AsyncSession,
    workspace_id: int,
    progress: dict[str, Any],
    *,
    now: datetime | None = None,
) -> OnboardingRuntime:
    current_time = now or datetime.now(UTC)
    runtime = await get_or_create_runtime_row(db, workspace_id)
    runtime.state = ONBOARDING_RUNTIME_PENDING
    runtime.phase = str(progress.get("phase") or "starting")
    runtime.percent = int(progress.get("percent") or 0)
    runtime.lease_owner = None
    runtime.leased_until = None
    runtime.next_attempt_at = current_time
    runtime.started_at = None
    runtime.completed_at = None
    runtime.failed_at = None
    runtime.last_error = None
    runtime.progress_snapshot = dict(progress)
    db.add(runtime)
    await db.flush()
    return runtime


async def mark_runtime_queued(
    workspace_id: int,
    progress: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    async with async_session() as db:
        await mark_runtime_queued_in_session(db, workspace_id, progress, now=now)
        await db.commit()


async def mark_runtime_started_in_session(
    db: AsyncSession,
    workspace_id: int,
    progress: dict[str, Any],
    *,
    lease_owner: str = DEFAULT_RUNTIME_LEASE_OWNER,
    lease_seconds: int = DEFAULT_RUNTIME_LEASE_SECONDS,
) -> OnboardingRuntime:
    now = datetime.now(UTC)
    runtime = await get_or_create_runtime_row(db, workspace_id)
    runtime.state = ONBOARDING_RUNTIME_RUNNING
    runtime.phase = str(progress.get("phase") or "starting")
    runtime.percent = int(progress.get("percent") or 0)
    runtime.attempt_count = int(runtime.attempt_count or 0) + 1
    runtime.lease_owner = lease_owner
    runtime.leased_until = now + timedelta(seconds=lease_seconds)
    runtime.started_at = now
    runtime.completed_at = None
    runtime.failed_at = None
    runtime.next_attempt_at = None
    runtime.last_error = None
    runtime.progress_snapshot = dict(progress)
    db.add(runtime)
    await db.flush()
    return runtime


async def record_runtime_progress_in_session(
    db: AsyncSession,
    workspace_id: int,
    progress: dict[str, Any],
) -> OnboardingRuntime | None:
    runtime = await db.scalar(
        select(OnboardingRuntime).where(OnboardingRuntime.workspace_id == workspace_id)
    )
    if runtime is None:
        return None
    runtime.phase = str(progress.get("phase") or runtime.phase or "not_started")
    runtime.percent = int(progress.get("percent") or 0)
    runtime.progress_snapshot = dict(progress)
    if progress.get("completed") and runtime.state != ONBOARDING_RUNTIME_DLQ:
        runtime.state = ONBOARDING_RUNTIME_COMPLETED
        runtime.completed_at = runtime.completed_at or datetime.now(UTC)
        runtime.leased_until = None
        runtime.lease_owner = None
        runtime.next_attempt_at = None
    db.add(runtime)
    await db.flush()
    return runtime


async def record_runtime_progress(workspace_id: int, progress: dict[str, Any]) -> None:
    async with async_session() as db:
        await record_runtime_progress_in_session(db, workspace_id, progress)
        await db.commit()


async def mark_runtime_completed_in_session(
    db: AsyncSession,
    workspace_id: int,
    progress: dict[str, Any],
) -> OnboardingRuntime:
    now = datetime.now(UTC)
    final_progress = dict(progress)
    final_progress["phase"] = "done"
    final_progress["percent"] = 100
    final_progress["completed"] = True
    runtime = await get_or_create_runtime_row(db, workspace_id)
    runtime.state = ONBOARDING_RUNTIME_COMPLETED
    runtime.phase = "done"
    runtime.percent = 100
    runtime.completed_at = now
    runtime.failed_at = None
    runtime.last_error = None
    runtime.leased_until = None
    runtime.lease_owner = None
    runtime.next_attempt_at = None
    runtime.progress_snapshot = final_progress
    db.add(runtime)
    await db.flush()
    return runtime


async def mark_runtime_completed(workspace_id: int, progress: dict[str, Any]) -> None:
    async with async_session() as db:
        await mark_runtime_completed_in_session(db, workspace_id, progress)
        await db.commit()


async def mark_runtime_failed_in_session(
    db: AsyncSession,
    workspace_id: int,
    progress: dict[str, Any],
    *,
    error: str,
) -> OnboardingRuntime:
    now = datetime.now(UTC)
    runtime = await get_or_create_runtime_row(db, workspace_id)
    max_attempts = int(runtime.max_attempts or DEFAULT_RUNTIME_MAX_ATTEMPTS)
    attempts = int(runtime.attempt_count or 0)
    runtime.state = ONBOARDING_RUNTIME_DLQ if attempts >= max_attempts else ONBOARDING_RUNTIME_FAILED
    runtime.phase = str(progress.get("phase") or "error")
    runtime.percent = int(progress.get("percent") or 0)
    runtime.failed_at = now
    runtime.last_error = error
    runtime.progress_snapshot = dict(progress)
    runtime.leased_until = None
    runtime.lease_owner = None
    runtime.next_attempt_at = (
        None
        if runtime.state == ONBOARDING_RUNTIME_DLQ
        else now + timedelta(seconds=DEFAULT_RUNTIME_RETRY_DELAY_SECONDS)
    )
    db.add(runtime)
    await db.flush()
    return runtime


async def mark_runtime_failed(
    workspace_id: int,
    progress: dict[str, Any],
    *,
    error: str,
) -> None:
    async with async_session() as db:
        await mark_runtime_failed_in_session(db, workspace_id, progress, error=error)
        await db.commit()


def _due_onboarding_runtime_clause(now: datetime):
    return or_(
        and_(
            OnboardingRuntime.state == ONBOARDING_RUNTIME_PENDING,
            or_(
                OnboardingRuntime.next_attempt_at.is_(None),
                OnboardingRuntime.next_attempt_at <= now,
            ),
            or_(
                OnboardingRuntime.leased_until.is_(None),
                OnboardingRuntime.leased_until <= now,
            ),
        ),
        and_(
            OnboardingRuntime.state == ONBOARDING_RUNTIME_FAILED,
            OnboardingRuntime.next_attempt_at.is_not(None),
            OnboardingRuntime.next_attempt_at <= now,
            or_(
                OnboardingRuntime.leased_until.is_(None),
                OnboardingRuntime.leased_until <= now,
            ),
        ),
        and_(
            OnboardingRuntime.state == ONBOARDING_RUNTIME_RUNNING,
            OnboardingRuntime.leased_until.is_not(None),
            OnboardingRuntime.leased_until <= now,
        ),
    )


async def claim_due_onboarding_jobs(
    db: AsyncSession,
    *,
    lease_owner: str,
    limit: int = DEFAULT_RUNTIME_BATCH_SIZE,
    lease_seconds: int = DEFAULT_RUNTIME_LEASE_SECONDS,
    now: datetime | None = None,
    workspace_id: int | None = None,
) -> list[OnboardingRuntime]:
    if limit <= 0:
        return []

    current_time = now or datetime.now(UTC)
    stmt = (
        select(OnboardingRuntime)
        .where(
            _due_onboarding_runtime_clause(current_time),
            OnboardingRuntime.attempt_count < OnboardingRuntime.max_attempts,
        )
        .order_by(OnboardingRuntime.next_attempt_at.asc().nullsfirst(), OnboardingRuntime.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if workspace_id is not None:
        stmt = stmt.where(OnboardingRuntime.workspace_id == workspace_id)

    jobs = list((await db.scalars(stmt)).all())
    leased_until = current_time + timedelta(seconds=lease_seconds)
    for job in jobs:
        job.state = ONBOARDING_RUNTIME_RUNNING
        job.phase = job.phase or "starting"
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.lease_owner = lease_owner
        job.leased_until = leased_until
        job.next_attempt_at = None
        job.started_at = current_time
        job.completed_at = None
        job.failed_at = None
        job.last_error = None
        db.add(job)
    await db.flush()
    return jobs


def _progress_snapshot_for_runtime(runtime: OnboardingRuntime) -> dict[str, Any]:
    progress = dict(runtime.progress_snapshot or {})
    if not progress:
        progress = default_ingestion_progress(runtime.workspace_id)
    progress.setdefault("workspace_id", runtime.workspace_id)
    progress["workspace_id"] = runtime.workspace_id
    progress["phase"] = progress.get("phase") or runtime.phase or "starting"
    progress["percent"] = int(progress.get("percent") or runtime.percent or 0)
    progress.setdefault("completed", False)
    progress.setdefault("errors", [])
    return progress


async def run_onboarding_job_for_workspace(
    workspace_id: int,
    *,
    lease_owner: str | None = None,
    sync_factory: Callable[[], ChannelConversationSync] | None = None,
) -> bool:
    owner = lease_owner or make_consumer_name(DEFAULT_RUNTIME_LEASE_OWNER)
    async with async_session() as db:
        jobs = await claim_due_onboarding_jobs(
            db,
            lease_owner=owner,
            limit=1,
            workspace_id=workspace_id,
        )
        if not jobs:
            await db.commit()
            return False
        runtime = jobs[0]
        progress = _progress_snapshot_for_runtime(runtime)
        workspace = await db.get(Workspace, workspace_id)
        await db.commit()

    if workspace is None:
        await mark_runtime_failed(workspace_id, progress, error="workspace_missing")
        return False

    await run_ingestion_bridge(
        workspace,
        initial_progress=progress,
        sync_factory=sync_factory,
    )
    return True


async def run_due_onboarding_once(
    *,
    lease_owner: str | None = None,
    limit: int = DEFAULT_RUNTIME_BATCH_SIZE,
    sync_factory: Callable[[], ChannelConversationSync] | None = None,
) -> int:
    owner = lease_owner or make_consumer_name(DEFAULT_RUNTIME_LEASE_OWNER)
    async with async_session() as db:
        jobs = await claim_due_onboarding_jobs(db, lease_owner=owner, limit=limit)
        claimed = [
            (job.workspace_id, _progress_snapshot_for_runtime(job))
            for job in jobs
        ]
        workspaces = {
            workspace.id: workspace
            for workspace in (
                await db.scalars(
                    select(Workspace).where(Workspace.id.in_([workspace_id for workspace_id, _ in claimed]))
                )
            ).all()
        } if claimed else {}
        await db.commit()

    for workspace_id, progress in claimed:
        workspace = workspaces.get(workspace_id)
        if workspace is None:
            await mark_runtime_failed(workspace_id, progress, error="workspace_missing")
            continue
        await run_ingestion_bridge(
            workspace,
            initial_progress=progress,
            sync_factory=sync_factory,
        )
    return len(claimed)


class OnboardingRuntimeWorker:
    """Supervised worker for due onboarding runtime rows."""

    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        redis: Any | None = None,
        poll_interval_seconds: float = DEFAULT_RUNTIME_POLL_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_RUNTIME_BATCH_SIZE,
        sync_factory: Callable[[], ChannelConversationSync] | None = None,
    ) -> None:
        self._db_factory = db_factory
        self._redis = redis
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = max(1, int(batch_size))
        self._sync_factory = sync_factory
        self._consumer_name = make_consumer_name("onboarding_runtime")
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None
        self._lease = (
            WorkerLease(redis, role="onboarding_runtime", ttl_seconds=30)
            if redis is not None
            else None
        )

    def set_heartbeat_callback(self, callback) -> None:
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
                await self.run_due_once()
                self._beat()
                await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                raise
            except Exception:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                has_lease = False
                logger.exception("onboarding_runtime_worker.tick_failed")
                await asyncio.sleep(min(self._poll_interval_seconds * 2, 10.0))
        if has_lease and self._lease is not None:
            await self._lease.release()

    async def stop(self) -> None:
        self._stopping = True

    async def run_due_once(self) -> int:
        return await run_due_onboarding_once(
            lease_owner=self._consumer_name,
            limit=self._batch_size,
            sync_factory=self._sync_factory,
        )

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()


async def get_progress_response(
    workspace: Workspace,
    *,
    minimal_missing: bool = False,
    include_is_running: bool = False,
) -> dict[str, Any]:
    """Read canonical onboarding progress with DB reconciliation.

    `minimal_missing` preserves the old `/onboarding/progress` response shape
    for lightweight frontend polling. `/onboarding/runtime` owns the full
    progress envelope.
    """
    progress = await load_progress(workspace.id)
    if progress:
        payload = await reconcile_progress_with_db(workspace.id, progress)
        if getattr(workspace, "onboarding_completed", False):
            payload = {
                **payload,
                "phase": "done",
                "percent": 100,
                "completed": True,
            }
        if payload != progress:
            await store_progress(workspace.id, payload)
    elif getattr(workspace, "onboarding_completed", False):
        if minimal_missing:
            payload = {
                "phase": "done",
                "percent": 100,
                "completed": True,
            }
        else:
            payload = {
                **default_ingestion_progress(workspace.id),
                "phase": "done",
                "percent": 100,
                "completed": True,
            }
    elif minimal_missing:
        payload = {
            "phase": "not_started",
            "percent": 0,
            "completed": False,
        }
    else:
        payload = default_ingestion_progress(workspace.id)

    if include_is_running:
        payload = dict(payload)
        payload["is_running"] = (
            not payload.get("completed", False)
            and payload.get("phase") not in ("not_started", "error")
        )
    return payload


async def run_ingestion_bridge(
    workspace: Workspace,
    *,
    initial_progress: dict[str, Any] | None = None,
    sync_factory: Callable[[], ChannelConversationSync] | None = None,
) -> None:
    progress = initial_progress or await load_progress(workspace.id) or default_ingestion_progress(workspace.id)
    await store_progress(workspace.id, progress)
    await notify_progress(progress)

    owned_redis: Any | None = None
    if sync_factory is None:
        owned_redis = await get_redis()
        event_spine = EventSpine(
            owned_redis,
            db_factory=lambda: async_session(),
        )

        def sync_factory() -> ChannelConversationSync:
            return ChannelConversationSync(event_append=event_spine.append)

    try:
        pipeline = OnboardingIngestionPipeline(
            progress_update=set_progress,
            notify_event=notify_event,
            sync_factory=sync_factory,
        )
        await pipeline.run(workspace, progress)
        await notify_event(workspace.id, {"kind": "source_learning_start"})
        source_learning = await run_source_learning_bridge(
            workspace.id,
            correlation_id=f"onboarding:{workspace.id}:source_learning",
        )
        source_learning_payload = source_learning.model_dump(mode="json")
        source_errors = list(progress.get("errors") or [])
        source_degraded = bool(
            source_learning.retrying_count or source_learning.failed_count
        )
        if source_degraded and "source_learning_degraded" not in source_errors:
            source_errors.append("source_learning_degraded")
        await notify_event(
            workspace.id,
            {
                "kind": "source_learning_done",
                "processed": source_learning.processed_count,
                "review_ready": source_learning.review_ready_count,
                "retrying": source_learning.retrying_count,
                "failed": source_learning.failed_count,
            },
        )
        await set_progress(
            progress,
            phase=(
                "review_learnings"
                if source_learning.review_ready_count
                else progress.get("phase", "awaiting_channels")
            ),
            percent=max(int(progress.get("percent") or 0), 85),
            source_learning=source_learning_payload,
            ai_learning_degraded=bool(progress.get("ai_learning_degraded"))
            or source_degraded,
            ai_learning_error=progress.get("ai_learning_error")
            or ("source_learning_degraded" if source_degraded else None),
            errors=source_errors,
        )
        try:
            await mark_runtime_completed(workspace.id, progress)
        except Exception:
            logger.warning(
                "Failed to mark onboarding runtime completed for workspace %d",
                workspace.id,
                exc_info=True,
            )
    except Exception as exc:
        logger.exception("Onboarding ingestion bridge failed for workspace %d", workspace.id)
        errors = list(progress.get("errors", []))
        errors.append(str(exc))
        await set_progress(
            progress,
            phase="error",
            completed=True,
            errors=errors,
        )
        try:
            await mark_runtime_failed(workspace.id, progress, error=str(exc))
        except Exception:
            logger.warning(
                "Failed to mark onboarding runtime failed for workspace %d",
                workspace.id,
                exc_info=True,
            )
    finally:
        if owned_redis is not None:
            await owned_redis.aclose()


async def run_source_learning_bridge(
    workspace_id: int,
    *,
    correlation_id: str,
):
    concurrency = max(1, min(int(get_settings().onboarding_source_learning_concurrency), 12))
    if concurrency <= 1:
        async with async_session() as db:
            repository = CommercialSpineRepository(db)
            result = await OnboardingSourceLearningRuntimeService(
                repository=repository,
            ).process_workspace_sources(
                workspace_id=workspace_id,
                correlation_id=correlation_id,
            )
            await db.commit()
            return result

    source_refs = await _source_refs_for_learning(workspace_id=workspace_id, limit=10)
    if not source_refs:
        return OnboardingSourceRuntimeResult(
            processed_count=0,
            review_ready_count=0,
            learned_count=0,
            retrying_count=0,
            failed_count=0,
            skipped_count=0,
            items=[],
        )

    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(source_ref: str) -> OnboardingSourceRuntimeResult:
        async with semaphore, async_session() as db:
            repository = CommercialSpineRepository(db)
            result = await OnboardingSourceLearningRuntimeService(
                repository=repository,
            ).process_workspace_sources(
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                limit=1,
                source_refs={source_ref},
            )
            await db.commit()
            return result

    results = await asyncio.gather(*(run_one(source_ref) for source_ref in source_refs))
    return _merge_source_learning_results(results)


async def _source_refs_for_learning(*, workspace_id: int, limit: int) -> list[str]:
    async with async_session() as db:
        repository = CommercialSpineRepository(db)
        source_facts = await repository.list_facts(
            workspace_id=workspace_id,
            fact_type="business_source_fact",
            statuses=("active", "degraded"),
            limit=250,
        )
    source_refs: list[str] = []
    for fact in source_facts:
        entity_ref = str(getattr(fact, "entity_ref", "") or "")
        if not entity_ref.startswith("workspace:source:"):
            continue
        source_ref = ""
        for ref in list(getattr(fact, "source_refs", []) or []):
            ref_text = str(ref)
            if ref_text.startswith("onboarding:source:") or ref_text.startswith("brain:source:"):
                source_ref = ref_text
                break
        if not source_ref:
            source_ref = entity_ref.removeprefix("workspace:source:")
        if source_ref and source_ref not in source_refs:
            source_refs.append(source_ref)
        if len(source_refs) >= limit:
            break
    return source_refs


def _merge_source_learning_results(
    results: list[OnboardingSourceRuntimeResult],
) -> OnboardingSourceRuntimeResult:
    items: list[OnboardingSourceRuntimeItem] = []
    for result in results:
        items.extend(result.items)
    return OnboardingSourceRuntimeResult(
        processed_count=sum(result.processed_count for result in results),
        review_ready_count=sum(result.review_ready_count for result in results),
        learned_count=sum(result.learned_count for result in results),
        retrying_count=sum(result.retrying_count for result in results),
        failed_count=sum(result.failed_count for result in results),
        skipped_count=sum(result.skipped_count for result in results),
        items=items,
    )


def start_ingestion_task(workspace: Workspace) -> None:
    spawn_guarded_task(
        run_onboarding_job_for_workspace(workspace.id),
        logger=logger,
        name=f"telegram-ingestion:{workspace.id}",
        registry=_bg_tasks,
    )


async def start_ingestion(workspace: Workspace) -> dict[str, Any]:
    """Start or resume onboarding ingestion without duplicating work."""
    existing_progress = await load_progress(workspace.id)
    if existing_progress and existing_progress.get("completed"):
        return {
            "status": "completed",
            "workspace_id": workspace.id,
            "progress": existing_progress,
        }
    runtime = await load_runtime(workspace.id)
    if runtime and runtime.state == ONBOARDING_RUNTIME_COMPLETED:
        return {
            "status": "completed",
            "workspace_id": workspace.id,
            "progress": _progress_snapshot_for_runtime(runtime),
        }
    if ingestion_progress_is_active(existing_progress) and onboarding_runtime_is_active(runtime):
        return {
            "status": "already_running",
            "workspace_id": workspace.id,
            "progress": _progress_snapshot_for_runtime(runtime),
        }
    if onboarding_runtime_is_active(runtime):
        return {
            "status": "already_running",
            "workspace_id": workspace.id,
            "progress": _progress_snapshot_for_runtime(runtime),
        }
    if ingestion_progress_is_active(existing_progress):
        logger.warning(
            "onboarding stale active progress without active runtime; restarting workspace=%d phase=%s",
            workspace.id,
            existing_progress.get("phase"),
        )

    if not await try_acquire_ingestion_start_lock(workspace.id):
        return {
            "status": "already_running",
            "workspace_id": workspace.id,
            "progress": existing_progress or default_ingestion_progress(workspace.id),
        }

    try:
        existing_progress = await load_progress(workspace.id)
        if existing_progress and existing_progress.get("completed"):
            return {
                "status": "completed",
                "workspace_id": workspace.id,
                "progress": existing_progress,
            }
        runtime = await load_runtime(workspace.id)
        if runtime and runtime.state == ONBOARDING_RUNTIME_COMPLETED:
            return {
                "status": "completed",
                "workspace_id": workspace.id,
                "progress": _progress_snapshot_for_runtime(runtime),
            }
        if ingestion_progress_is_active(existing_progress) and onboarding_runtime_is_active(runtime):
            return {
                "status": "already_running",
                "workspace_id": workspace.id,
                "progress": _progress_snapshot_for_runtime(runtime),
            }
        if onboarding_runtime_is_active(runtime):
            return {
                "status": "already_running",
                "workspace_id": workspace.id,
                "progress": _progress_snapshot_for_runtime(runtime),
            }
        if ingestion_progress_is_active(existing_progress):
            logger.warning(
                "onboarding locked restart from stale active progress workspace=%d phase=%s",
                workspace.id,
                existing_progress.get("phase"),
            )

        await replace_events(workspace.id, [])
        progress = default_ingestion_progress(workspace.id)
        progress["phase"] = "starting"
        progress["percent"] = 1
        await store_progress(workspace.id, progress)
        await mark_runtime_queued(workspace.id, progress)
        start_ingestion_task(workspace)
        return {
            "status": "started",
            "workspace_id": workspace.id,
            "progress": progress,
        }
    finally:
        await release_ingestion_start_lock(workspace.id)
