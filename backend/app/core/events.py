"""Unified event protocol — Issue #103.

Every system event follows one schema:
  {type, scope, message, data, ts}

This module provides a helper to emit events through the existing
WebSocket manager. Replaces ad-hoc event dictionaries.
"""

from datetime import datetime, timezone

from app.core.logging import get_logger

logger = get_logger("oqim_business.events")


async def emit_event(
    workspace_id: int,
    *,
    type: str,
    message: str,
    scope: str = "system",
    data: dict | None = None,
) -> None:
    """Emit a unified event to a workspace via WebSocket.

    Args:
        workspace_id: target workspace
        type: event type (e.g. "sync:progress", "agent_action:thinking")
        message: human-readable description (Uzbek)
        scope: "system" or "conversation"
        data: optional structured payload
    """
    from app.api.routes.ws import manager

    event = {
        "type": type,
        "scope": scope,
        "message": message,
        "data": data or {},
        "ts": int(datetime.now(timezone.utc).timestamp()),
    }
    await manager.broadcast(workspace_id, event)
