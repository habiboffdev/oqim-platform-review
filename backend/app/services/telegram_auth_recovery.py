from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.telegram_auth_attempt import TelegramAuthAttempt
from app.services.worker_lease import WorkerLease, make_worker_owner_id

logger = get_logger("services.telegram_auth_recovery")

DEFAULT_POLL_INTERVAL_SECONDS = 3.0
DEFAULT_BATCH_SIZE = 10


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def delivery_state(result: dict | None) -> tuple[str | None, str | None, int | None, dict | None]:
    delivery = result.get("delivery") if isinstance(result, dict) else None
    if not isinstance(delivery, dict):
        return None, None, None, None
    timeout = delivery.get("timeoutSeconds")
    return (
        str(delivery["type"]) if delivery.get("type") else None,
        str(delivery["nextType"]) if delivery.get("nextType") else None,
        int(timeout) if isinstance(timeout, int) else None,
        delivery,
    )


async def default_sidecar_post_json(path: str, payload: dict) -> dict | None:
    settings = get_settings()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.sidecar_api_key:
        headers["X-Sidecar-Key"] = settings.sidecar_api_key
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{settings.sidecar_url}{path}", json=payload, headers=headers)
            try:
                data = response.json()
            except ValueError:
                data = {"error": response.text or response.reason_phrase}
            if response.status_code >= 400:
                data["_sidecar_status_code"] = response.status_code
            return data
    except Exception as exc:
        logger.warning("telegram_auth_recovery.sidecar_unreachable path=%s error=%s", path, exc)
        return None


class TelegramAuthRecoveryWorker:
    """Server-owned recovery for Telegram phone auth delivery steps.

    The browser may close after Telegram says "SMS sent". This worker owns the
    delayed resend/call transition using persisted temp MTProto session data.
    """

    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        redis: Any | None = None,
        sidecar_post_json: Callable[[str, dict], Awaitable[dict | None]] = default_sidecar_post_json,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._db_factory = db_factory
        self._redis = redis
        self._sidecar_post_json = sidecar_post_json
        self._poll_interval_seconds = max(float(poll_interval_seconds), 0.5)
        self._batch_size = max(1, int(batch_size))
        self._consumer_name = make_worker_owner_id("telegram_auth_recovery")
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None
        self._lease = (
            WorkerLease(redis, role="telegram_auth_recovery", ttl_seconds=30)
            if redis is not None
            else None
        )

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._heartbeat_callback = callback

    async def start(self) -> None:
        self._stopping = False
        has_lease = False
        while not self._stopping:
            try:
                if self._lease is not None:
                    has_lease = (
                        await self._lease.renew()
                        if has_lease
                        else await self._lease.acquire()
                    )
                    if not has_lease:
                        self._beat()
                        await asyncio.sleep(self._poll_interval_seconds)
                        continue
                processed = await self.run_once()
                self._beat()
                if processed == 0:
                    await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                raise
            except Exception:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                has_lease = False
                logger.exception("telegram_auth_recovery.tick_failed")
                await asyncio.sleep(min(self._poll_interval_seconds * 2, 10.0))
        if has_lease and self._lease is not None:
            await self._lease.release()

    async def stop(self) -> None:
        self._stopping = True

    async def run_once(self, *, now: datetime | None = None) -> int:
        current_time = now or utc_now()
        async with self._db_factory() as session:
            result = await session.execute(
                select(TelegramAuthAttempt)
                .where(
                    TelegramAuthAttempt.next_recovery_at.is_not(None),
                    TelegramAuthAttempt.next_recovery_at <= current_time,
                    TelegramAuthAttempt.next_delivery_type.is_not(None),
                    ~TelegramAuthAttempt.state.in_(["authenticated", "failed"]),
                    TelegramAuthAttempt.recovery_attempt_count
                    < TelegramAuthAttempt.max_recovery_attempts,
                )
                .order_by(TelegramAuthAttempt.next_recovery_at.asc(), TelegramAuthAttempt.id.asc())
                .limit(self._batch_size)
            )
            attempts = list(result.scalars().all())

            for attempt in attempts:
                attempt.recovery_state = "running"
                attempt.last_recovery_at = current_time
                attempt.recovery_attempt_count = (attempt.recovery_attempt_count or 0) + 1
                attempt.next_recovery_at = None
            await session.commit()
            attempt_ids = [int(attempt.id) for attempt in attempts]

        processed = 0
        for attempt_id in attempt_ids:
            self._beat()
            async with self._db_factory() as session:
                attempt = await session.get(TelegramAuthAttempt, attempt_id)
                if attempt is None:
                    continue
                payload: dict[str, str] = {
                    "phoneNumber": attempt.phone_number,
                    "tempSessionId": str(attempt.temp_session_id or ""),
                    "phoneCodeHash": str(attempt.phone_code_hash or ""),
                }
                if attempt.temp_session_data:
                    payload["tempSessionString"] = attempt.temp_session_data
                try:
                    result = await self._sidecar_post_json("/auth/resend-code", payload)
                    if result is None:
                        raise RuntimeError("Sidecar unreachable")
                    status_code = result.get("_sidecar_status_code")
                    if isinstance(status_code, int) and status_code >= 400:
                        retry_after = result.get("retryAfter")
                        attempt.retry_after_seconds = int(retry_after) if isinstance(retry_after, int) else None
                        raise RuntimeError(
                            str(result.get("code") or result.get("error") or "Telegram auth recovery failed")
                        )
                    delivery_type, next_delivery_type, timeout_seconds, delivery_payload = delivery_state(result)
                    attempt.phone_code_hash = str(result.get("phoneCodeHash") or attempt.phone_code_hash or "")
                    if result.get("tempSessionString"):
                        attempt.temp_session_data = str(result["tempSessionString"])
                    attempt.delivery_type = delivery_type or attempt.delivery_type
                    attempt.next_delivery_type = next_delivery_type
                    attempt.timeout_seconds = timeout_seconds
                    attempt.delivery_payload = delivery_payload or attempt.delivery_payload
                    attempt.state = "recovery_sent"
                    attempt.last_step = "server_recovery"
                    attempt.last_error = None
                    if next_delivery_type and timeout_seconds:
                        attempt.next_recovery_at = current_time + timedelta(seconds=max(timeout_seconds, 1))
                        attempt.recovery_state = "scheduled"
                    else:
                        attempt.next_recovery_at = None
                        attempt.recovery_state = "exhausted"
                    await session.commit()
                except Exception as exc:
                    attempt.last_step = "server_recovery"
                    attempt.last_error = f"{type(exc).__name__}: {exc}"
                    if attempt.recovery_attempt_count < attempt.max_recovery_attempts:
                        delay_seconds = attempt.retry_after_seconds or 90
                        attempt.next_recovery_at = current_time + timedelta(seconds=delay_seconds)
                        attempt.recovery_state = "scheduled"
                    else:
                        attempt.next_recovery_at = None
                        attempt.recovery_state = "failed"
                        attempt.state = "failed"
                    await session.commit()
                processed += 1
        return processed

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()
