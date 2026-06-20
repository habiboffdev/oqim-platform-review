from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

DeliveryRuntimeState = Literal[
    "requested",
    "sending",
    "unknown",
    "confirmed",
    "reconciled",
    "failed",
]

DeliveryCustomerStatus = Literal["sending", "uncertain", "sent", "failed"]
DeliveryNextAction = Literal["wait", "reconcile", "retry", "none"]


class DeliveryRuntimeProjection(BaseModel):
    schema_version: Literal["delivery_runtime.v1"] = "delivery_runtime.v1"
    state: DeliveryRuntimeState
    customer_status: DeliveryCustomerStatus
    next_action: DeliveryNextAction
    is_terminal: bool
    requires_reconciliation: bool
    can_retry: bool
    attempt_count: int
    max_attempts: int
    retry_budget_remaining: int
    external_message_id: str | None = None
    last_error: str | None = None
    requested_at: datetime | None = None
    sending_at: datetime | None = None
    confirmed_at: datetime | None = None
    failed_at: datetime | None = None
    unknown_at: datetime | None = None
    reconciled_at: datetime | None = None
    updated_at: datetime | None = None
