"""Media semantic extraction for Source Intake and Universal Extraction.

This module turns hydrated media bytes into evidence capsules that can be
attached to Business Brain and OQIM Intelligence extraction requests. It does
not decide business truth; owner runtimes consume the evidence through typed
Universal Extraction candidates and review/proposal flows.

Each result includes:
  - text: the normalized content
  - confidence: 0.0-1.0 (how reliable the evidence is)
  - original_type: the raw media type
"""

from __future__ import annotations

import json
import base64
from dataclasses import dataclass, field
from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from app.brain.llm import generate_with_fallback
from app.brain.llm_policy import FLASH_LITE_GEMINI_CHAIN
from app.brain.prompt_registry import get_prompt_registry
from app.core.logging import get_logger
from app.modules.commercial_spine.contracts import LLMGatewayRequest
from app.modules.commercial_spine.llm_gateway import LLMGateway

logger = get_logger("extraction_runtime.media_semantics")

_PROMPTS = get_prompt_registry()
MEDIA_VOICE_TRANSCRIPTION_PROMPT = _PROMPTS.load(
    "media.voice_transcription",
    version="1.0.0",
).body.strip()
MEDIA_IMAGE_DESCRIPTION_PROMPT = _PROMPTS.load(
    "media.image_description",
    version="1.0.0",
).body.strip()


@dataclass
class NormalizedMessage:
    """Media or text evidence prepared for downstream extraction."""
    text: str
    confidence: float = 1.0
    original_type: str = "text"
    metadata: dict = field(default_factory=dict)


MEDIA_EVIDENCE_VERSION = "media_evidence.v1"


class MediaEvidenceObservation(BaseModel):
    kind: str = Field(min_length=1)
    value: str | int | float | bool | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    fields: dict[str, Any] = Field(default_factory=dict)


class MediaEvidenceCapsule(BaseModel):
    schema_version: Literal["media_evidence.v1"] = (
        MEDIA_EVIDENCE_VERSION
    )
    modality: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    observations: list[MediaEvidenceObservation] = Field(
        default_factory=list,
        max_length=24,
    )
    embedded_text: list[str] = Field(default_factory=list, max_length=20)
    transcript: str | None = None
    customer_supplied: bool = True
    confidence: float = Field(ge=0.0, le=1.0)


class ImageSemanticDescription(BaseModel):
    visible_description: str = Field(min_length=1)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    media_evidence: MediaEvidenceCapsule


class VoiceTranscriptOutput(BaseModel):
    transcript: str = ""


def normalize_text_message(text: str) -> NormalizedMessage:
    """Pass-through for text messages."""
    return NormalizedMessage(text=text, confidence=1.0, original_type="text")


def normalize_media_placeholder(media_type: str) -> NormalizedMessage:
    """Placeholder for media messages when bytes aren't available.

    Used as a fallback until the media runtime hydrates semantic content.
    """
    labels = {
        "voice": "Mijoz ovozli xabar yubordi",
        "audio": "Mijoz ovozli xabar yubordi",
        "photo": "Mijoz rasm yubordi",
        "video": "Mijoz video yubordi",
        "video_note": "Mijoz video xabar yubordi",
        "document": "Mijoz hujjat yubordi",
        "sticker": "😊",
    }
    label = labels.get(media_type, f"[{media_type}]")
    return NormalizedMessage(
        text=f"[{media_type}] {label}",
        confidence=0.0,
        original_type=media_type,
    )


async def normalize_voice_message(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    *,
    gateway: LLMGateway | None = None,
    workspace_id: int | None = None,
    correlation_id: str | None = None,
    source_refs: list[str] | None = None,
) -> NormalizedMessage:
    """Transcribe a voice message via Flash-Lite.

    Returns NormalizedMessage with confidence based on transcription quality.
    """
    try:
        if gateway is not None and workspace_id is not None and correlation_id:
            result = await gateway.generate(
                LLMGatewayRequest(
                    route_key="structured_fast",
                    workflow_name="media_voice_transcription",
                    prompt_id="media.voice_transcription",
                    prompt_version="1.0.0",
                    input_payload={"mime_type": mime_type},
                    content_parts=[
                        {
                            "kind": "inline_data",
                            "mime_type": mime_type,
                            "data_base64": base64.b64encode(audio_bytes).decode("ascii"),
                        }
                    ],
                    output_schema_name="VoiceTranscriptOutput",
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                    source_refs=source_refs or [],
                ),
                output_model=VoiceTranscriptOutput,
            )
            text = (
                str((result.parsed_output or {}).get("transcript") or "").strip()
                if result.status == "ok"
                else ""
            )
        else:
            audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
            response = await generate_with_fallback(
                chain=FLASH_LITE_GEMINI_CHAIN,
                contents=[
                    types.Content(parts=[
                        audio_part,
                        types.Part(text=MEDIA_VOICE_TRANSCRIPTION_PROMPT),
                    ]),
                ],
                config=types.GenerateContentConfig(temperature=1.0, max_output_tokens=500),
            )
            text = _parse_voice_transcript(response.text)
        if not text:
            return normalize_media_placeholder("voice")

        # Short transcripts are weak evidence; longer transcripts are usually useful.
        if len(text) < 5:
            confidence = 0.3
        elif len(text) < 20:
            confidence = 0.6
        else:
            confidence = 0.85

        logger.info("Voice transcribed: %d chars, confidence=%.2f", len(text), confidence)
        return NormalizedMessage(
            text=text,
            confidence=confidence,
            original_type="voice",
            metadata={
                "media_evidence": _transcript_media_evidence(
                    text=text,
                    confidence=confidence,
                )
            },
        )

    except Exception:
        logger.exception("Voice transcription failed")
        return normalize_media_placeholder("voice")


async def normalize_image_message(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    *,
    gateway: LLMGateway | None = None,
    workspace_id: int | None = None,
    correlation_id: str | None = None,
    source_refs: list[str] | None = None,
) -> NormalizedMessage:
    """Describe an image and return typed commercial semantics via Flash-Lite.

    Returns a text description like "Customer sent a photo of the visible item."
    """
    try:
        if gateway is not None and workspace_id is not None and correlation_id:
            result = await gateway.generate(
                LLMGatewayRequest(
                    route_key="media_rich",
                    workflow_name="media_image_description",
                    prompt_id="media.image_description",
                    prompt_version="1.0.0",
                    input_payload={"mime_type": mime_type},
                    content_parts=[
                        {
                            "kind": "inline_data",
                            "mime_type": mime_type,
                            "data_base64": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    ],
                    output_schema_name="ImageSemanticDescription",
                    workspace_id=workspace_id,
                    correlation_id=correlation_id,
                    source_refs=source_refs or [],
                ),
                output_model=ImageSemanticDescription,
            )
            semantic = (
                ImageSemanticDescription.model_validate(result.parsed_output)
                if result.status == "ok" and result.parsed_output is not None
                else None
            )
        else:
            image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            response = await generate_with_fallback(
                chain=FLASH_LITE_GEMINI_CHAIN,
                contents=[
                    types.Content(parts=[
                        image_part,
                        types.Part(text=MEDIA_IMAGE_DESCRIPTION_PROMPT),
                    ]),
                ],
                config=types.GenerateContentConfig(
                    temperature=1.0,
                    max_output_tokens=512,
                    response_mime_type="application/json",
                    response_json_schema=ImageSemanticDescription.model_json_schema(),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                ),
            )
            semantic = _parse_image_semantic_description(response.text)
        if semantic is None:
            return normalize_media_placeholder("photo")

        description = f"[photo] {semantic.visible_description.strip()}"
        logger.info("Image described: %s", semantic.visible_description[:80])
        return NormalizedMessage(
            text=description,
            confidence=semantic.confidence,
            original_type="photo",
            metadata={
                "media_evidence": semantic.media_evidence.model_dump(
                    mode="json"
                )
            },
        )

    except Exception:
        logger.exception("Image description failed")
        return normalize_media_placeholder("photo")


def _parse_image_semantic_description(
    raw_text: str | None,
) -> ImageSemanticDescription | None:
    text = (raw_text or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    try:
        return ImageSemanticDescription.model_validate(payload)
    except ValidationError:
        return None


def _parse_voice_transcript(raw_text: str | None) -> str:
    text = (raw_text or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, dict):
        return str(parsed.get("transcript") or "").strip()
    return text


def _transcript_media_evidence(*, text: str, confidence: float) -> dict[str, Any]:
    return {
        "schema_version": MEDIA_EVIDENCE_VERSION,
        "modality": "voice",
        "summary": text,
        "observations": [
            {
                "kind": "transcript",
                "value": text,
                "confidence": confidence,
                "fields": {},
            }
        ],
        "embedded_text": [],
        "transcript": text,
        "customer_supplied": True,
        "confidence": confidence,
    }
