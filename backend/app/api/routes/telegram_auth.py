"""Telegram auth routes — phone number login + onboarding bridge via GramJS sidecar.

Replaces QR auth with phone + code + 2FA flow.

POST /telegram/auth/send-code       — send verification code to phone
POST /telegram/auth/sign-in         — verify code and sign in
POST /telegram/auth/check-2fa       — submit 2FA password
GET  /telegram/auth/status          — proxy sidecar connection status
POST /telegram/start-ingestion      — onboarding Step 1 bridge
GET  /telegram/channels             — onboarding channel picker
POST /telegram/scan-channels        — onboarding channel scan bridge
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_current_workspace_optional, get_db_session
from app.core.logging import get_logger
from app.db.session import async_session
from app.models.telegram_auth_attempt import TelegramAuthAttempt
from app.models.workspace import Workspace
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import MemoryFactWriteInput
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.source_runtime import OnboardingSourceLearningRuntimeService
from app.services import onboarding_runtime
from app.services.telegram_connection_state import resolve_telegram_connection_status

logger = get_logger("api.telegram_auth")

router = APIRouter(prefix="/telegram", tags=["telegram-auth"])
TEMP_SESSION_COOKIE = "oqim_tg_temp_session"
SIDECAR_STATUS_KEY = "_sidecar_status_code"
_bg_tasks = onboarding_runtime._bg_tasks
SERVER_ONLY_SIDECAR_KEYS = {"tempSessionString"}
AUTH_THROTTLE_WINDOW_SECONDS = 10 * 60
AUTH_THROTTLE_MAX_PHONE_ATTEMPTS = 3
AUTH_THROTTLE_MAX_WORKSPACE_ATTEMPTS = 10


class ScanChannelsRequest(BaseModel):
    channel_ids: list[int]


def _set_temp_session_cookie(response: Response, temp_session_id: str) -> None:
    settings = get_settings()
    cookie_kwargs = {
        "key": TEMP_SESSION_COOKIE,
        "value": temp_session_id,
        "max_age": 30 * 60,
        "path": "/",
        "httponly": True,
        "samesite": "lax",
        "secure": settings.cookie_secure,
    }
    if settings.cookie_domain:
        cookie_kwargs["domain"] = settings.cookie_domain
    response.set_cookie(**cookie_kwargs)


def _attach_temp_session_id(request: Request, body: dict) -> dict:
    temp_session_id = body.get("tempSessionId") or request.cookies.get(TEMP_SESSION_COOKIE)
    if temp_session_id:
        body["tempSessionId"] = temp_session_id
    return body


def _telegram_channel_source_ref(*, workspace_id: int, channel_id: int) -> str:
    return f"telegram_channel:{workspace_id}:{channel_id}"


def _source_message_from_channel_post(post: dict[str, Any], *, index: int) -> dict[str, Any]:
    message_id = post.get("message_id") or post.get("postId") or post.get("id") or index
    return {
        "message_id": str(message_id),
        "text": str(post.get("text") or post.get("caption") or ""),
        "caption": str(post.get("caption") or ""),
        "date": post.get("date"),
        "media_type": post.get("mediaType") or post.get("media_type"),
        "media_ref": post.get("media_ref"),
        "media_url": post.get("media_url") or post.get("url"),
        "mime_type": post.get("mime_type") or post.get("mimeType"),
    }


async def _sidecar_get(
    path: str,
    *,
    params: dict[str, str | int] | None = None,
    timeout_seconds: float = 30.0,
) -> dict | list | None:
    """GET from GramJS sidecar."""
    settings = get_settings()
    url = f"{settings.sidecar_url}{path}"
    headers: dict[str, str] = {}
    if settings.sidecar_api_key:
        headers["X-Sidecar-Key"] = settings.sidecar_api_key
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.warning("Sidecar GET %s failed: %s", path, e)
    return None


async def _sidecar_post_json(path: str, payload: dict) -> dict | None:
    """POST JSON to GramJS sidecar and return parsed response.

    Returns None if sidecar is unreachable or returns non-JSON.
    """
    settings = get_settings()
    url = f"{settings.sidecar_url}{path}"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.sidecar_api_key:
        headers["X-Sidecar-Key"] = settings.sidecar_api_key

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            try:
                data = resp.json()
            except ValueError:
                data = {"error": resp.text or resp.reason_phrase}
            if resp.status_code >= 400:
                data[SIDECAR_STATUS_KEY] = resp.status_code
            return data
    except httpx.ConnectError:
        logger.error("Sidecar unreachable at %s", url)
        return None
    except Exception:
        logger.exception("Sidecar request failed: %s", path)
        return None


def _raise_sidecar_error(result: dict) -> None:
    status_code = result.get(SIDECAR_STATUS_KEY)
    if not isinstance(status_code, int) or status_code < 400:
        return
    detail = (
        result.get("code")
        or result.get("error")
        or result.get("message")
        or "Telegram auth failed"
    )
    raise HTTPException(status_code=status_code, detail=str(detail))


def _sidecar_error_detail(result: dict) -> str:
    return str(
        result.get("code")
        or result.get("error")
        or result.get("message")
        or "Telegram auth failed"
    )


def _delivery_state(
    result: dict | None,
) -> tuple[str | None, str | None, int | None, str | None, bool, str | None, dict | None]:
    delivery = result.get("delivery") if isinstance(result, dict) else None
    if not isinstance(delivery, dict):
        return None, None, None, None, False, None, None
    timeout = delivery.get("timeoutSeconds")
    preferred_delivery_type = (
        str(delivery["preferredType"]) if delivery.get("preferredType") else None
    )
    delivery_degraded = bool(delivery.get("degraded"))
    delivery_degraded_reason = (
        str(delivery["degradedReason"]) if delivery.get("degradedReason") else None
    )
    return (
        str(delivery["type"]) if delivery.get("type") else None,
        str(delivery["nextType"]) if delivery.get("nextType") else None,
        int(timeout) if isinstance(timeout, int) else None,
        preferred_delivery_type,
        delivery_degraded,
        delivery_degraded_reason,
        delivery,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _client_auth_result(result: dict) -> dict:
    """Remove sidecar-only auth material before returning JSON to the browser."""
    return {key: value for key, value in result.items() if key not in SERVER_ONLY_SIDECAR_KEYS}


def _next_recovery_at_from_delivery(
    *,
    next_delivery_type: str | None,
    timeout_seconds: int | None,
    now: datetime | None = None,
) -> datetime | None:
    if not next_delivery_type or not timeout_seconds:
        return None
    return (now or _utc_now()) + timedelta(seconds=max(timeout_seconds, 1))


async def _find_auth_attempt(
    session: AsyncSession,
    *,
    temp_session_id: str | None = None,
    phone: str | None = None,
    workspace_id: int | None = None,
) -> TelegramAuthAttempt | None:
    if temp_session_id:
        return (
            await session.execute(
                select(TelegramAuthAttempt).where(
                    TelegramAuthAttempt.temp_session_id == temp_session_id
                )
            )
        ).scalar_one_or_none()

    if not phone:
        return None
    query = (
        select(TelegramAuthAttempt)
        .where(TelegramAuthAttempt.phone_number == phone)
        .order_by(TelegramAuthAttempt.updated_at.desc(), TelegramAuthAttempt.id.desc())
        .limit(1)
    )
    if workspace_id is not None:
        query = query.where(TelegramAuthAttempt.workspace_id == workspace_id)
    else:
        query = query.where(TelegramAuthAttempt.workspace_id.is_(None))
    return (await session.execute(query)).scalar_one_or_none()


async def _augment_payload_with_stored_auth_attempt(
    *,
    session: AsyncSession,
    payload: dict,
    phone: str,
    workspace_id: int | None,
) -> dict:
    attempt = await _find_auth_attempt(
        session,
        temp_session_id=str(payload.get("tempSessionId")) if payload.get("tempSessionId") else None,
        phone=phone,
        workspace_id=workspace_id,
    )
    if attempt is None:
        return payload
    if attempt.temp_session_data and not payload.get("tempSessionString"):
        payload["tempSessionString"] = attempt.temp_session_data
    if attempt.phone_code_hash and not payload.get("phoneCodeHash"):
        payload["phoneCodeHash"] = attempt.phone_code_hash
    return payload


async def _enforce_auth_throttle(
    session: AsyncSession,
    *,
    phone: str,
    workspace_id: int | None,
) -> None:
    since = _utc_now() - timedelta(seconds=AUTH_THROTTLE_WINDOW_SECONDS)
    attempt_weight = case(
        (TelegramAuthAttempt.attempt_count > 0, TelegramAuthAttempt.attempt_count),
        else_=1,
    )
    phone_count = await session.scalar(
        select(func.coalesce(func.sum(attempt_weight), 0)).where(
            TelegramAuthAttempt.phone_number == phone,
            TelegramAuthAttempt.updated_at >= since,
            TelegramAuthAttempt.last_step.in_(["send_code", "resend_code"]),
        )
    )
    if int(phone_count or 0) >= AUTH_THROTTLE_MAX_PHONE_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many Telegram code requests for this phone. Try again later.",
            headers={"Retry-After": str(AUTH_THROTTLE_WINDOW_SECONDS)},
        )
    if workspace_id is None:
        return
    workspace_count = await session.scalar(
        select(func.coalesce(func.sum(attempt_weight), 0)).where(
            TelegramAuthAttempt.workspace_id == workspace_id,
            TelegramAuthAttempt.updated_at >= since,
            TelegramAuthAttempt.last_step.in_(["send_code", "resend_code"]),
        )
    )
    if int(workspace_count or 0) >= AUTH_THROTTLE_MAX_WORKSPACE_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many Telegram code requests for this workspace. Try again later.",
            headers={"Retry-After": str(AUTH_THROTTLE_WINDOW_SECONDS)},
        )


async def _record_auth_attempt(
    *,
    session: AsyncSession,
    phone: str,
    step: str,
    state: str,
    workspace_id: int | None = None,
    temp_session_id: str | None = None,
    phone_code_hash: str | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    (
        delivery_type,
        next_delivery_type,
        timeout_seconds,
        preferred_delivery_type,
        delivery_degraded,
        delivery_degraded_reason,
        delivery_payload,
    ) = _delivery_state(result)
    existing: TelegramAuthAttempt | None = None
    lookup_temp_session_id = temp_session_id or (
        str(result.get("tempSessionId")) if isinstance(result, dict) and result.get("tempSessionId") else None
    )
    if lookup_temp_session_id:
        existing = (
            await session.execute(
                select(TelegramAuthAttempt).where(
                    TelegramAuthAttempt.temp_session_id == lookup_temp_session_id
                )
            )
        ).scalar_one_or_none()

    if existing is None:
        query = (
            select(TelegramAuthAttempt)
            .where(TelegramAuthAttempt.phone_number == phone)
            .order_by(TelegramAuthAttempt.updated_at.desc(), TelegramAuthAttempt.id.desc())
            .limit(1)
        )
        if workspace_id is not None:
            query = query.where(TelegramAuthAttempt.workspace_id == workspace_id)
        else:
            query = query.where(TelegramAuthAttempt.workspace_id.is_(None))
        existing = (await session.execute(query)).scalar_one_or_none()

    attempt = existing or TelegramAuthAttempt(
        phone_number=phone or "unknown",
        workspace_id=workspace_id,
    )
    if phone:
        attempt.phone_number = phone
    attempt.workspace_id = workspace_id
    attempt.state = state
    attempt.last_step = step
    attempt.last_error = error
    attempt.attempt_count = (attempt.attempt_count or 0) + 1
    attempt.temp_session_id = lookup_temp_session_id or attempt.temp_session_id
    result_phone_code_hash = (
        str(result.get("phoneCodeHash")) if isinstance(result, dict) and result.get("phoneCodeHash") else None
    )
    attempt.phone_code_hash = result_phone_code_hash or phone_code_hash or attempt.phone_code_hash
    should_clear_stale_delivery = (
        state == "failed"
        and step in {"send_code", "resend_code", "server_recovery"}
        and delivery_payload is None
    )
    if should_clear_stale_delivery:
        attempt.delivery_type = None
        attempt.preferred_delivery_type = None
        attempt.delivery_degraded = False
        attempt.delivery_degraded_reason = None
        attempt.next_delivery_type = None
        attempt.timeout_seconds = None
        attempt.delivery_payload = None
    else:
        attempt.delivery_type = delivery_type or attempt.delivery_type
        attempt.preferred_delivery_type = preferred_delivery_type or attempt.preferred_delivery_type
        attempt.delivery_degraded = delivery_degraded
        attempt.delivery_degraded_reason = delivery_degraded_reason
        attempt.next_delivery_type = next_delivery_type
        attempt.timeout_seconds = timeout_seconds
        attempt.delivery_payload = delivery_payload or attempt.delivery_payload
    attempt.next_recovery_at = _next_recovery_at_from_delivery(
        next_delivery_type=attempt.next_delivery_type,
        timeout_seconds=attempt.timeout_seconds,
    )
    if attempt.next_recovery_at:
        attempt.recovery_state = "scheduled"
    elif state in {"authenticated", "failed"}:
        attempt.recovery_state = state
    elif state == "code_sent":
        attempt.recovery_state = "awaiting_code"
    elif step in {"resend_code", "server_recovery"} and state == "recovery_sent":
        attempt.recovery_state = "exhausted"
    if isinstance(result, dict) and result.get("tempSessionString"):
        attempt.temp_session_data = str(result["tempSessionString"])
    if result and isinstance(result.get("retryAfter"), int):
        attempt.retry_after_seconds = int(result["retryAfter"])
    if existing is None:
        session.add(attempt)
    await session.commit()


@router.get("/auth/status")
async def telegram_status(
    workspace: Workspace | None = Depends(get_current_workspace_optional),
):
    """Proxy sidecar connection status for the health widget."""
    if workspace is None:
        return {
            "state": "disconnected",
            "workspaceId": 0,
            "userId": None,
            "phone": None,
            "reconnectAttempts": 0,
            "lastError": None,
            "queueSize": 0,
            "lastCatchUpAt": None,
            "lastCatchUpCount": 0,
            "identityLinked": False,
            "needsReconnect": False,
        }

    status = await resolve_telegram_connection_status(
        workspace_id=workspace.id,
        fetch_status=_sidecar_get,
    )
    payload = status.as_api_dict()
    identity_linked = workspace.telegram_user_id is not None
    identity_unverified = (
        identity_linked
        and status.state in {"connected", "degraded"}
        and status.user_id is None
    )
    identity_mismatch = (
        identity_linked
        and status.state in {"connected", "degraded"}
        and status.user_id is not None
        and str(status.user_id) != str(workspace.telegram_user_id)
    )
    if identity_mismatch or identity_unverified:
        payload["state"] = "stale"
        payload["userId"] = None
        payload["phone"] = None
        payload["lastError"] = (
            "telegram_identity_mismatch"
            if identity_mismatch
            else "telegram_identity_unverified"
        )
    payload["identityLinked"] = identity_linked
    payload["identityMismatch"] = identity_mismatch
    payload["identityVerified"] = (
        identity_linked
        and status.user_id is not None
        and str(status.user_id) == str(workspace.telegram_user_id)
    )
    payload["needsReconnect"] = identity_mismatch or identity_unverified or (identity_linked and payload["state"] in {
        "disconnected",
        "failed",
        "revoked",
        "stale",
    })
    return payload


@router.post("/auth/send-code")
async def send_telegram_code(
    request: Request,
    response: Response,
    workspace: Workspace | None = Depends(get_current_workspace_optional),
    db: AsyncSession = Depends(get_db_session),
):
    """Send verification code to a phone number via Telegram."""
    body = await request.json()
    phone = body.get("phone", "")
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    await _enforce_auth_throttle(db, phone=phone, workspace_id=workspace.id if workspace else None)

    payload: dict[str, str | int] = {
        "phoneNumber": phone,
        "deliveryPreference": "app",
    }
    if temp_session_id := body.get("tempSessionId"):
        payload["tempSessionId"] = str(temp_session_id)
    if auth_transport := body.get("authTransport"):
        normalized_transport = str(auth_transport).strip().lower()
        if normalized_transport not in {"web", "tcp"}:
            raise HTTPException(status_code=400, detail="authTransport must be web or tcp")
        payload["authTransport"] = normalized_transport
    if workspace:
        payload["workspaceId"] = workspace.id

    result = await _sidecar_post_json("/auth/send-code", payload)
    if result is None:
        await _record_auth_attempt(
            session=db,
            phone=phone,
            workspace_id=workspace.id if workspace else None,
            step="send_code",
            state="failed",
            error="Sidecar unreachable",
        )
        raise HTTPException(status_code=502, detail="Sidecar unreachable")
    if isinstance(result.get(SIDECAR_STATUS_KEY), int) and result[SIDECAR_STATUS_KEY] >= 400:
        await _record_auth_attempt(
            session=db,
            phone=phone,
            workspace_id=workspace.id if workspace else None,
            step="send_code",
            state="failed",
            result=result,
            error=_sidecar_error_detail(result),
        )
    _raise_sidecar_error(result)

    if temp_session_id := result.get("tempSessionId"):
        _set_temp_session_cookie(response, str(temp_session_id))
    await _record_auth_attempt(
        session=db,
        phone=phone,
        workspace_id=workspace.id if workspace else None,
        step="send_code",
        state="code_sent",
        result=result,
    )

    return _client_auth_result(result)


@router.post("/auth/resend-code")
async def resend_telegram_code(
    request: Request,
    response: Response,
    workspace: Workspace | None = Depends(get_current_workspace_optional),
    db: AsyncSession = Depends(get_db_session),
):
    """Resend verification code for the current Telegram temp auth session."""
    body = await request.json()
    phone = body.get("phone", "")
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")

    payload = {"phoneNumber": phone}
    if phone_code_hash := body.get("phoneCodeHash"):
        payload["phoneCodeHash"] = phone_code_hash
    if temp_session_id := body.get("tempSessionId"):
        payload["tempSessionId"] = temp_session_id
    payload = _attach_temp_session_id(request, payload)
    await _enforce_auth_throttle(db, phone=phone, workspace_id=workspace.id if workspace else None)
    payload = await _augment_payload_with_stored_auth_attempt(
        session=db,
        payload=payload,
        phone=phone,
        workspace_id=workspace.id if workspace else None,
    )
    result = await _sidecar_post_json("/auth/resend-code", payload)
    if result is None:
        await _record_auth_attempt(
            session=db,
            phone=phone,
            workspace_id=workspace.id if workspace else None,
            temp_session_id=str(payload.get("tempSessionId")) if payload.get("tempSessionId") else None,
            phone_code_hash=str(payload.get("phoneCodeHash")) if payload.get("phoneCodeHash") else None,
            step="resend_code",
            state="failed",
            error="Sidecar unreachable",
        )
        raise HTTPException(status_code=502, detail="Sidecar unreachable")
    if isinstance(result.get(SIDECAR_STATUS_KEY), int) and result[SIDECAR_STATUS_KEY] >= 400:
        await _record_auth_attempt(
            session=db,
            phone=phone,
            workspace_id=workspace.id if workspace else None,
            temp_session_id=str(payload.get("tempSessionId")) if payload.get("tempSessionId") else None,
            phone_code_hash=str(payload.get("phoneCodeHash")) if payload.get("phoneCodeHash") else None,
            step="resend_code",
            state="failed",
            result=result,
            error=_sidecar_error_detail(result),
        )
    _raise_sidecar_error(result)

    if temp_session_id := result.get("tempSessionId"):
        _set_temp_session_cookie(response, str(temp_session_id))
    await _record_auth_attempt(
        session=db,
        phone=phone,
        workspace_id=workspace.id if workspace else None,
        temp_session_id=str(payload.get("tempSessionId")) if payload.get("tempSessionId") else None,
        phone_code_hash=str(payload.get("phoneCodeHash")) if payload.get("phoneCodeHash") else None,
        step="resend_code",
        state="recovery_sent",
        result=result,
    )

    return _client_auth_result(result)


@router.post("/auth/sign-in")
async def sign_in_telegram(
    request: Request,
    response: Response,
    workspace: Workspace | None = Depends(get_current_workspace_optional),
    db: AsyncSession = Depends(get_db_session),
):
    """Verify code and sign in. Returns {error: '2FA_REQUIRED'} if 2FA needed."""
    body = await request.json()
    phone = body.get("phone", "")
    phone_code_hash = body.get("phoneCodeHash", "")
    code = body.get("code", "")

    if not phone or not code:
        raise HTTPException(status_code=400, detail="phone and code are required")

    payload: dict[str, str | int] = {
        "phoneNumber": phone,
        "phoneCodeHash": phone_code_hash,
        "phoneCode": code,
    }
    if temp_session_id := body.get("tempSessionId"):
        payload["tempSessionId"] = temp_session_id
    if workspace:
        payload["workspaceId"] = workspace.id
    payload = _attach_temp_session_id(request, payload)
    payload = await _augment_payload_with_stored_auth_attempt(
        session=db,
        payload=payload,
        phone=phone,
        workspace_id=workspace.id if workspace else None,
    )

    result = await _sidecar_post_json("/auth/sign-in", payload)
    if result is None:
        await _record_auth_attempt(
            session=db,
            phone=phone,
            workspace_id=workspace.id if workspace else None,
            temp_session_id=str(payload.get("tempSessionId")) if payload.get("tempSessionId") else None,
            phone_code_hash=phone_code_hash,
            step="sign_in",
            state="failed",
            error="Sidecar unreachable",
        )
        raise HTTPException(status_code=502, detail="Sidecar unreachable")
    if isinstance(result.get(SIDECAR_STATUS_KEY), int) and result[SIDECAR_STATUS_KEY] >= 400:
        await _record_auth_attempt(
            session=db,
            phone=phone,
            workspace_id=workspace.id if workspace else None,
            temp_session_id=str(payload.get("tempSessionId")) if payload.get("tempSessionId") else None,
            phone_code_hash=phone_code_hash,
            step="sign_in",
            state="failed",
            result=result,
            error=_sidecar_error_detail(result),
        )
    _raise_sidecar_error(result)

    if temp_session_id := result.get("tempSessionId"):
        _set_temp_session_cookie(response, str(temp_session_id))
    await _record_auth_attempt(
        session=db,
        phone=phone,
        workspace_id=workspace.id if workspace else None,
        temp_session_id=str(payload.get("tempSessionId")) if payload.get("tempSessionId") else None,
        phone_code_hash=phone_code_hash,
        step="sign_in",
        state="awaiting_2fa" if result.get("error") == "2FA_REQUIRED" else "authenticated",
        result=result,
    )

    return _client_auth_result(result)


@router.post("/auth/check-2fa")
async def check_2fa(
    request: Request,
    response: Response,
    workspace: Workspace | None = Depends(get_current_workspace_optional),
    db: AsyncSession = Depends(get_db_session),
):
    """Submit 2FA password after sign-in returned 2FA_REQUIRED."""
    body = await request.json()
    password = body.get("password", "")

    if not password:
        raise HTTPException(status_code=400, detail="password is required")

    payload: dict[str, str | int] = {"password": password}
    if temp_session_id := body.get("tempSessionId"):
        payload["tempSessionId"] = temp_session_id
    if workspace:
        payload["workspaceId"] = workspace.id
    payload = _attach_temp_session_id(request, payload)
    payload = await _augment_payload_with_stored_auth_attempt(
        session=db,
        payload=payload,
        phone=str(body.get("phone") or ""),
        workspace_id=workspace.id if workspace else None,
    )

    result = await _sidecar_post_json("/auth/check-password", payload)
    if result is None:
        await _record_auth_attempt(
            session=db,
            phone=str(body.get("phone") or ""),
            workspace_id=workspace.id if workspace else None,
            temp_session_id=str(payload.get("tempSessionId")) if payload.get("tempSessionId") else None,
            step="check_2fa",
            state="failed",
            error="Sidecar unreachable",
        )
        raise HTTPException(status_code=502, detail="Sidecar unreachable")
    if isinstance(result.get(SIDECAR_STATUS_KEY), int) and result[SIDECAR_STATUS_KEY] >= 400:
        await _record_auth_attempt(
            session=db,
            phone=str(body.get("phone") or ""),
            workspace_id=workspace.id if workspace else None,
            temp_session_id=str(payload.get("tempSessionId")) if payload.get("tempSessionId") else None,
            step="check_2fa",
            state="failed",
            result=result,
            error=_sidecar_error_detail(result),
        )
    _raise_sidecar_error(result)

    if temp_session_id := result.get("tempSessionId"):
        _set_temp_session_cookie(response, str(temp_session_id))
    await _record_auth_attempt(
        session=db,
        phone=str(body.get("phone") or result.get("phone") or ""),
        workspace_id=workspace.id if workspace else None,
        temp_session_id=str(payload.get("tempSessionId")) if payload.get("tempSessionId") else None,
        step="check_2fa",
        state="authenticated",
        result=result,
    )

    return _client_auth_result(result)


@router.get("/auth/attempt-status")
async def telegram_auth_attempt_status(
    request: Request,
    temp_session_id: str | None = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Browser-safe status for the current temporary phone auth attempt."""
    temp_id = temp_session_id or request.cookies.get(TEMP_SESSION_COOKIE)
    if not temp_id:
        raise HTTPException(status_code=400, detail="tempSessionId is required")
    attempt = await _find_auth_attempt(session=db, temp_session_id=temp_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Telegram auth attempt not found")
    return {
        "tempSessionId": attempt.temp_session_id,
        "state": attempt.state,
        "recoveryState": attempt.recovery_state,
        "preferredDeliveryType": attempt.preferred_delivery_type,
        "deliveryDegraded": attempt.delivery_degraded,
        "deliveryDegradedReason": attempt.delivery_degraded_reason,
        "delivery": attempt.delivery_payload,
        "nextRecoveryAt": attempt.next_recovery_at.isoformat() if attempt.next_recovery_at else None,
        "recoveryAttemptCount": attempt.recovery_attempt_count,
        "maxRecoveryAttempts": attempt.max_recovery_attempts,
        "lastError": attempt.last_error,
        "updatedAt": attempt.updated_at.isoformat(),
    }


@router.post("/auth/qr/start")
async def start_qr_auth():
    result = await _sidecar_post_json("/qr-auth/start", {})
    if result is None:
        raise HTTPException(status_code=502, detail="Sidecar unreachable")
    _raise_sidecar_error(result)
    return result


@router.get("/auth/qr/status")
async def get_qr_auth_status():
    result = await _sidecar_get("/qr-auth/status")
    if result is None:
        raise HTTPException(status_code=502, detail="Sidecar unreachable")
    return result


@router.get("/auth/qr/code")
async def get_qr_auth_code():
    result = await _sidecar_get("/qr-auth/code")
    if result is None:
        raise HTTPException(status_code=404, detail="QR code not ready")
    return result


@router.post("/auth/qr/check-2fa")
async def submit_qr_auth_2fa(
    request: Request,
):
    body = await request.json()
    password = body.get("password", "")
    if not password:
        raise HTTPException(status_code=400, detail="password is required")

    result = await _sidecar_post_json("/qr-auth/password", {"password": password})
    if result is None:
        raise HTTPException(status_code=502, detail="Sidecar unreachable")
    _raise_sidecar_error(result)
    return result


@router.post("/start-ingestion")
async def start_ingestion(
    workspace: Workspace = Depends(get_current_workspace_optional),
):
    """Start onboarding Step 1 hydration/classification/voice bridge."""
    if workspace is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    status = await telegram_status(workspace)
    if status.get("state") not in {"connected", "degraded"} or status.get("needsReconnect"):
        raise HTTPException(status_code=400, detail="Telegram not connected")

    return await onboarding_runtime.start_ingestion(workspace)


@router.get("/channels")
async def list_channels(
    workspace: Workspace = Depends(get_current_workspace_optional),
):
    """List seller channels/groups for onboarding channel picker."""
    if workspace is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    data = await _sidecar_get("/channels", params={"workspaceId": workspace.id})
    if not isinstance(data, list):
        return {"channels": [], "count": 0}
    return {"channels": data, "count": len(data)}


@router.post("/scan-channels")
async def scan_channels(
    body: ScanChannelsRequest,
    workspace: Workspace = Depends(get_current_workspace_optional),
    db: AsyncSession = Depends(get_db_session),
):
    """Fetch channel posts and queue them as Business Brain learning sources."""
    if workspace is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not body.channel_ids:
        raise HTTPException(status_code=400, detail="No channels selected")

    progress = await onboarding_runtime.load_progress(
        workspace.id
    ) or onboarding_runtime.default_ingestion_progress(workspace.id)
    await onboarding_runtime.set_progress(
        progress, phase="scanning_channels", percent=70, completed=False
    )

    queued_sources = 0
    queued_source_refs: set[str] = set()
    for index, channel_id in enumerate(body.channel_ids, start=1):
        posts = await _sidecar_get(
            "/channel-posts",
            params={"workspaceId": workspace.id, "channelId": channel_id, "limit": 100},
        )
        if not isinstance(posts, list):
            continue

        source_ref = _telegram_channel_source_ref(
            workspace_id=workspace.id,
            channel_id=channel_id,
        )
        await BusinessBrainMemoryService(
            repository=CommercialSpineRepository(db),
        ).write_memory_fact(
            MemoryFactWriteInput(
                workspace_id=workspace.id,
                fact_id=f"brain:{workspace.id}:telegram-channel:{channel_id}",
                fact_type="business_source_fact",
                entity_ref=f"workspace:source:{source_ref}",
                value={
                    "kind": "telegram_channel",
                    "label": f"Telegram channel {channel_id}",
                    "input": {
                        "channel_id": str(channel_id),
                        "messages": [
                            _source_message_from_channel_post(post, index=i)
                            for i, post in enumerate(posts)
                            if isinstance(post, dict)
                        ],
                        "limit": 100,
                    },
                    "processing": {
                        "state": "queued",
                        "reason": "telegram_channel_waiting_for_source_learning",
                    },
                },
                source_refs=[source_ref, f"telegram_channel:{channel_id}"],
                source="import",
                status="active",
                approval_state="confirmed",
                confidence=1.0,
                risk_tier="low",
                correlation_id=f"telegram-scan:{workspace.id}",
                idempotency_key=f"telegram-scan:{workspace.id}:{channel_id}",
                actor_ref=f"workspace:{workspace.id}",
            )
        )
        await db.commit()
        queued_sources += 1
        queued_source_refs.add(source_ref)
        await onboarding_runtime.notify_event(
            workspace.id,
            {
                "kind": "source_queued",
                "source": "telegram_channel",
                "channel_id": channel_id,
                "posts_count": len(posts),
            },
        )

        await onboarding_runtime.set_progress(
            progress,
            products_extracted=0,
            knowledge_items=0,
            percent=70 + int(25 * index / max(len(body.channel_ids), 1)),
        )

    learning_result = await OnboardingSourceLearningRuntimeService(
        repository=CommercialSpineRepository(db),
        session_factory=async_session,
        max_parallelism=4,
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id=f"telegram-scan-source-learning:{workspace.id}",
        limit=max(queued_sources, 1),
        source_refs=queued_source_refs or None,
        force=True,
    )
    await db.commit()

    await onboarding_runtime.notify_event(
        workspace.id,
        {
            "kind": "scan_complete",
            "queued_sources": queued_sources,
            "processed": learning_result.processed_count,
            "review_ready": learning_result.review_ready_count,
            "retrying": learning_result.retrying_count,
            "failed": learning_result.failed_count,
        },
    )
    await onboarding_runtime.set_progress(
        progress,
        phase="review_learnings" if learning_result.review_ready_count else "awaiting_sources",
        percent=100 if learning_result.review_ready_count else 85,
        completed=False,
    )
    return {
        "status": "learned" if learning_result.review_ready_count else "queued",
        "queued_sources": queued_sources,
        "processed": learning_result.processed_count,
        "review_ready": learning_result.review_ready_count,
        "retrying": learning_result.retrying_count,
        "failed": learning_result.failed_count,
    }
