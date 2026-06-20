"""PromoterDripWorker — supervised warm-drip reconciler. Its job is RESTRAINT.

CrmSyncWorker-shaped lifecycle (db_factory, set_heartbeat_callback, start/stop,
public run_once). Slice B drains WARM targets only: re-opens an existing seller
dialog via DeliveryService (sidecar send, idempotent), records the opener as a
seller message, writes a contact note back to the CRM, and queues the daily
digest. Cold resolution (phone->peer) and the PeerFlood campaign pause are
Slice C.

Safety lines:
- batch of 1 per tick, own session, no reply-path locks (never starves replies);
- working hours + campaign-level jitter gate (durable: MAX(sent_at));
- never DMs an opted-out customer or an active conversation;
- crash-window: state='sending' commits BEFORE delivery; the sidecar dedupes by
  idempotency_key and the seller Message placeholder is reused by
  client_message_uuid — a crash mid-send never double-sends or double-bubbles.
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection
from app.models.customer import Customer
from app.models.message import Message
from app.models.outreach import OutreachCampaign, OutreachTarget
from app.modules.bi_promoter.contracts import effective_caps, within_working_hours
from app.modules.bi_promoter.personalizer import personalize_opener
from app.modules.conversation_core.service import create_seller_placeholder_message
from app.modules.crm_connector.factory import provider_for
from app.modules.crm_connector.owner_cards import queue_crm_owner_notification
from app.modules.crm_connector.provider import CrmProvider

logger = get_logger("promoter.drip_worker")

_TICK_INTERVAL_SECONDS = 60.0
_BATCH_PER_TICK = 1          # never a burst — restraint is the product
_MAX_ATTEMPTS = 6
_BACKOFF_BASE_SECONDS = 120
_BACKOFF_CAP_SECONDS = 6 * 3600


class PromoterDripWorker:
    def __init__(
        self,
        *,
        db_factory: Callable[[], AsyncSession] | None,
        delivery: Any,
        provider_factory: Callable[[str], CrmProvider] | None = None,
        personalize: Callable[..., Any] | None = None,
        interval_seconds: float = _TICK_INTERVAL_SECONDS,
        rng: random.Random | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._db_factory = db_factory
        self._delivery = delivery
        self._provider_factory = provider_factory or provider_for
        self._personalize = personalize or personalize_opener
        self._interval = interval_seconds
        self._rng = rng or random.Random()
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._stopping = False
        self._beat: Callable[[], None] = lambda: None

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._beat = callback or (lambda: None)

    async def start(self) -> None:
        assert self._db_factory is not None, "db_factory required to run the loop"
        self._stopping = False
        while not self._stopping:
            try:
                async with self._db_factory() as session:
                    processed = await self.run_once(session)
                self._beat()
                if processed:
                    logger.info("promoter_drip_worker.processed", extra={"count": processed})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("promoter_drip_worker.tick_failed", exc_info=exc)
            slept = 0.0
            while not self._stopping and slept < self._interval:
                await asyncio.sleep(min(5.0, self._interval - slept))
                slept += 5.0
                self._beat()

    async def stop(self) -> None:
        self._stopping = True

    # ------------------------------------------------------------------ #
    async def run_once(self, session: AsyncSession) -> int:
        """Queue due digests, then drain at most _BATCH_PER_TICK warm targets."""
        now = self._now()
        await self._queue_daily_digests(session, now)
        rows = (
            await session.execute(
                select(OutreachTarget, OutreachCampaign)
                .join(OutreachCampaign, OutreachTarget.campaign_id == OutreachCampaign.id)
                .where(
                    OutreachCampaign.status == "running",
                    OutreachTarget.tier == "warm",
                    OutreachTarget.state.in_(("pending", "sending")),
                    OutreachTarget.next_attempt_at <= now,
                )
                .order_by(OutreachTarget.next_attempt_at.asc())
                .limit(_BATCH_PER_TICK)
            )
        ).all()
        processed = 0
        for target, campaign in rows:
            self._beat()
            caps = effective_caps(campaign.caps)
            if not within_working_hours(caps, now):
                break  # outside hours: hold the whole drip quietly (level-triggered)
            if not await self._jitter_gate_open(session, campaign, caps, now):
                break  # pacing gap not yet elapsed — try again next tick
            try:
                await self._send_one(session, target, campaign, caps, now)
            except Exception as exc:
                await self._handle_failure(session, target, exc)
            processed += 1
        return processed

    # ------------------------------------------------------------------ #
    async def _send_one(
        self,
        session: AsyncSession,
        target: OutreachTarget,
        campaign: OutreachCampaign,
        caps: dict,
        now: datetime,
    ) -> None:
        customer, conversation = await self._warm_destination(session, target)
        if customer is None or conversation is None or conversation.telegram_chat_id is None:
            # not actually warm-sendable: demote; cold resolution is Slice C.
            # Reset to pending so a reclaimed 'sending' target can't wedge as
            # cold+sending (invisible to the warm scan and to Slice C's cold scan).
            target.tier = "cold"
            target.state = "pending"
            await session.commit()
            return
        if customer.opted_out:
            target.state = "skipped"
            target.last_error = "opted_out"
            await session.commit()
            return
        if target.state == "pending":
            # The active-conversation gate guards ENROLLMENT only. A reclaimed
            # 'sending' target already passed it — and our own placeholder bumped
            # conversation.last_message_at, so re-checking on retry would
            # wrongly skip every crashed send forever.
            window = timedelta(hours=float(caps.get("active_window_h") or 72))
            last_at = conversation.last_message_at
            if last_at is not None and (now - last_at) < window:
                # the seller (or the customer) is mid-conversation — never talk over it.
                target.state = "skipped"
                target.last_error = "active_conversation"
                await session.commit()
                return

        # Durable claim BEFORE any visible side effect (crash window).
        target.state = "sending"
        target.customer_id = customer.id
        target.conversation_id = conversation.id
        await session.commit()

        message = await self._find_placeholder(session, conversation.id, target.idempotency_key)
        if message is None:
            crm_context = await self._crm_context(session, campaign, target)
            opener = await self._personalize(
                workspace_id=target.workspace_id,
                base_message=campaign.base_message,
                contact_name=target.display_name,
                crm_context=crm_context,
            )
            message = await create_seller_placeholder_message(
                session,
                conversation=conversation,
                content=opener,
                client_message_uuid=target.idempotency_key,
            )

        result = await self._delivery.deliver_message(
            conversation.id,
            message.content,
            db=session,
            workspace_id=target.workspace_id,
            client_idempotency_key=target.idempotency_key,
            message_id=message.id,
        )
        if result.success:
            message.delivery_state = "confirmed"
            message.external_message_id = result.external_message_id
            target.state = "sent"
            target.sent_at = self._now()
            target.last_error = None
            await session.commit()
            await self._note_contact(session, campaign, target)
            return
        if result.retry_after_seconds is not None:
            # FloodWait-style throttle: back THIS target off; NOT an attempt.
            # Stay 'sending' (NOT 'pending'): the placeholder we just created
            # bumped conversation.last_message_at, so a 'pending' retry would
            # self-skip on the active-conversation gate. 'sending' is reclaimed by
            # the scan (next_attempt_at gates timing) and bypasses that gate.
            lo = float((caps.get("jitter_s") or [180, 600])[0])
            target.state = "sending"
            target.next_attempt_at = self._now() + timedelta(
                seconds=result.retry_after_seconds + lo
            )
            target.last_error = f"rate_limited:{result.retry_after_seconds:.0f}s"
            await session.commit()
            return
        raise RuntimeError(f"delivery_{result.state or 'failed'}:{result.error or ''}")

    # ------------------------------------------------------------------ #
    async def _warm_destination(
        self, session: AsyncSession, target: OutreachTarget
    ) -> tuple[Customer | None, Conversation | None]:
        row = (
            await session.execute(
                select(Customer, Conversation)
                .join(Conversation, Conversation.customer_id == Customer.id)
                .where(
                    Customer.workspace_id == target.workspace_id,
                    Conversation.workspace_id == target.workspace_id,
                    Customer.phone_number == target.phone,
                    Conversation.telegram_chat_id.is_not(None),
                )
                .order_by(Conversation.last_message_at.desc().nulls_last())
                .limit(1)
            )
        ).first()
        if row is None:
            return None, None
        return row[0], row[1]

    async def _find_placeholder(
        self, session: AsyncSession, conversation_id: int, idempotency_key: str
    ) -> Message | None:
        return (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.client_message_uuid == idempotency_key,
                )
                .limit(1)
            )
        ).scalars().first()

    async def _crm_context(
        self, session: AsyncSession, campaign: OutreachCampaign, target: OutreachTarget
    ) -> str:
        """Best-effort: the contact's last CRM note grounds the opener."""
        try:
            conn = await session.get(CrmConnection, campaign.connection_id)
            if conn is None or conn.status != "active":
                return ""
            provider = self._provider_factory(conn.provider)
            note = await provider.fetch_last_contact_note(
                conn, contact_id=target.provider_contact_id
            )
            return note or ""
        except Exception:
            logger.warning("promoter.drip.crm_context_failed target=%s", target.id)
            return ""

    async def _note_contact(
        self, session: AsyncSession, campaign: OutreachCampaign, target: OutreachTarget
    ) -> None:
        """Best-effort touch note on the amoCRM contact; never fails a sent target."""
        try:
            conn = await session.get(CrmConnection, campaign.connection_id)
            if conn is None or conn.status != "active":
                return
            provider = self._provider_factory(conn.provider)
            await provider.add_contact_note(
                conn,
                contact_id=target.provider_contact_id,
                text=f"OQIM outreach ({campaign.name}): sent",
            )
        except Exception:
            logger.warning("promoter.drip.contact_note_failed target=%s", target.id)

    async def _jitter_gate_open(
        self, session: AsyncSession, campaign: OutreachCampaign, caps: dict, now: datetime
    ) -> bool:
        """Durable campaign-level pacing: gap since MAX(sent_at) must exceed a
        random draw from jitter_s. Restart-safe; re-drawn each tick."""
        last_sent = (
            await session.execute(
                select(func.max(OutreachTarget.sent_at)).where(
                    OutreachTarget.campaign_id == campaign.id
                )
            )
        ).scalar_one_or_none()
        if last_sent is None:
            return True
        lo, hi = (caps.get("jitter_s") or [180, 600])[:2]
        gap = self._rng.uniform(float(lo), float(hi))
        return (now - last_sent).total_seconds() >= gap

    async def _handle_failure(
        self, session: AsyncSession, target: OutreachTarget, exc: Exception
    ) -> None:
        target.attempts = (target.attempts or 0) + 1
        delay = min(
            _BACKOFF_BASE_SECONDS * (2 ** (target.attempts - 1)), _BACKOFF_CAP_SECONDS
        )
        target.next_attempt_at = self._now() + timedelta(seconds=delay)
        target.last_error = f"{type(exc).__name__}: {exc}"[:500]
        if target.attempts >= _MAX_ATTEMPTS:
            target.state = "failed"  # the daily digest surfaces failures
        logger.warning(
            "promoter.drip.target_failed target=%s attempts=%s error=%s",
            target.id, target.attempts, type(exc).__name__,
        )
        await session.commit()

    # ------------------------------------------------------------------ #
    async def _queue_daily_digests(self, session: AsyncSession, now: datetime) -> None:
        """One digest card per running campaign per local day (dedup is free:
        queue_crm_owner_notification is idempotent by key). Flips a fully
        drained campaign to completed."""
        campaigns = (
            await session.execute(
                select(OutreachCampaign).where(OutreachCampaign.status == "running")
            )
        ).scalars().all()
        for campaign in campaigns:
            caps = effective_caps(campaign.caps)
            if not within_working_hours(caps, now):
                continue  # the card should arrive in the morning, not at midnight
            counts = dict(
                (
                    await session.execute(
                        select(OutreachTarget.state, func.count())
                        .where(OutreachTarget.campaign_id == campaign.id)
                        .group_by(OutreachTarget.state)
                    )
                ).all()
            )
            if not counts:
                continue
            local_day = now.astimezone(ZoneInfo(str(caps["tz"]))).strftime("%Y%m%d")
            summary = (
                f"{campaign.name}: {counts.get('sent', 0)} yuborildi · "
                f"{counts.get('replied', 0)} javob · {counts.get('pending', 0)} navbatda · "
                f"{counts.get('skipped', 0)} o'tkazildi · {counts.get('failed', 0)} xato"
            )
            try:
                await queue_crm_owner_notification(
                    session,
                    workspace_id=campaign.workspace_id,
                    title="Promouter kampaniya hisoboti",
                    summary=summary,
                    recommended_action="Pauza: /campaign pause · Holat: /campaign",
                    idempotency_key=f"promoter_digest:{campaign.id}:{local_day}",
                )
            except Exception:
                logger.warning("promoter.drip.digest_failed campaign=%s", campaign.id)
            if counts.get("pending", 0) == 0 and counts.get("sending", 0) == 0:
                campaign.status = "completed"
        # Always commit: queue_crm_owner_notification only flushes its projection
        # into the open transaction — without a commit the worker session would
        # roll it back on close when no send happens this tick.
        await session.commit()
