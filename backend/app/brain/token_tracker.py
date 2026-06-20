"""Token usage tracking per workspace per operation.

Stores daily token counts in Redis hashes keyed by workspace + date.
Records are kept for 30 days and broken down by operation and provider.

Usage:
    # Set context at the start of a pipeline:
    set_token_context(workspace_id=1, operation="agent_turn_generation")

    # All generate_with_fallback calls within this async context auto-track.
    # No need to pass workspace_id/operation to each call.
"""
import contextvars
import logging
from datetime import date

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Async context vars — set once per pipeline, read by the tracker
_ctx_workspace_id: contextvars.ContextVar[int | None] = contextvars.ContextVar("token_workspace_id", default=None)
_ctx_operation: contextvars.ContextVar[str | None] = contextvars.ContextVar("token_operation", default=None)


def set_token_context(workspace_id: int, operation: str) -> None:
    """Set workspace + operation for token tracking in this async context."""
    _ctx_workspace_id.set(workspace_id)
    _ctx_operation.set(operation)


def get_token_context() -> tuple[int | None, str | None]:
    """Get current token tracking context."""
    return _ctx_workspace_id.get(None), _ctx_operation.get(None)


class TokenTracker:
    """Tracks LLM token usage in Redis hashes, keyed by workspace + day."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def record(
        self,
        workspace_id: int,
        operation: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record token usage for an operation."""
        key = f"tokens:{workspace_id}:{date.today().isoformat()}"
        try:
            await self.redis.hincrby(key, f"{operation}:{provider}:input", input_tokens)
            await self.redis.hincrby(key, f"{operation}:{provider}:output", output_tokens)
            await self.redis.expire(key, 86400 * 30)  # keep 30 days
        except Exception as e:
            logger.warning(f"Token tracking failed: {e}")

    async def record_operation(self, workspace_id: int, operation: str) -> int:
        """Increment the daily invocation counter for an operation.

        Separate from record() so per-operation caps can use a simple counter
        without requiring token metadata. Returns the new count.
        """
        key = f"ops:{workspace_id}:{date.today().isoformat()}"
        try:
            new_count = await self.redis.hincrby(key, operation, 1)
            await self.redis.expire(key, 86400 * 2)
            return int(new_count)
        except Exception as e:
            logger.warning(f"Operation counter failed: {e}")
            return 0

    async def get_operation_count(self, workspace_id: int, operation: str) -> int:
        """Return today's invocation count for a given operation."""
        key = f"ops:{workspace_id}:{date.today().isoformat()}"
        try:
            raw = await self.redis.hget(key, operation)
            return int(raw) if raw else 0
        except Exception:
            return 0

    async def get_daily_usage(self, workspace_id: int, day: date | None = None) -> dict:
        """Get token usage breakdown for a workspace on a given day."""
        day = day or date.today()
        key = f"tokens:{workspace_id}:{day.isoformat()}"
        try:
            raw = await self.redis.hgetall(key)
            return {k.decode() if isinstance(k, bytes) else k: int(v) for k, v in raw.items()} if raw else {}
        except Exception:
            return {}


# ── Module-level singleton ──

_token_tracker: TokenTracker | None = None


def init_token_tracker(redis: Redis) -> None:
    """Initialize the module-level token tracker. Call during app startup."""
    global _token_tracker
    _token_tracker = TokenTracker(redis)
    logger.info("TokenTracker initialized")


def get_token_tracker() -> TokenTracker | None:
    """Return the module-level tracker, or None if not yet initialized."""
    return _token_tracker
