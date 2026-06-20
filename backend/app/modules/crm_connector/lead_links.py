"""Shared CRM lead-link helpers (#421 S2 dedup).

The active-link lookup and the reconciliation re-arm were copied between the CRM
sync service and the promoter opt-out hook. They live here once so a change to
"what counts as the active link" or "how we re-arm a link" happens in one place.
NO new behavior — extracted verbatim from those two consumers.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.modules.crm_connector.stage_map import resolve_pipeline_view


async def active_lead_link(
    session: AsyncSession, *, workspace_id: int, conversation_id: int
) -> CrmLeadLink | None:
    """The conversation's single CRM lead link whose connection is active.

    At most one exists per conversation (unique constraint); ``None`` when there
    is no link or the connection is not active."""
    return (
        await session.execute(
            select(CrmLeadLink)
            .join(CrmConnection, CrmLeadLink.connection_id == CrmConnection.id)
            .where(
                CrmLeadLink.workspace_id == workspace_id,
                CrmLeadLink.conversation_id == conversation_id,
                CrmConnection.status == "active",
            )
            .limit(1)
        )
    ).scalars().first()


def rearm_lead_link(link: CrmLeadLink) -> None:
    """Re-arm a lead link for reconciliation (also recovers a degraded link):
    back to ``pending``, attempts cleared, scheduled immediately. Mutates in
    place; the caller owns the flush/commit."""
    link.sync_state = "pending"
    link.attempts = 0
    link.next_attempt_at = utc_now()


def crm_stage_label(
    pipeline_config: dict | None, stage_id: str | None, *, pipeline_id: str | None = None
) -> str | None:
    """Human stage name for a stage id, from the lead's pipeline snapshot (resolved
    across the flat<->nested config shapes via the read shim).

    Returns ``None`` when the id is unknown/missing — callers omit the label
    rather than show a raw id."""
    if not stage_id:
        return None
    view = resolve_pipeline_view(pipeline_config, pipeline_id)
    for status in view["snapshot_statuses"]:
        if str(status.get("stage_id")) == str(stage_id):
            name = str(status.get("name") or "").strip()
            return name or None
    return None
