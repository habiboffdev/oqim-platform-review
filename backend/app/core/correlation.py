"""Correlation ID propagation.

Every inbound HTTP request gets a UUID that rides in a contextvar so all logs,
DB writes, event bus publishes, and jobs spawned from the request share it.

Grepping one correlation_id then shows the full story of a single message or
action across the whole system.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

HEADER_NAME = "x-correlation-id"

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def current_correlation_id() -> str | None:
    """Read the correlation ID for the current async task, if any."""
    return _correlation_id.get()


def set_correlation_id(value: str | None) -> None:
    """Set the correlation ID for the current async task."""
    _correlation_id.set(value)


def new_correlation_id() -> str:
    """Generate a fresh UUID4 hex string."""
    return uuid.uuid4().hex


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Stamp every inbound request with a correlation ID.

    Picks up ``X-Correlation-ID`` if present, otherwise mints a new UUID. Writes
    it back in the response header so clients can quote it when reporting bugs.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        incoming = request.headers.get(HEADER_NAME)
        cid = incoming if incoming else new_correlation_id()
        token = _correlation_id.set(cid)
        try:
            response: Response = await call_next(request)
        finally:
            _correlation_id.reset(token)
        response.headers[HEADER_NAME] = cid
        return response


class CorrelationLogFilter(logging.Filter):
    """Inject ``correlation_id`` into every LogRecord from the contextvar.

    Downstream formatters can reference ``%(correlation_id)s`` without each
    call site remembering to pass it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get() or "-"
        return True
