"""Promoter opt-out latch — DB-only, called from the records pass.

When committed turn facts carry ``opted_out``: person-level truth is set on
``Customer.opted_out`` (permanent, all campaigns), the person's non-terminal
outreach targets are retired, and a CRM note rides the existing lead-link
``pending_notes`` drain (the CrmSyncWorker does the HTTP; this hook never does).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.customer import Customer
from app.models.outreach import OutreachTarget
from app.modules.crm_connector.lead_links import active_lead_link, rearm_lead_link

logger = get_logger("promoter.fact_hooks")


async def apply_promoter_turn_facts(
    session: AsyncSession,
    result: Any,
    *,
    workspace_id: int,
    conversation_id: int,
    customer_id: int,
) -> None:
    """Opt-out latch, called from ``run_records_pass`` beside the CRM stage advance
    in the same fresh post-commit session. Independently non-fatal — a crash here
    must never abort that session's CRM writes. Cheap no-op unless the turn's facts
    carry ``opted_out``."""
    if result is None:
        return
    facts = (getattr(result, "state", None) or {}).get("facts", {}) or {}
    if facts.get("opted_out") is not True:
        return
    try:
        customer = await session.get(Customer, customer_id)
        if customer is None or customer.workspace_id != workspace_id:
            return
        first_time = not bool(customer.opted_out)
        customer.opted_out = True

        # Retire the person's queued/unanswered outreach everywhere (all campaigns).
        conditions = [OutreachTarget.conversation_id == conversation_id]
        phone = (customer.phone_number or "").strip()
        if phone:
            conditions.append(OutreachTarget.phone == phone)
        await session.execute(
            update(OutreachTarget)
            .where(
                OutreachTarget.workspace_id == workspace_id,
                OutreachTarget.state.in_(("pending", "sending", "sent")),
                or_(*conditions),
            )
            .values(state="skipped", last_error="opted_out")
            .execution_options(synchronize_session=False)
        )

        if first_time:
            link = await active_lead_link(
                session, workspace_id=workspace_id, conversation_id=conversation_id
            )
            if link is not None:
                link.pending_notes = [
                    *link.pending_notes,
                    {"key": f"{conversation_id}:opted_out",
                     "text": "Customer opted out of OQIM outreach (permanent)"},
                ]
                rearm_lead_link(link)
        await session.flush()
        logger.info(
            "promoter.opted_out workspace=%s customer=%s conversation=%s",
            workspace_id, customer_id, conversation_id,
        )
    except Exception:
        logger.exception("promoter opt-out hook failed (non-fatal)")
