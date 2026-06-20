import os

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse


def _get_workspace_id(request: Request) -> str:
    """Extract workspace ID from cookie JWT for rate limiting key."""
    from app.core.security import verify_token
    token = request.cookies.get("oqim_session")
    if token:
        sub = verify_token(token)
        if sub:
            return f"ws:{sub}"
    return get_remote_address(request)


# Use Redis in production, in-memory in dev/test
_env = os.environ.get("APP_ENV", "development")
_redis_url = os.environ.get("REDIS_URL", "redis://localhost:6381/0")
_auth_storage = _redis_url if _env in ("staging", "production") else "memory://"

# Auth tier: Redis-backed in prod (survives restarts), in-memory in dev/test
auth_limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_auth_storage,
)

# General tier: always in-memory
general_limiter = Limiter(
    key_func=_get_workspace_id,
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}"},
        headers={"Retry-After": "60"},
    )
