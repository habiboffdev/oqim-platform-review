"""Reconcile 'unknown' deliveries against the outbound echo Telegram streams back.

A timed-out send leaves a DeliveryRuntime in `unknown` (the message id was never
returned). Telegram echoes every sent message back through the sidecar, recorded
as an outbound (seller) Message. If that echo exists, the send DID happen ->
transition to `reconciled` ("sent"). With no echo we stay honestly `unknown` — a
missing echo does not prove failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.delivery_runtime import DeliveryRuntime
from app.models.message import Message, SenderType
from app.services.delivery_runtime import (
    DELIVERY_RECONCILED,
    DELIVERY_UNKNOWN,
    record_delivery_state,
)

_ECHO_SKEW_SECONDS = 60


@dataclass(frozen=True)
class ReconcileReport:
    scanned: int
    reconciled: int
    still_unknown: int


class DeliveryReconciler:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reconcile(self, *, workspace_id: int) -> ReconcileReport:
        rows = list(
            (
                await self.session.scalars(
                    select(DeliveryRuntime).where(
                        DeliveryRuntime.workspace_id == workspace_id,
                        DeliveryRuntime.state == DELIVERY_UNKNOWN,
                    )
                )
            ).all()
        )
        reconciled = 0
        for row in rows:
            text = await self._sent_text(row)
            if not text:
                continue
            echo = await self._find_echo(row, text)
            if echo is None:
                continue
            await record_delivery_state(
                self.session,
                workspace_id=workspace_id,
                conversation_id=row.conversation_id,
                channel=row.channel,
                channel_conversation_id=row.channel_conversation_id,
                client_idempotency_key=row.client_idempotency_key,
                state=DELIVERY_RECONCILED,
                external_message_id=(
                    str(echo.telegram_message_id)
                    if echo.telegram_message_id is not None
                    else echo.external_message_id
                ),
            )
            reconciled += 1
        return ReconcileReport(
            scanned=len(rows),
            reconciled=reconciled,
            still_unknown=len(rows) - reconciled,
        )

    async def _sent_text(self, row: DeliveryRuntime) -> str | None:
        if row.message_id is not None:
            msg = await self.session.get(Message, row.message_id)
            if msg is not None:
                return (msg.content or "").strip() or None
        return None

    async def _find_echo(self, row: DeliveryRuntime, text: str) -> Message | None:
        candidates = list(
            (
                await self.session.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == row.conversation_id,
                        Message.sender_type == SenderType.SELLER.value,
                        Message.content == text,
                        Message.is_deleted.is_(False),
                    )
                    .order_by(Message.created_at)
                )
            ).all()
        )
        window_start = row.sending_at or row.requested_at
        if window_start is not None:
            threshold = window_start - timedelta(seconds=_ECHO_SKEW_SECONDS)
            candidates = [
                m for m in candidates
                if (m.telegram_timestamp or m.created_at) >= threshold
            ]
        return candidates[0] if candidates else None
