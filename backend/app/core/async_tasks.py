from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any


def spawn_guarded_task(
    awaitable: Awaitable[Any],
    *,
    logger: logging.Logger,
    name: str,
    registry: set[asyncio.Task] | None = None,
) -> asyncio.Task:
    """Create a background task that always reports exceptions.

    This keeps fire-and-forget work observable and lets callers optionally
    track the task for shutdown/cancellation.
    """
    task = asyncio.create_task(awaitable, name=name)
    if registry is not None:
        registry.add(task)

    def _on_done(done_task: asyncio.Task) -> None:
        if registry is not None:
            registry.discard(done_task)
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Failed to inspect background task '%s'", name)
            return

        if exc is not None:
            logger.error(
                "Background task '%s' failed",
                name,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    task.add_done_callback(_on_done)
    return task
