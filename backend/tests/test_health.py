"""
Health endpoint tests — basic and detailed health checks (no auth required).
"""

from httpx import AsyncClient


class TestHealthCheck:
    async def test_basic_health_returns_ok(self, client: AsyncClient):
        """GET /health should return ok status without auth."""
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["service"] == "oqim-business"

    async def test_no_auth_required(self, client: AsyncClient):
        """Health endpoints must work without any Authorization header."""
        res = await client.get("/health")
        assert res.status_code == 200
        assert "status" in res.json()


class TestHealthDetailed:
    async def test_detailed_returns_database_status(self, client: AsyncClient):
        """GET /health/detailed should report database and redis status."""
        res = await client.get("/health/detailed")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ("ok", "degraded")
        assert data["database"] in ("connected", "error")
        assert data["redis"] in ("connected", "error")

    async def test_detailed_no_auth_required(self, client: AsyncClient):
        """Detailed health check must not require authentication."""
        res = await client.get("/health/detailed")
        assert res.status_code == 200
        assert "database" in res.json()
        assert "redis" in res.json()

    async def test_detailed_has_no_gateway_redis(self, client: AsyncClient):
        """Health must not check gateway_redis — gateway was removed (#75)."""
        res = await client.get("/health/detailed")
        data = res.json()
        assert "gateway_redis" not in data
        # Remaining fields still present
        assert "database" in data
        assert "redis" in data
