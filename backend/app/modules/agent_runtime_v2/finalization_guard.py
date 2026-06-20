from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.agent_runtime_v2.faithfulness import FaithfulnessVerdict


@dataclass(frozen=True)
class FinalizedCustomerReply:
    customer_visible_text: str
    blocked: bool
    reason_code: str
    telemetry: dict[str, Any] = field(default_factory=dict)


def finalize_customer_visible_reply(
    *,
    reply_text: str,
    faithfulness: FaithfulnessVerdict,
    committed_action_refs: list[str],
) -> FinalizedCustomerReply:
    refs = [str(ref).strip() for ref in committed_action_refs if str(ref).strip()]
    unsupported = faithfulness.unsupported_authority_claims
    critic_reason = (
        "unsupported_authority_observed"
        if unsupported > 0 and not _has_committed_handoff(refs)
        else (
            "unsupported_authority_with_committed_handoff"
            if unsupported > 0
            else "supported_or_no_authority_claim"
        )
    )
    telemetry = {
        "schema_version": "customer_reply_finalization_guard.v1",
        "unsupported_authority_claims": unsupported,
        "committed_action_refs": refs,
        "blocked": False,
        "reason_code": critic_reason,
        "mode": "critic_only",
    }
    return FinalizedCustomerReply(
        customer_visible_text=reply_text,
        blocked=False,
        reason_code=critic_reason,
        telemetry=telemetry,
    )


def _has_committed_handoff(refs: list[str]) -> bool:
    return any(
        ref.startswith(
            ("owner_task:", "owner_notification:", "order_intent:", "checkout_intent:")
        )
        for ref in refs
    )
