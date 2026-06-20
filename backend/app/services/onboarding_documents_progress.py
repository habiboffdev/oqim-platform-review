"""Redis store for document-generation progress during onboarding.

Tracks which doc/section the streaming orchestrator is currently generating.
The live pointer is overlaid on top of persisted sections in the projection.
"""

from __future__ import annotations

import json

from app.services.onboarding_runtime import get_redis

DOCGEN_KEY = "onboarding:docgen:{workspace_id}"  # JSON value, TTL 3600s
_TTL = 3600


def default_docgen_progress() -> dict:
    return {
        "running": False,
        "current_doc": None,
        "current_section": None,
        "error": None,
        "skill_status": "pending",
        "skill_candidates": 0,
    }


async def load_docgen_progress(workspace_id: int) -> dict:
    """Return stored doc-gen progress merged over defaults.

    Missing key → default_docgen_progress().
    Present key → stored dict merged over defaults so missing keys fall back.
    """
    r = await get_redis()
    try:
        data = await r.get(DOCGEN_KEY.format(workspace_id=workspace_id))
    finally:
        await r.aclose()

    if not data:
        return default_docgen_progress()

    stored = json.loads(data)
    merged = default_docgen_progress()
    merged.update(stored)
    return merged


async def store_docgen_progress(workspace_id: int, payload: dict) -> None:
    """Persist doc-gen progress with a 3600s TTL."""
    r = await get_redis()
    try:
        await r.setex(
            DOCGEN_KEY.format(workspace_id=workspace_id),
            _TTL,
            json.dumps(payload),
        )
    finally:
        await r.aclose()
