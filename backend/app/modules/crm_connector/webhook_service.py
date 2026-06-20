"""Apply inbound amoCRM webhook events to crm_lead_links — DB-only, low-trust.

The webhook payload is observed STATE, never a command: the only effects are
recording the observed stage and latching the permanent human-touch flag. It can
never advance OQIM's own record or trigger a send."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.conversation import Conversation
from app.models.crm_connection import CrmLeadLink
from app.modules.crm_connector.contracts import CrmWebhookBatch


def _is_human(author_id: int | None) -> bool:
    """amoCRM stamps OQIM's own API/integration writes with author 0; a real human
    carries a positive user id. Fail-open: an unknown author (None) is treated as
    non-human so an OQIM echo never self-latches.

    Assumes amoCRM attributes integration writes to user id 0 (true for the pilot
    account). An account that attributes OQIM's writes to a DEDICATED bot user
    (id > 0) would re-introduce the self-latch — that case needs the connection's
    integration user id stored and excluded here (deferred; see the design doc §7)."""
    return author_id is not None and author_id > 0


class CrmWebhookService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def apply(self, *, connection_id: int, batch: CrmWebhookBatch) -> int:
        """Apply each event to its matching lead link. Returns the count applied.
        Idempotent + monotonic: re-delivery re-asserts the same observed stage and
        the human latch only ever moves oqim -> human."""
        applied = 0
        for event in batch.events:
            if event.kind == "update_contact":
                applied += await self._apply_contact_event(connection_id, event)
                continue
            link = (
                await self._session.execute(
                    select(CrmLeadLink)
                    .where(
                        CrmLeadLink.connection_id == connection_id,
                        CrmLeadLink.provider_lead_id == event.lead_id,
                    )
                    .limit(1)
                )
            ).scalars().first()
            if link is None:
                continue  # a lead OQIM does not track
            if event.kind == "status_lead" and event.status_id:
                link.last_observed_stage_id = event.status_id
                if event.status_id != link.last_synced_stage_id:
                    link.stage_authority = "human"
            elif event.kind in ("responsible_lead", "update_lead") and _is_human(event.author_id):
                # A human reassigned the lead or edited its Sum/fields. OQIM's own
                # API writes (author 0) echo back as these events too — never latch
                # on those. note_lead is no longer a latch signal at all (a note is
                # too weak a takeover signal, and OQIM's first-contact note echoes
                # back as note_lead — the original self-latch).
                link.stage_authority = "human"
                # S4 DNC inbound: a human edit may be a do-not-contact toggle — re-arm
                # so the worker (which does HTTP) inspects the mapped DNC field. DB-only
                # here; the handler never makes a network call.
                link.sync_state = "pending"
                link.next_attempt_at = utc_now()
            if event.value is not None:
                await self._capture_value(link, event.value)
            applied += 1
        await self._session.flush()
        return applied

    async def _apply_contact_event(self, connection_id: int, event) -> int:
        """A human edit to a CONTACT (e.g. the do-not-contact checkbox, S4b). Re-arm
        every active link for that contact + queue a dnc_recheck marker so the worker
        inspects the mapped DNC field. No latch — a contact edit is not a lead-stage
        takeover. OQIM's own contact writes (author 0, e.g. the DNC write-back) are
        ignored, so this never self-triggers."""
        if not _is_human(event.author_id):
            return 0
        links = (
            await self._session.execute(
                select(CrmLeadLink).where(
                    CrmLeadLink.connection_id == connection_id,
                    CrmLeadLink.provider_contact_id == event.lead_id,
                )
            )
        ).scalars().all()
        for link in links:
            ops = list(link.pending_field_ops or [])
            if not any(o.get("kind") == "dnc_recheck" for o in ops):
                ops.append({"kind": "dnc_recheck", "entity": "contact"})
            link.pending_field_ops = ops
            link.sync_state = "pending"
            link.next_attempt_at = utc_now()
        return len(links)

    async def _capture_value(self, link: CrmLeadLink, value: int) -> None:
        """Record a human-set deal price ONLY when OQIM has none yet. Loop-safe: a
        non-null deal_value is never overwritten by an echo, and setting
        synced_value prevents a redundant worker re-push."""
        if value <= 0:
            return  # 0/negative is "no price" — never pin deal_value to it
        conv = await self._session.get(Conversation, link.conversation_id)
        if conv is None or conv.deal_value is not None:
            return
        amount = Decimal(value)
        conv.deal_value = amount
        link.synced_value = amount
