from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

_PRESENCE_TIMEOUT_S = 5.0


@dataclass(slots=True)
class PresencePulseResult:
    online: bool = False
    read: bool = False
    typing: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "read": self.read,
            "typing": self.typing,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class PresenceLease:
    workspace_id: int
    conversation_id: int
    trigger_message_id: int
    hermes_run_id: str
    chat_id: str
    started_monotonic: float
    initial_result: PresencePulseResult
    heartbeat_task: asyncio.Task | None = None
    finished: bool = False
    finish_state: str | None = None


class TalkPresenceService:
    """Best-effort Telegram presence while Hermes is thinking.

    Presence is channel mechanics, not agent cognition. Failures are recorded as
    warnings and never block reply generation.
    """

    def __init__(
        self,
        *,
        sidecar_url: str,
        sidecar_api_key: str = "",
        timeout_seconds: float = _PRESENCE_TIMEOUT_S,
    ) -> None:
        self._sidecar_url = sidecar_url.rstrip("/")
        self._sidecar_api_key = sidecar_api_key
        self._timeout_seconds = max(float(timeout_seconds), 0.1)

    async def pulse(
        self,
        *,
        workspace_id: int,
        chat_id: str,
        max_message_id: int | None = None,
        online: bool = True,
        read: bool = True,
        typing: bool | None = True,
    ) -> PresencePulseResult:
        result = PresencePulseResult()
        if not chat_id:
            result.warnings.append("presence:no_chat_id")
            return result

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            if read:
                result.read = await self._post(
                    client,
                    "/read",
                    {
                        "workspaceId": workspace_id,
                        "chatId": chat_id,
                        "maxId": max_message_id or 0,
                        "allowReadReceipt": True,
                    },
                    warnings=result.warnings,
                    label="read",
                )
            if online:
                result.online = await self._post(
                    client,
                    "/online",
                    {
                        "workspaceId": workspace_id,
                        "allowOnlinePresence": True,
                    },
                    warnings=result.warnings,
                    label="online",
                )
            if typing is not None:
                result.typing = await self._post(
                    client,
                    "/typing",
                    {
                        "workspaceId": workspace_id,
                        "chatId": chat_id,
                        "typing": bool(typing),
                    },
                    warnings=result.warnings,
                    label="typing",
                )
        return result

    async def _post(
        self,
        client: httpx.AsyncClient,
        path: str,
        payload: dict[str, Any],
        *,
        warnings: list[str],
        label: str,
    ) -> bool:
        try:
            response = await client.post(
                f"{self._sidecar_url}{path}",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            body = response.json()
            if body.get("ok") is True:
                return True
            warnings.append(f"presence:{label}:{body.get('warning') or 'not_ok'}")
            return False
        except Exception as exc:
            warnings.append(f"presence:{label}:{type(exc).__name__}")
            return False

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._sidecar_api_key:
            headers["X-Sidecar-Key"] = self._sidecar_api_key
        return headers


