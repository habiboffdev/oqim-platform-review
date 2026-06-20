"""Instagram Login OAuth: connect a workspace's IG professional account.

No Facebook Page required (the modern 2024+ path). The long-lived token
lives 60 days; backend/app/services/instagram_token_refresher.py refreshes it.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import (
    get_current_workspace,
    get_current_workspace_optional,
    get_db_session,
)
from app.core.logging import get_logger
from app.models.workspace import Workspace
from app.services.instagram_channel_adapter import GRAPH_VERSION

router = APIRouter(prefix="/instagram/auth", tags=["instagram-auth"])
logger = get_logger("api.instagram_auth")

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]

_AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
_SHORT_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
_SCOPES = ",".join(
    [
        "instagram_business_basic",
        "instagram_business_manage_messages",
        "instagram_business_manage_comments",
    ]
)


# State = workspace_id.expires_at.nonce.signature. The nonce is single-TTL
# but not single-USE (strict one-time-use needs server-side storage);
# session-binding on /callback + the 15-min TTL closes the practical
# account-binding CSRF attack for the pilot.
_STATE_TTL_SECONDS = 15 * 60


def _state_signature(workspace_id: int, expires_at: int, nonce: str) -> str:
    secret = get_settings().secret_key
    message = f"ig-oauth:{workspace_id}:{expires_at}:{nonce}"
    return hmac.new(secret.encode("utf-8"), message.encode(), hashlib.sha256).hexdigest()[:32]


def _state_for(workspace_id: int) -> str:
    expires_at = int(time.time()) + _STATE_TTL_SECONDS
    nonce = secrets.token_urlsafe(16)
    return f"{workspace_id}.{expires_at}.{nonce}.{_state_signature(workspace_id, expires_at, nonce)}"


def _workspace_id_from_state(state: str) -> int | None:
    parts = state.split(".", 3)
    if len(parts) != 4:
        return None
    raw_id, raw_expires, nonce, digest = parts
    try:
        workspace_id = int(raw_id)
        expires_at = int(raw_expires)
    except ValueError:
        return None
    if time.time() > expires_at:
        return None
    expected = _state_signature(workspace_id, expires_at, nonce)
    if not hmac.compare_digest(digest, expected):
        return None
    return workspace_id


@router.get("/start")
async def instagram_auth_start(workspace: WorkspaceDep) -> dict[str, str]:
    settings = get_settings()
    if not settings.instagram_app_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="instagram_not_configured",
        )
    params = urlencode(
        {
            "client_id": settings.instagram_app_id,
            "redirect_uri": settings.instagram_redirect_uri,
            "scope": _SCOPES,
            "response_type": "code",
            "state": _state_for(workspace.id),
        }
    )
    return {"authorize_url": f"{_AUTHORIZE_URL}?{params}"}


async def exchange_and_store_instagram_token(
    db: AsyncSession,
    *,
    workspace: Workspace,
    code: str,
    http_client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> None:
    """code -> short token -> long-lived token -> /me -> persist + subscribe."""
    settings = get_settings()
    graph_base = settings.instagram_graph_base.rstrip("/")
    async with http_client_factory(timeout=20.0) as client:
        short_response = await client.post(
            _SHORT_TOKEN_URL,
            data={
                "client_id": settings.instagram_app_id,
                "client_secret": settings.instagram_app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": settings.instagram_redirect_uri,
                "code": code,
            },
        )
        short_response.raise_for_status()
        short_token = str(short_response.json().get("access_token") or "")
        if not short_token:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="instagram_short_token_missing",
            )

        # The short->long exchange keeps the token in query params: that is
        # Meta's documented interface for this endpoint (the token IS the
        # subject of the exchange, not a credential for another resource).
        long_response = await client.get(
            f"{graph_base}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.instagram_app_secret,
                "access_token": short_token,
            },
        )
        long_response.raise_for_status()
        long_payload = long_response.json()
        long_token = str(long_payload.get("access_token") or "")
        if not long_token:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="instagram_long_token_missing",
            )
        expires_in = int(long_payload.get("expires_in") or 5_184_000)

        # Bearer header, not query param — query strings land in proxy/server logs.
        # Fetch BOTH ids: Instagram-Login exposes user_id and id for one account
        # and Meta's docs don't say which the webhook entry.id carries, so we
        # store both and the webhook resolver matches either.
        me_response = await client.get(
            f"{graph_base}/{GRAPH_VERSION}/me",
            params={"fields": "user_id,id,username"},
            headers={"Authorization": f"Bearer {long_token}"},
        )
        me_response.raise_for_status()
        me_payload = me_response.json()
        ig_user_id = str(me_payload.get("user_id") or "")
        ig_id = str(me_payload.get("id") or "")
        if not ig_user_id:
            # NEVER persist an empty page_id: the webhook resolver matches on
            # instagram_page_id, and a malformed webhook entry with no id
            # would resolve to this workspace.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="instagram_account_id_missing",
            )

        # One IG account belongs to exactly one workspace — webhook routing
        # resolves by either id and must never be ambiguous. Guard both id
        # spaces against both columns so a reconnect under the other id is
        # still recognised as the same account.
        candidate_ids = [v for v in (ig_user_id, ig_id) if v]
        existing = (
            await db.execute(
                select(Workspace)
                .where(
                    or_(
                        Workspace.instagram_page_id.in_(candidate_ids),
                        Workspace.instagram_account_id.in_(candidate_ids),
                    ),
                    Workspace.id != workspace.id,
                )
                .limit(1)
            )
        ).scalars().first()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="instagram_account_already_connected",
            )

        workspace.instagram_connected = True
        workspace.instagram_access_token = long_token
        workspace.instagram_page_id = ig_user_id
        workspace.instagram_account_id = ig_id or None
        workspace.instagram_token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        await db.flush()

        subscribe_response = await client.post(
            f"{graph_base}/{GRAPH_VERSION}/me/subscribed_apps",
            params={"subscribed_fields": "messages,comments"},
            headers={"Authorization": f"Bearer {long_token}"},
        )
        subscribe_response.raise_for_status()


@router.get("/callback")
async def instagram_auth_callback(
    session: SessionDep,
    current_workspace: Annotated[Workspace | None, Depends(get_current_workspace_optional)],
    code: str = "",
    state: str = "",
    error: str = "",
) -> RedirectResponse:
    if error or not code:
        # error is attacker-controlled query input — strip newlines (log
        # injection) and cap length before logging.
        safe_error = (error or "missing_code").replace("\n", " ").replace("\r", " ")[:120]
        logger.warning("instagram oauth callback error=%s", safe_error)
        return RedirectResponse(url="/integrations?instagram=error")
    workspace_id = _workspace_id_from_state(state)
    if workspace_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid_state")
    if current_workspace is None or current_workspace.id != workspace_id:
        # The browser completing the flow must be logged into the SAME
        # workspace that initiated it — otherwise an attacker-crafted
        # authorize link could bind a victim's IG account to the
        # attacker's workspace (account-binding CSRF). The oqim_session
        # cookie IS sent on this top-level GET redirect (SameSite=Lax).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="state_session_mismatch"
        )
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace_not_found")
    try:
        await exchange_and_store_instagram_token(session, workspace=workspace, code=code)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_409_CONFLICT:
            return RedirectResponse(url="/integrations?instagram=already_connected")
        # A browser is on the other end of this redirect — never surface raw
        # JSON errors; log the detail and send it to the integrations page.
        logger.error(
            "instagram connect failed for workspace=%s detail=%s", workspace_id, exc.detail
        )
        return RedirectResponse(url="/integrations?instagram=error")
    except httpx.HTTPError as exc:
        # No exc_info: httpx errors embed the full request URL (token included).
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        logger.error(
            "instagram token exchange failed for workspace=%s error=%s status=%s",
            workspace_id,
            type(exc).__name__,
            status_code,
        )
        return RedirectResponse(url="/integrations?instagram=error")
    await session.commit()
    return RedirectResponse(url="/integrations?instagram=connected")


@router.get("/status")
async def instagram_auth_status(workspace: WorkspaceDep) -> dict[str, Any]:
    expires_at = workspace.instagram_token_expires_at
    now = datetime.now(UTC)
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    needs_reconnect = bool(
        workspace.instagram_connected and expires_at is not None and expires_at <= now
    )
    return {
        "connected": bool(workspace.instagram_connected),
        "page_id": workspace.instagram_page_id,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "needs_reconnect": needs_reconnect,
    }


def _clear_instagram_connection(workspace: Workspace) -> None:
    """Full disconnect: both ids MUST be cleared — the webhook resolver and the
    409 duplicate guard look up by instagram_page_id AND instagram_account_id,
    so a stale id would route inbound events to a disconnected workspace and
    block the IG account from ever connecting elsewhere.
    """
    workspace.instagram_connected = False
    workspace.instagram_access_token = None
    workspace.instagram_token_expires_at = None
    workspace.instagram_page_id = None
    workspace.instagram_account_id = None


@router.post("/disconnect")
async def instagram_auth_disconnect(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, str]:
    _clear_instagram_connection(workspace)
    await session.commit()
    return {"status": "disconnected"}
