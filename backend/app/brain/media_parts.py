"""Neutral contract for the media a single live turn carries into Gemini.

Lives in app.brain (the LLM layer) so the Gemini boundary in app/brain/llm.py
and the agent-runtime staging code share ONE definition without a layering
cycle. These objects are injected only for the CURRENT turn at the Gemini
boundary and never enter Hermes session history (structural pay-once).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from google.genai import types


@dataclass(slots=True)
class TurnMediaPart:
    message_ref: str                       # "message:<id>" — provenance/audit
    kind: Literal["vision", "audio"]       # how the model should perceive it
    mime_type: str                         # e.g. "image/jpeg", "audio/ogg"
    source: Literal["inline", "file_uri"]
    data: bytes | None = None              # set when source == "inline"
    file_uri: str | None = None            # set when source == "file_uri"


def to_gemini_part(part: TurnMediaPart) -> types.Part:
    """Convert a TurnMediaPart to a Gemini content Part."""
    if part.source == "file_uri":
        if not part.file_uri:
            raise ValueError("file_uri source requires file_uri")
        return types.Part.from_uri(file_uri=part.file_uri, mime_type=part.mime_type)
    if part.data is None:
        raise ValueError("inline source requires data bytes")
    return types.Part.from_bytes(data=part.data, mime_type=part.mime_type)
