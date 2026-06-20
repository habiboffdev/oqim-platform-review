from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, asdict
from typing import Any


@dataclass(slots=True)
class TelegramConnectionStatus:
    state: str
    workspace_id: int
    user_id: str | None
    phone: str | None
    reconnect_attempts: int
    last_error: str | None = None
    queue_size: int = 0
    last_catch_up_at: str | None = None
    last_catch_up_count: int = 0

    @classmethod
    def disconnected(cls, workspace_id: int) -> "TelegramConnectionStatus":
        return cls(
            state="disconnected",
            workspace_id=workspace_id,
            user_id=None,
            phone=None,
            reconnect_attempts=0,
        )

    def as_api_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["workspaceId"] = payload.pop("workspace_id")
        payload["userId"] = payload.pop("user_id")
        payload["reconnectAttempts"] = payload.pop("reconnect_attempts")
        payload["lastError"] = payload.pop("last_error")
        payload["queueSize"] = payload.pop("queue_size")
        payload["lastCatchUpAt"] = payload.pop("last_catch_up_at")
        payload["lastCatchUpCount"] = payload.pop("last_catch_up_count")
        return payload


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _canonical_state(status: dict[str, Any]) -> str:
    raw_state = str(status.get("state") or "disconnected")
    if status.get("lastError") == "SESSION_REVOKED":
        return "revoked"
    if raw_state == "connected" and status.get("lastError"):
        return "degraded"
    if raw_state in {
        "connected",
        "disconnected",
        "connecting",
        "reconnecting",
        "failed",
        "revoked",
        "stale",
    }:
        return raw_state
    return "disconnected"


async def resolve_telegram_connection_status(
    *,
    workspace_id: int,
    fetch_status: Callable[..., Awaitable[dict[str, Any] | list[Any] | None]],
) -> TelegramConnectionStatus:
    status = await fetch_status(f"/status?workspaceId={workspace_id}", timeout_seconds=2.0)
    if not isinstance(status, dict):
        return TelegramConnectionStatus.disconnected(workspace_id=workspace_id)

    reported_workspace_id = status.get("workspaceId")
    try:
        if reported_workspace_id is not None and int(reported_workspace_id) != workspace_id:
            return TelegramConnectionStatus.disconnected(workspace_id=workspace_id)
    except (TypeError, ValueError):
        return TelegramConnectionStatus.disconnected(workspace_id=workspace_id)

    state = _canonical_state(status)
    has_transport = state in {"connected", "degraded"}
    return TelegramConnectionStatus(
        state=state,
        workspace_id=workspace_id,
        user_id=status.get("userId") if has_transport else None,
        phone=status.get("phone") if has_transport else None,
        reconnect_attempts=_safe_int(status.get("reconnectAttempts")),
        last_error=status.get("lastError"),
        queue_size=_safe_int(status.get("queueSize")),
        last_catch_up_at=status.get("lastCatchUpAt"),
        last_catch_up_count=_safe_int(status.get("lastCatchUpCount")),
    )
