"""Stage the current turn's media as native Gemini parts (Slice 1: photo + voice,
Telegram only) by REUSING the bytes hydration already cached — NOT a second
sidecar fetch (which is fragile and doubles download load; see the 2026-06-13
voice CLIENT_ABORTED incident and media_perception_cache). On a cache miss or any
problem this returns nothing — the agent still gets the existing describe-to-text
caption, so the reply never blocks or fails.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.brain.media_parts import TurnMediaPart
from app.services.media_perception_cache import read_perception_bytes

logger = logging.getLogger(__name__)

# Slice 1 perception map. Slice 2/3 extend this (sticker/video_note/gif/video)
# and lift it into a per-agent MediaPerceptionPolicy on RuntimeProfile.
PERCEPTION_KIND: dict[str, str] = {
    "photo": "vision",
    "sticker": "vision",  # static .webp; animated .tgs / video .webm skip via mime guard
    "voice": "audio",
    "audio": "audio",
}

# Gemini parts must match their kind: vision needs an image, audio needs audio.
# Guards out non-image stickers (.tgs gzip / .webm video) -> text placeholder.
_KIND_MIME_PREFIX = {"vision": "image/", "audio": "audio/"}

# Fallback mime when hydration did not record one on the message metadata.
_DEFAULT_MIME = {"photo": "image/jpeg", "voice": "audio/ogg", "audio": "audio/ogg"}

# Inline ceiling. Gemini's File API kicks in above 20 MB (Slice 3); for Slice 1
# anything larger than this falls back to the text caption rather than inlining.
MAX_INLINE_BYTES = 18 * 1024 * 1024


def _mime_for(message: Any, media_type: str) -> str:
    meta = getattr(message, "media_metadata", None)
    if isinstance(meta, dict):
        mime = meta.get("mime_type")
        if isinstance(mime, str) and "/" in mime:
            return mime
    return _DEFAULT_MIME.get(media_type, "application/octet-stream")


async def stage_turn_media(
    messages: list[Any],
    *,
    workspace_id: int,
    chat_id: int | str | None,
    channel: str | None,
    read_bytes: Callable[[int, int], bytes | None] | None = None,
) -> list[TurnMediaPart]:
    """Return TurnMediaParts for the perceivable media in this turn's burst,
    reusing hydration-cached bytes (no sidecar fetch at dispatch)."""
    # Slice 1: only Telegram media is hydrated+cached locally.
    if (channel or "telegram_dm") != "telegram_dm":
        return []
    read = read_bytes or read_perception_bytes

    staged: list[TurnMediaPart] = []
    for message in messages or []:
        media_type = getattr(message, "media_type", None) or ""
        kind = PERCEPTION_KIND.get(media_type)
        if kind is None:
            continue
        message_id = int(getattr(message, "id", 0) or 0)
        if message_id <= 0:
            continue
        data = read(workspace_id, message_id)
        if not data or len(data) > MAX_INLINE_BYTES:
            logger.info(
                "media_perception_skipped msg=%s reason=%s",
                message_id,
                "no_cached_bytes" if not data else "oversize",
            )
            continue
        mime = _mime_for(message, media_type)
        if not mime.startswith(_KIND_MIME_PREFIX[kind]):
            # e.g. an animated .tgs / video .webm sticker -> not a vision image.
            logger.info("media_perception_skipped msg=%s reason=mime:%s", message_id, mime)
            continue
        staged.append(
            TurnMediaPart(
                message_ref=f"message:{message_id}",
                kind=kind,  # type: ignore[arg-type]
                mime_type=mime,
                source="inline",
                data=data,
            )
        )
    return staged
