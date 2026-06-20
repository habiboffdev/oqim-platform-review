"""Evidence confidence scoring for the Reply Agent runtime."""

from __future__ import annotations

_NO_GROUNDING_CAP = 0.5
_TOOL_ERROR_CAP = 0.4
_AUTHORITY_WARNING_CAP = 0.5
_UNSUPPORTED_AUTHORITY_CAP = 0.3  # a faithfulness miss must stay too weak for normal autopilot thresholds


def score_confidence(
    *,
    grounding_hits: int,
    tool_errors: int,
    authority_warnings: list[str] | None = None,
    unsupported_authority_claims: int = 0,
) -> float:
    """Return a 0..1 confidence score from observed evidence signals."""
    score = 1.0
    if tool_errors > 0:
        score = min(score, _TOOL_ERROR_CAP)
    if authority_warnings:
        score = min(score, _AUTHORITY_WARNING_CAP)
    if grounding_hits == 0:
        score = min(score, _NO_GROUNDING_CAP)
    if unsupported_authority_claims > 0:
        score = min(score, _UNSUPPORTED_AUTHORITY_CAP)
    return round(score, 4)
