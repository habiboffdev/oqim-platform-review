from __future__ import annotations

from app.modules.agent_talking.contracts import PacingPlan, TalkActionKind, TalkBundle

_PROFILES = {
    "none": {
        "base": 0,
        "per_char": 0,
        "min": 0,
        "max": 0,
        "gap_base": 0,
        "gap_factor": 0,
        "gap_min": 0,
        "gap_max": 0,
    },
    "fast": {
        "base": 200,
        "per_char": 12,
        "min": 200,
        "max": 900,
        "gap_base": 150,
        "gap_factor": 2,
        "gap_min": 150,
        "gap_max": 600,
    },
    "human": {
        "base": 500,
        "per_char": 28,
        "min": 500,
        "max": 6000,
        "gap_base": 350,
        "gap_factor": 3,
        "gap_min": 350,
        "gap_max": 1400,
    },
    "slow": {
        "base": 900,
        "per_char": 32,
        "min": 900,
        "max": 4200,
        "gap_base": 700,
        "gap_factor": 4,
        "gap_min": 700,
        "gap_max": 2200,
    },
}


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(value, upper))


def compute_pacing_plan(bundle: TalkBundle) -> list[PacingPlan]:
    policy = bundle.talking_policy_snapshot
    profile = _PROFILES[policy.pacing_profile]
    out: list[PacingPlan] = []
    for idx, action in enumerate(bundle.actions):
        text = action.text or ""
        if policy.typing_indicator == "off" or action.kind not in {
            TalkActionKind.SEND_MSG,
            TalkActionKind.REPLY_TO_MSG,
            TalkActionKind.SEND_MEDIA,
        }:
            typing_ms = 0
        else:
            typing_ms = _clamp(
                int(profile["base"] + len(text) * profile["per_char"]),
                int(profile["min"]),
                int(profile["max"]),
            )
        delay_after_ms = 0
        if idx < len(bundle.actions) - 1:
            delay_after_ms = _clamp(
                int(profile["gap_base"] + len(text) * profile["gap_factor"]),
                int(profile["gap_min"]),
                int(profile["gap_max"]),
            )
        out.append(PacingPlan(action_index=idx, typing_ms=typing_ms, delay_after_ms=delay_after_ms))
    return out

