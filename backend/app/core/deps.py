from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import verify_token
from app.db.session import async_session
from app.models.workspace import Workspace

security = HTTPBearer(auto_error=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


def get_settings_dep() -> Settings:
    return get_settings()


def _extract_token(request: Request, bearer: HTTPAuthorizationCredentials | None) -> str | None:
    """Extract JWT from cookie first, then fall back to Authorization header."""
    token = request.cookies.get("oqim_session")
    if token:
        return token
    if bearer:
        return bearer.credentials
    return None


async def get_current_workspace(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(security),
    session: AsyncSession = Depends(get_db_session),
) -> Workspace:
    """Authenticate workspace via cookie or Bearer token."""
    token = _extract_token(request, bearer)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    sub = verify_token(token)
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    workspace_id = int(sub)
    workspace = await session.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )

    return workspace


async def get_app_redis(request: Request) -> aioredis.Redis:
    return request.app.state.app_redis


async def get_current_workspace_optional(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(security),
    session: AsyncSession = Depends(get_db_session),
) -> Workspace | None:
    """Optionally authenticate workspace. Returns None if not authenticated."""
    token = _extract_token(request, bearer)
    if not token:
        return None

    sub = verify_token(token)
    if not sub:
        return None

    workspace_id = int(sub)
    return await session.get(Workspace, workspace_id)


async def verify_sidecar_key(request: Request) -> None:
    """Verify X-Sidecar-Key header for service-to-service auth."""
    settings = get_settings()
    if not settings.sidecar_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SIDECAR_API_KEY not configured",
        )
    if request.headers.get("X-Sidecar-Key") != settings.sidecar_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid sidecar key",
        )


def get_conversation_turn_runner(request: Request):
    """Get the conversation turn runner from app state."""
    return request.app.state.conversation_turn_runner


def get_delivery_service(request: Request):
    """Get unified delivery service from app state."""
    return request.app.state.delivery
