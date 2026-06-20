"""Provider-neutral owner-bot card for the CRM sync plane.

Same ``owner_notification`` projection shape Instagram + AgentBusinessActions
write (flushed by OwnerControlBotWorker), generalized off the channel:
``entity_ref`` is ``crm:{workspace_id}``. Idempotent on a hashed key so a
recurring failure (a degraded connection, an auth-dead refresh) surfaces to the
owner exactly once per key — never a notification storm.
"""
from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.commercial_spine.contracts import BusinessBrainProjection
from app.modules.commercial_spine.repository import CommercialSpineRepository


async def queue_crm_owner_notification(
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
    """Queue an owner-bot card; idempotent on ``idempotency_key``."""
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
            entity_ref=f"crm:{workspace_id}",
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
