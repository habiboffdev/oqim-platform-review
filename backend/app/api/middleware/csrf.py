from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/bridge-login",
    "/health",
}

class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in SAFE_METHODS:
            return await call_next(request)

        # Skip CSRF for non-cookie auth (Bearer token)
        if "authorization" in request.headers:
            return await call_next(request)

        # Skip exempt paths
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # If no session cookie, skip CSRF (not a cookie-authenticated request)
        if "oqim_session" not in request.cookies:
            return await call_next(request)

        # Validate double-submit: cookie value must match header
        cookie_csrf = request.cookies.get("oqim_csrf", "")
        header_csrf = request.headers.get("x-csrf-token", "")

        if not cookie_csrf or cookie_csrf != header_csrf:
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF validation failed"},
            )

        return await call_next(request)
