from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


IFRAME_ALLOWED_PATHS = {"/api/telegram/web-session"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.path in IFRAME_ALLOWED_PATHS:
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        else:
            response.headers["X-Frame-Options"] = "DENY"
        return response
