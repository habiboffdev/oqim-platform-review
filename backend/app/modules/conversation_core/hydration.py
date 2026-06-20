"""Message hydration — placeholder matching for delivery confirmation.

Gateway-based hydration removed (Issue #69). Only the placeholder
matching logic remains (used by persist_message for delivery confirmation).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models.message import Message


def choose_placeholder_candidate(
    candidates: list[Message],
    message_date: datetime | None,
) -> Message | None:
    """Find the best placeholder message matching a confirmed delivery timestamp."""
    if not candidates:
        return None
    if not message_date:
        return candidates[0]

    ten_minutes = timedelta(minutes=10)
    filtered = [
        candidate
        for candidate in candidates
        if candidate.created_at and abs(candidate.created_at - message_date) <= ten_minutes
    ]
    if not filtered:
        return None
    return min(filtered, key=lambda candidate: abs(candidate.created_at - message_date))
