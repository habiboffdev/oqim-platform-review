from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


def _create_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        future=True,
        # Right-sized for a Postgres shared across this process + in-process
        # consumers + the test DB. pool_size is the count of connections kept
        # open PERSISTENTLY (not a ceiling), so 50 made one process hoard ~50
        # idle connections and burst to 100 (50+overflow) — half of local PG's
        # max_connections=200, and ALL of a prod default of 100. 10 kept + 20
        # burst = 30 max/process: ample for single-owner concurrency, but a
        # process can no longer monopolise the cluster.
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=1800,
        # Liveness-check a pooled connection before handing it out, so stale
        # sockets (left by sidecar/network blips or a Postgres restart) are
        # transparently discarded instead of surfacing as a mid-request error.
        pool_pre_ping=True,
        # Safety net: Postgres auto-aborts a transaction left idle (no query
        # running) for >2 min. Without it, a session held open across a hung/slow
        # LLM call holds its locks indefinitely — observed one idle-in-transaction
        # held 1h+ while 29 business_brain_updates INSERTs blocked behind its lock
        # and exhausted the whole pool. 2 min is well above a normal LLM hold
        # (<45s) so it only kills genuinely stuck transactions.
        connect_args={
            "server_settings": {"idle_in_transaction_session_timeout": "120000"}
        },
    )


engine = _create_engine()
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
