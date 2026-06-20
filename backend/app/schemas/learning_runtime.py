"""Projection schemas for Seller Agent correction learning state."""

from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel

SellerAgentLearningState = Literal[
    "not_applicable",
    "queued",
    "learned",
    "skipped",
    "failed",
]
SellerAgentLearningAction = Literal["none", "wait", "retry"]


class SellerAgentLearningRuntimeProjection(BaseModel):
    schema_version: Literal["seller_agent_learning_runtime.v1"] = "seller_agent_learning_runtime.v1"
    state: SellerAgentLearningState
    source_action_id: int | None = None
    signal_id: int | None = None
    next_action: SellerAgentLearningAction
    last_error: str | None = None


def build_seller_agent_learning_runtime(actions: list[object]) -> SellerAgentLearningRuntimeProjection:
    if not actions:
        return SellerAgentLearningRuntimeProjection(state="not_applicable", next_action="none")

    latest = max(
        actions,
        key=lambda action: (
            getattr(action, "created_at", None) or datetime.min,
            getattr(action, "id", 0) or 0,
        ),
    )
    state = (getattr(latest, "learning_state", None) or "not_applicable").strip()
    if state not in {
        "not_applicable",
        "queued",
        "learned",
        "skipped",
        "failed",
    }:
        state = "not_applicable"

    if state == "queued":
        next_action: SellerAgentLearningAction = "wait"
    elif state == "failed":
        next_action = "retry"
    else:
        next_action = "none"

    return SellerAgentLearningRuntimeProjection(
        state=cast(SellerAgentLearningState, state),
        source_action_id=getattr(latest, "id", None),
        signal_id=getattr(latest, "learning_signal_id", None),
        next_action=next_action,
        last_error=getattr(latest, "learning_error", None),
    )
