"""Local cache for media bytes already downloaded during hydration.

The reply turn perceives media natively by reusing THESE bytes instead of
re-downloading from the sidecar at dispatch — the second fetch is fragile
(2026-06-13 voice CLIENT_ABORTED incident: the sidecar aborted a re-download
mid-stream while reconnecting) and doubles per-message download load. Hydration
already holds the bytes to transcribe/describe; we stash them here so dispatch
reads them locally with zero sidecar round-trip.

Single-VM filesystem cache (same process + cwd as the hydration worker and the
dispatcher). A shared store (Redis/blob) would be needed only if the API scales
to multiple instances — hydration on instance A then dispatch on instance B
would miss this cache and fall back to the text caption (still correct, just not
native). Best-effort throughout: never raises into hydration or dispatch.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PERCEPTION_CACHE_DIR = Path("./media_cache/perception")
# Lifetime only needs to span hydration -> turn dispatch (seconds), plus headroom
# for turn retries/revisions. Kept short so the cache stays small.
PERCEPTION_CACHE_TTL_SECONDS = 30 * 60
PERCEPTION_CACHE_MAX_FILES_PER_WORKSPACE = 500


def _path(workspace_id: int, message_id: int) -> Path:
    return PERCEPTION_CACHE_DIR / str(workspace_id) / str(message_id)


def write_perception_bytes(workspace_id: int, message_id: int, data: bytes) -> None:
    """Stash media bytes for the reply turn. Best-effort; never raises."""
    if not data:
        return
    try:
        path = _path(workspace_id, message_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic publish — dispatch never reads a partial file
        _evict_workspace(path.parent)
    except Exception as exc:
        logger.info("perception_cache_write_failed msg=%s err=%s", message_id, exc)


def read_perception_bytes(workspace_id: int, message_id: int) -> bytes | None:
    """Return cached bytes if present and fresh, else None (caller falls back)."""
    path = _path(workspace_id, message_id)
    try:
        if not path.exists():
            return None
        if (time.time() - path.stat().st_mtime) > PERCEPTION_CACHE_TTL_SECONDS:
            path.unlink(missing_ok=True)
            return None
        return path.read_bytes()
    except Exception as exc:
        logger.info("perception_cache_read_failed msg=%s err=%s", message_id, exc)
        return None


def _evict_workspace(ws_dir: Path) -> None:
    """Drop expired files and cap the per-workspace file count (newest kept)."""
    try:
        now = time.time()
        files = [p for p in ws_dir.iterdir() if p.is_file() and not p.name.endswith(".tmp")]
        for p in files:
            if (now - p.stat().st_mtime) > PERCEPTION_CACHE_TTL_SECONDS:
                p.unlink(missing_ok=True)
        live = sorted(
            (p for p in ws_dir.iterdir() if p.is_file() and not p.name.endswith(".tmp")),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(live) - PERCEPTION_CACHE_MAX_FILES_PER_WORKSPACE
        for p in live[: max(0, excess)]:
            p.unlink(missing_ok=True)
    except Exception:
        pass
