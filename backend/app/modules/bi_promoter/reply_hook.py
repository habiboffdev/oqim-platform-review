"""Inbound-reply hook (persist consumer): a customer reply retires the matching
outreach targets to ``replied`` — promoter steps aside, the seller loop owns the
conversation from here. The new amoCRM lead is opened by the shipped
``ensure_lead_link`` hook at the same choke point; nothing CRM happens here.
"""
from __future__ import annotations

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.outreach import OutreachTarget


async def mark_outreach_replied(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation_id: int,
    phone: str | None,
) -> int:
    """Flip the person's outreach targets to replied. Matches by the sent
    conversation OR by phone (retires queued openers in other campaigns too).
    This is a SAFETY retire: it covers every state except ``replied`` and
    ``skipped`` (opted-out / deliberately-skipped), so it never depends on the
    drip worker's exact scan set. ``sending`` cancels a claimed-but-crashed
    opener; ``failed`` (delivery gave up) is not "done with this person" — if
    they reply anyway, that is a ``replied`` outcome. The seller loop owns the
    conversation from here."""
    conditions = [OutreachTarget.conversation_id == conversation_id]
    cleaned = (phone or "").strip()
    if cleaned:
        conditions.append(OutreachTarget.phone == cleaned)
    result = await session.execute(
        update(OutreachTarget)
        .where(
            OutreachTarget.workspace_id == workspace_id,
            OutreachTarget.state.in_(("pending", "sending", "sent", "failed")),
            or_(*conditions),
        )
        .values(state="replied", reply_at=utc_now(), conversation_id=conversation_id)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)
