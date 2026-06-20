"""Message intake gate.

This module intentionally does not infer customer intent. It only filters
structurally empty events; semantic should-reply decisions belong to the
post-debounce LLM planner.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ClassificationResult:
    should_enter_reply_lifecycle: bool
    reason: str


def classify_local(
    text: str,
    *,
    media_type: str | None = None,
) -> ClassificationResult:
    """Return whether this event should enter the Seller Agent reply lifecycle."""
    stripped = text.strip()
    if not stripped and not media_type:
        return ClassificationResult(False, "empty_message")

    return ClassificationResult(True, "defer_to_lifecycle_planner")
