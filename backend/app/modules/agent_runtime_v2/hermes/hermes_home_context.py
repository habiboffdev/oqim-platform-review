"""Per-workspace HERMES_HOME for the OWNER plane (run-model X, spike #439).

The seller plane runs Hermes thin (`skip_context_files=True`) and never reads
HERMES_HOME. The owner/setup plane goes Hermes-native: each workspace gets its
OWN HERMES_HOME so its file-drop SKILL.md skills (and config.yaml for MCP) are
isolated from every other workspace.

The mechanism is a contextvar, NOT `os.environ["HERMES_HOME"]`: the env var is
process-global and unsafe under the concurrent multi-tenant FastAPI service.
`vendor_patches._install_hermes_home_resolver` wraps Hermes's `get_hermes_home`
to read this contextvar first (falling back to the global default when unset, so
the seller path is byte-identical). Because the owner engine run dispatches via
`asyncio.to_thread` (engine.py), which copies the calling context into the worker
thread, a value set here before the run is visible to all of Hermes's
runtime `get_hermes_home()` calls (skill discovery, SOUL.md, config.yaml).
"""

from __future__ import annotations

import contextvars
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# None => fall back to Hermes's global default (the seller path never sets this).
current_hermes_home: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "oqim_hermes_home", default=None
)


@contextmanager
def use_hermes_home(home: Path | str) -> Iterator[Path]:
    """Scope a per-workspace HERMES_HOME for the duration of an owner run.

    Wraps an ``await``: the contextvar stays set across the awaited engine call
    (same asyncio task) and is copied into the ``asyncio.to_thread`` worker where
    Hermes loads skills/context files.
    """
    path = Path(home)
    token = current_hermes_home.set(path)
    try:
        yield path
    finally:
        current_hermes_home.reset(token)


def workspace_hermes_homes_base() -> Path:
    """Root that holds every workspace's HERMES_HOME (`<base>/ws-{id}`)."""
    override = os.environ.get("OQIM_HERMES_HOMES_BASE", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".oqim-hermes-homes"


def workspace_hermes_home(workspace_id: int) -> Path:
    """The HERMES_HOME directory for one workspace's owner/setup agent."""
    return workspace_hermes_homes_base() / f"ws-{int(workspace_id)}"


def ensure_workspace_hermes_home(workspace_id: int) -> Path:
    """Create (idempotently) the workspace HERMES_HOME + its skills/ dir."""
    home = workspace_hermes_home(workspace_id)
    (home / "skills").mkdir(parents=True, exist_ok=True)
    return home
