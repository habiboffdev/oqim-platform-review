from fastapi import APIRouter, Request
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import async_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "oqim-business"}


@router.get("/health/detailed")
async def health_detailed(request: Request):
    result = {
        "status": "ok",
        "database": "unknown",
        "redis": "unknown",
    }

    # Check database
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        result["database"] = "connected"
    except Exception:
        result["database"] = "error"
        result["status"] = "degraded"

    # Check Redis — prefer app.state clients, fall back to one-shot connection
    try:
        import redis.asyncio as aioredis

        app_redis = getattr(request.app.state, "app_redis", None)
        if app_redis:
            await app_redis.ping()
        else:
            settings = get_settings()
            r = aioredis.from_url(settings.redis_url)
            await r.ping()
            await r.aclose()
        result["redis"] = "connected"
    except Exception:
        result["redis"] = "error"
        result["status"] = "degraded"

    # Consumer liveness
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor:
        consumer_health = supervisor.health_report()
        result["consumers"] = consumer_health
        if not supervisor.is_healthy():
            result["status"] = "degraded"

    from app.api.routes.ws import manager as ws_manager
    result["ws_subscriber"] = (
        "running"
        if ws_manager._subscriber_task and not ws_manager._subscriber_task.done()
        else "stopped"
    )

    # Dead-letter stream length — non-zero means messages are being lost
    try:
        app_redis = getattr(request.app.state, "app_redis", None)
        if app_redis:
            dl_len = await app_redis.xlen("oqim:deadletter")
            result["dead_letters"] = dl_len
            if dl_len > 0:
                result["status"] = "degraded"
    except Exception:
        result["dead_letters"] = "unknown"

    return result
