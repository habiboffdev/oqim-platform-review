"""Instagram messaging policy: 24h reply window + owner surfacing.

Meta rule: a business may send free-form messages only within 24h of the
customer's last inbound message. A closed window must surface honestly
(owner card via the #413 owner_notification projection) -- never drop silently.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message, SenderType
from app.modules.commercial_spine.contracts import BusinessBrainProjection
from app.modules.commercial_spine.repository import CommercialSpineRepository

INSTAGRAM_WINDOW_SECONDS = 24 * 60 * 60


async def instagram_window_is_open(db: AsyncSession, conversation_id: int) -> bool:
    # Window measured from PLATFORM time (telegram_timestamp carries the
    # channel-native send time for every channel), falling back to ingest
    # time: Meta redelivers webhooks hours after outages, so created_at can
    # be much later than when the customer actually wrote.
    last_inbound_at = await db.scalar(
        select(
            func.max(func.coalesce(Message.telegram_timestamp, Message.created_at))
        ).where(
            Message.conversation_id == conversation_id,
            Message.sender_type == SenderType.CUSTOMER.value,
        )
    )
    if last_inbound_at is None:
        return False
    if last_inbound_at.tzinfo is None:
        last_inbound_at = last_inbound_at.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - last_inbound_at).total_seconds()
    return age < INSTAGRAM_WINDOW_SECONDS


async def queue_instagram_owner_notification(
    db: AsyncSession,
    *,
    workspace_id: int,
    title: str,
    summary: str,
    recommended_action: str,
    idempotency_key: str,
    conversation_id: int = 0,
    customer_label: str | None = None,
) -> str:
    """Queue an owner-bot card through the owner_notification projection
    (same shape AgentBusinessActionsService.notify_owner writes; flushed by
    OwnerControlBotWorker)."""
    notification_ref = (
        f"owner_notification:{workspace_id}:"
        f"{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()[:24]}"
    )
    repository = CommercialSpineRepository(db)
    existing = await repository.get_projection(
        workspace_id=workspace_id, projection_ref=notification_ref
    )
    if existing is not None:
        return notification_ref
    await repository.upsert_projection(
        BusinessBrainProjection(
            projection_ref=notification_ref,
            workspace_id=workspace_id,
            projection_type="owner_notification",
            entity_ref=f"instagram:{workspace_id}",
            state={
                "status": "queued",
                "channel": "owner_bot",
                "idempotency_key": idempotency_key,
                "bot_payload": {
                    "workspace_id": workspace_id,
                    "agent_id": 0,
                    "agent_session_id": 0,
                    "customer_id": 0,
                    "conversation_id": conversation_id,
                    "task_ref": None,
                    "order_ref": None,
                    "title": title,
                    "summary": summary,
                    "recommended_action": recommended_action,
                    "selected_item_refs": [],
                    "shown_price_refs": [],
                    "missing_authority": [],
                    "source_refs": [],
                    "customer_label": customer_label,
                    "chat_summary": None,
                    "hermes_run_id": None,
                },
            },
            source_refs=[f"workspace:{workspace_id}"],
        )
    )
    return notification_ref
