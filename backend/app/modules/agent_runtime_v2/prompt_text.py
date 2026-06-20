"""Single source of truth for rendering one Message into prompt text.

Was duplicated in dispatcher.py and conversation_turns/service.py.

For media whose bytes are attached to the live turn natively
(``native_media=True``), the text is the LABELED transcript/description —
``[Voice message: "…"]`` / ``[Photo: "…"]`` — NOT the bare transcript. Two
reasons:
  1. The label tells the model it was spoken/shown, so it can't reply "thanks
     for writing in text" to a voice note (live failure 2026-06-13).
  2. This text is what gets recorded into conversation history, so later turns
     remember what the customer said via the transcript — while the actual audio/
     image is attached only on THIS turn (side-channel, pay-once) for fidelity.
So: model hears the audio NOW, reads the transcript when looking back LATER.

Pass ``bare=True`` at the live call site to omit the transcript and show only
the marker (``[Voice message]``) — the attached audio/image Part carries the
content, so the transcript must not compete with it at inference time.

Without ``native_media`` (history replay, grounding, non-staged media) the raw
transcript / placeholder is returned as before.
"""
from __future__ import annotations

from typing import Any

# media_type -> human noun the model sees in the label
_NATIVE_MEDIA = {
    "voice": "Voice message",
    "audio": "Voice message",
    "photo": "Photo",
    "sticker": "Sticker",
}


def message_prompt_text(message: Any, *, native_media: bool = False, bare: bool = False) -> str:
    media_type = getattr(message, "media_type", None)
    text = (getattr(message, "content", None) or "").strip()
    if native_media and media_type in _NATIVE_MEDIA:
        noun = _NATIVE_MEDIA[media_type]
        # bare: live Gemini call sees ONLY the marker (the audio/image Part
        # carries the content). Non-bare: labeled transcript for the session.
        if bare:
            return f"[{noun}]"
        return f'[{noun}: "{text}"]' if text else f"[{noun}]"
    if text:
        return text
    return f"[{media_type}]" if media_type else ""
