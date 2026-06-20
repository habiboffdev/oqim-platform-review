from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.delivery_runtime import DeliveryRuntime
from app.schemas.delivery import DeliveryRuntimeProjection

DELIVERY_REQUESTED = "requested"
DELIVERY_SENDING = "sending"
DELIVERY_CONFIRMED = "confirmed"
DELIVERY_FAILED = "failed"
DELIVERY_UNKNOWN = "unknown"
DELIVERY_RECONCILED = "reconciled"
DELIVERY_MAX_ATTEMPTS = 3


def _merge_runtime_refs(
    row: DeliveryRuntime,
    *,
    message_id: int | None,
    action_record_id: int | None,
    external_message_id: str | None,
) -> None:
    if message_id is not None:
        row.message_id = row.message_id or message_id
    if action_record_id is not None:
        row.action_record_id = row.action_record_id or action_record_id
    if external_message_id is not None:
        row.external_message_id = row.external_message_id or external_message_id


def _should_ignore_state_transition(current: str, next_state: str) -> bool:
    if current == DELIVERY_RECONCILED and next_state != DELIVERY_RECONCILED:
        return True
    if current == DELIVERY_CONFIRMED and next_state not in {
        DELIVERY_CONFIRMED,
        DELIVERY_RECONCILED,
    }:
        return True
    if current == DELIVERY_UNKNOWN and next_state in {
        DELIVERY_REQUESTED,
        DELIVERY_SENDING,
        DELIVERY_FAILED,
    }:
        return True
    if current in {DELIVERY_SENDING, DELIVERY_FAILED} and next_state == DELIVERY_REQUESTED:
        return True
    return False


async def record_delivery_state(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation_id: int,
    channel: str,
    channel_conversation_id: str | None,
    client_idempotency_key: str,
    state: str,
    message_id: int | None = None,
    action_record_id: int | None = None,
    external_message_id: str | None = None,
    error: str | None = None,
) -> DeliveryRuntime:
    now = datetime.now(timezone.utc)
    row = await session.scalar(
        select(DeliveryRuntime).where(
            DeliveryRuntime.workspace_id == workspace_id,
            DeliveryRuntime.client_idempotency_key == client_idempotency_key,
        )
    )
    if row is None:
        row = DeliveryRuntime(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            channel=channel,
            channel_conversation_id=channel_conversation_id,
            client_idempotency_key=client_idempotency_key,
            state=state,
            attempt_count=0,
            requested_at=now,
            created_at=now,
            updated_at=now,
        )
    elif _should_ignore_state_transition(row.state, state):
        # Replayed/late events must not make a confirmed or uncertain delivery
        # look failed/pending again. Failed deliveries may still be retried by
        # moving to sending, but unknown deliveries wait for echo reconciliation.
        _merge_runtime_refs(
            row,
            message_id=message_id,
            action_record_id=action_record_id,
            external_message_id=external_message_id,
        )
        session.add(row)
        await session.flush()
        return row

    row.conversation_id = conversation_id
    row.channel = channel
    row.channel_conversation_id = channel_conversation_id
    row.state = state
    row.updated_at = now
    if message_id is not None:
        row.message_id = message_id
    if action_record_id is not None:
        row.action_record_id = action_record_id
    if external_message_id is not None:
        row.external_message_id = external_message_id
    if error is not None:
        row.last_error = error
    elif state in {DELIVERY_CONFIRMED, DELIVERY_RECONCILED}:
        row.last_error = None

    if state == DELIVERY_REQUESTED and row.requested_at is None:
        row.requested_at = now
    elif state == DELIVERY_SENDING:
        row.sending_at = now
        row.attempt_count = int(row.attempt_count or 0) + 1
    elif state == DELIVERY_CONFIRMED:
        row.confirmed_at = now
    elif state == DELIVERY_FAILED:
        row.failed_at = now
    elif state == DELIVERY_UNKNOWN:
        row.unknown_at = now
    elif state == DELIVERY_RECONCILED:
        row.reconciled_at = now

    session.add(row)
    await session.flush()
    return row


def project_delivery_runtime(
    runtime: DeliveryRuntime | None,
    *,
    max_attempts: int = DELIVERY_MAX_ATTEMPTS,
) -> DeliveryRuntimeProjection | None:
    if runtime is None:
        return None

    state = runtime.state
    attempt_count = int(runtime.attempt_count or 0)
    retry_budget_remaining = max(0, int(max_attempts) - attempt_count)

    if state in {DELIVERY_CONFIRMED, DELIVERY_RECONCILED}:
        customer_status = "sent"
        next_action = "none"
        is_terminal = True
        requires_reconciliation = False
        can_retry = False
    elif state == DELIVERY_UNKNOWN:
        customer_status = "uncertain"
        next_action = "reconcile"
        is_terminal = False
        requires_reconciliation = True
        can_retry = False
    elif state == DELIVERY_FAILED:
        customer_status = "failed"
        next_action = "retry"
        is_terminal = True
        requires_reconciliation = False
        can_retry = True
    else:
        customer_status = "sending"
        next_action = "wait"
        is_terminal = False
        requires_reconciliation = False
        can_retry = False

    return DeliveryRuntimeProjection(
        state=state,
        customer_status=customer_status,
        next_action=next_action,
        is_terminal=is_terminal,
        requires_reconciliation=requires_reconciliation,
        can_retry=can_retry,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        retry_budget_remaining=retry_budget_remaining,
        external_message_id=runtime.external_message_id,
        last_error=runtime.last_error,
        requested_at=runtime.requested_at,
        sending_at=runtime.sending_at,
        confirmed_at=runtime.confirmed_at,
        failed_at=runtime.failed_at,
        unknown_at=runtime.unknown_at,
        reconciled_at=runtime.reconciled_at,
        updated_at=runtime.updated_at,
    )
