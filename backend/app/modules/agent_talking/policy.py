from __future__ import annotations

from app.modules.agent_talking.contracts import (
    TalkActionKind,
    TalkBundle,
    TalkingMode,
    TalkPolicyDecision,
)

AUTOPILOT_TRUST_MODE = "autopilot"


def _auto_send_allowed(
    *,
    trust_mode: str,
    confidence: float,
    auto_send_threshold: float,
) -> tuple[bool, str]:
    if trust_mode != AUTOPILOT_TRUST_MODE:
        return False, "trust_mode_disabled"
    if auto_send_threshold < 0:
        return False, "threshold_disabled"
    if auto_send_threshold > 0 and confidence < auto_send_threshold:
        return False, "below_threshold"
    return True, "eligible"


def evaluate_talk_bundle(
    bundle: TalkBundle,
    *,
    granted_scopes: set[str],
    trust_mode: str,
    confidence: float,
    auto_send_threshold: float,
) -> TalkPolicyDecision:
    policy = bundle.talking_policy_snapshot
    if not bundle.actions:
        return TalkPolicyDecision(action="blocked", reason="empty_bundle")
    if len(bundle.actions) > policy.max_bubbles_per_turn:
        return TalkPolicyDecision(action="blocked", reason="max_bubbles_exceeded")

    missing_scopes = sorted(
        {action.requires_scope for action in bundle.actions if action.requires_scope not in granted_scopes}
    )
    if missing_scopes:
        return TalkPolicyDecision(
            action="blocked",
            reason="missing_tool_grant",
            required_scopes=missing_scopes,
        )

    blocked_indexes: list[int] = []
    for idx, action in enumerate(bundle.actions):
        if (
            action.kind in {TalkActionKind.SEND_MSG, TalkActionKind.REPLY_TO_MSG}
            and len(action.text or "") > policy.max_chars_per_bubble
        ):
            blocked_indexes.append(idx)
        if action.kind is TalkActionKind.SEND_MEDIA and not policy.allow_media:
            blocked_indexes.append(idx)
        if action.kind is TalkActionKind.SEND_REACTION and not policy.allow_reaction:
            blocked_indexes.append(idx)
        if action.kind is TalkActionKind.DELETE_MESSAGE and not policy.allow_delete:
            return TalkPolicyDecision(
                action="blocked",
                reason="delete_disabled",
                blocked_action_indexes=[idx],
            )
        if action.kind is TalkActionKind.SEND_STICKER and not policy.allow_sticker:
            blocked_indexes.append(idx)
    if blocked_indexes:
        return TalkPolicyDecision(
            action="blocked",
            reason="talking_policy_blocked_action",
            blocked_action_indexes=blocked_indexes,
        )

    if policy.mode in {TalkingMode.DRAFT, TalkingMode.SCANNER, TalkingMode.SILENT}:
        return TalkPolicyDecision(action="propose", reason=f"{policy.mode.value}: owner approval required")

    allowed, reason = _auto_send_allowed(
        trust_mode=trust_mode,
        confidence=confidence,
        auto_send_threshold=auto_send_threshold,
    )
    if allowed:
        return TalkPolicyDecision(action="auto_send", reason=reason)
    return TalkPolicyDecision(action="propose", reason=reason)
