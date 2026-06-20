from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.correlation import CorrelationIdMiddleware, current_correlation_id


async def _ping(_request):
    return JSONResponse({"correlation_id": current_correlation_id()})


def _client() -> TestClient:
    app = Starlette(routes=[Route("/ping", _ping)])
    app.add_middleware(CorrelationIdMiddleware)
    return TestClient(app)


def test_correlation_middleware_generates_and_exposes_id() -> None:
    response = _client().get("/ping")

    correlation_id = response.headers["x-correlation-id"]
    assert len(correlation_id) == 32
    assert response.json() == {"correlation_id": correlation_id}


def test_correlation_middleware_preserves_incoming_id() -> None:
    response = _client().get("/ping", headers={"x-correlation-id": "turn-123"})

    assert response.headers["x-correlation-id"] == "turn-123"
    assert response.json() == {"correlation_id": "turn-123"}
