"""amoCRM per-workspace OAuth: connect a workspace's amoCRM account.

Mirrors the Instagram connect flow (HMAC state + session-binding on callback)
but persists a typed ``CrmConnection`` row (creds off the hot ``workspaces``
row) and reads the account's pipelines on connect to build the default
role->stage map. The account host (subdomain) only arrives in the callback's
``referer`` param — we never guess it.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import (
    get_current_workspace,
    get_current_workspace_optional,
    get_db_session,
)
from app.core.logging import get_logger
from app.models.crm_connection import CrmConnection
from app.models.workspace import Workspace
from app.modules.crm_connector.contracts import (
    CrmAuthError,
    CrmOAuthCallback,
    CrmUnauthorizedError,
)
from app.modules.crm_connector.factory import provider_for
from app.modules.crm_connector.stage_map import default_mapping

router = APIRouter(prefix="/amocrm/auth", tags=["amocrm-auth"])
logger = get_logger("api.amocrm_auth")

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]

_STATE_TTL_SECONDS = 15 * 60


def _state_signature(workspace_id: int, expires_at: int, nonce: str) -> str:
    secret = get_settings().secret_key
    message = f"amocrm-oauth:{workspace_id}:{expires_at}:{nonce}"
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
async def amocrm_auth_start(workspace: WorkspaceDep) -> dict[str, str]:
    settings = get_settings()
    if not settings.amocrm_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="amocrm_not_configured",
        )
    url = provider_for("amocrm").oauth_authorize_url(
        state=_state_for(workspace.id), redirect_uri=settings.amocrm_redirect_uri
    )
    return {"authorize_url": url}


async def exchange_and_store_amocrm_connection(
    session: AsyncSession,
    *,
    workspace: Workspace,
    code: str,
    referer: str,
    http_client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> None:
    """code+referer -> tokens -> pipelines -> persist an active CrmConnection.

    Atomic: a pipelines-fetch failure aborts before persisting a half-configured
    connection. Replacing this workspace's existing active connection is part of
    the same flush (the partial unique index allows only one active row).
    """
    provider = provider_for("amocrm", http_client_factory=http_client_factory)
    boot = await provider.oauth_exchange(
        CrmOAuthCallback(code=code, raw_params={"referer": referer})
    )
    conn_like = SimpleNamespace(
        provider_account_ref=boot.provider_account_ref,
        access_token=boot.tokens.access_token,
    )
    schema = await provider.discover_schema(conn_like)
    pipeline_config = default_mapping(schema)

    # One external amoCRM account belongs to exactly one workspace.
    existing_other = (
        await session.execute(
            select(CrmConnection).where(
                CrmConnection.provider == "amocrm",
                CrmConnection.provider_account_ref == boot.provider_account_ref,
                CrmConnection.status == "active",
                CrmConnection.workspace_id != workspace.id,
            ).limit(1)
        )
    ).scalars().first()
    if existing_other is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="amocrm_account_already_connected",
        )

    # Retire this workspace's prior active connection (kept for audit).
    prior = (
        await session.execute(
            select(CrmConnection).where(
                CrmConnection.workspace_id == workspace.id,
                CrmConnection.status == "active",
            )
        )
    ).scalars().all()
    for conn in prior:
        conn.status = "disconnected"
    await session.flush()

    session.add(
        CrmConnection(
            workspace_id=workspace.id,
            provider="amocrm",
            status="active",
            provider_account_ref=boot.provider_account_ref,
            access_token=boot.tokens.access_token,
            refresh_token=boot.tokens.refresh_token,
            token_expires_at=boot.tokens.expires_at,
            webhook_token=secrets.token_urlsafe(32),
            pipeline_config=pipeline_config,
        )
    )
    await session.flush()


@router.get("/callback")
async def amocrm_auth_callback(
    session: SessionDep,
    current_workspace: Annotated[Workspace | None, Depends(get_current_workspace_optional)],
    code: str = "",
    state: str = "",
    referer: str = "",
    error: str = "",
) -> RedirectResponse:
    if error or not code:
        safe_error = (error or "missing_code").replace("\n", " ").replace("\r", " ")[:120]
        logger.warning("amocrm oauth callback error=%s", safe_error)
        return RedirectResponse(url="/integrations?amocrm=error")
    workspace_id = _workspace_id_from_state(state)
    if workspace_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid_state")
    if current_workspace is None or current_workspace.id != workspace_id:
        # The browser completing the flow must be logged into the SAME workspace
        # that started it (account-binding CSRF).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="state_session_mismatch"
        )
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace_not_found")
    try:
        await exchange_and_store_amocrm_connection(
            session, workspace=workspace, code=code, referer=referer
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_409_CONFLICT:
            return RedirectResponse(url="/integrations?amocrm=already_connected")
        logger.error("amocrm connect failed for workspace=%s detail=%s", workspace_id, exc.detail)
        return RedirectResponse(url="/integrations?amocrm=error")
    except (CrmAuthError, CrmUnauthorizedError, httpx.HTTPError) as exc:
        # No exc_info: amoCRM errors can embed request bodies (secret/tokens).
        # CrmUnauthorizedError: a 401 mid-discovery (the connect path does 5 reads)
        # must redirect to the error page, not escape as an unhandled 500.
        logger.error(
            "amocrm token exchange failed for workspace=%s error=%s",
            workspace_id,
            type(exc).__name__,
        )
        return RedirectResponse(url="/integrations?amocrm=error")
    await session.commit()
    return RedirectResponse(url="/integrations?amocrm=connected")


async def _active_connection(session: AsyncSession, workspace_id: int) -> CrmConnection | None:
    return (
        await session.execute(
            select(CrmConnection).where(
                CrmConnection.workspace_id == workspace_id,
                CrmConnection.status == "active",
            ).limit(1)
        )
    ).scalars().first()


@router.get("/status")
async def amocrm_auth_status(workspace: WorkspaceDep, session: SessionDep) -> dict[str, Any]:
    conn = await _active_connection(session, workspace.id)
    if conn is None:
        return {
            "connected": False,
            "provider_account_ref": None,
            "expires_at": None,
            "needs_reconnect": False,
        }
    expires_at = conn.token_expires_at
    now = datetime.now(UTC)
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return {
        "connected": True,
        "provider_account_ref": conn.provider_account_ref,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "needs_reconnect": bool(expires_at is not None and expires_at <= now),
    }


@router.post("/disconnect")
async def amocrm_auth_disconnect(workspace: WorkspaceDep, session: SessionDep) -> dict[str, str]:
    conns = (
        await session.execute(
            select(CrmConnection).where(
                CrmConnection.workspace_id == workspace.id,
                CrmConnection.status == "active",
            )
        )
    ).scalars().all()
    for conn in conns:
        conn.status = "disconnected"
    await session.commit()
    return {"status": "disconnected"}
