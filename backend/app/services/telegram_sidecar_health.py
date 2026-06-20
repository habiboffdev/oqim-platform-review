"""Telegram sidecar health probing for workspace-scoped operations views."""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.services.telegram_connection_state import (
    TelegramConnectionStatus,
    resolve_telegram_connection_status,
)

logger = logging.getLogger(__name__)


async def load_telegram_sidecar_status(*, workspace_id: int) -> TelegramConnectionStatus:
    settings = get_settings()
    headers: dict[str, str] = {}
    if settings.sidecar_api_key:
        headers["X-Sidecar-Key"] = settings.sidecar_api_key

    async def _fetch_status(path: str, *, timeout_seconds: float) -> dict:
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.get(
                    f"{settings.sidecar_url}{path}",
                    headers=headers,
                )
            if response.status_code >= 400:
                return {
                    "workspaceId": workspace_id,
                    "state": "failed",
                    "lastError": f"sidecar_http_{response.status_code}",
                }
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            logger.warning("failed to inspect telegram sidecar health: %s", exc)
            return {
                "workspaceId": workspace_id,
                "state": "failed",
                "lastError": exc.__class__.__name__,
            }

    return await resolve_telegram_connection_status(
        workspace_id=workspace_id,
        fetch_status=_fetch_status,
    )
