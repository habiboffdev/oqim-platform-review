# backend/tests/test_media_semantics.py
"""Tests for app.modules.extraction_runtime.media_semantics."""

from unittest.mock import AsyncMock, patch

import pytest

from app.brain.llm import LLMResponse
from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.extraction_runtime.media_semantics import (
    normalize_image_message,
    normalize_media_placeholder,
    normalize_text_message,
    normalize_voice_message,
)


# ── Text messages ──


def test_text_passthrough():
    """Plain text messages are returned as-is with confidence=1.0."""
    result = normalize_text_message("iPhone 15 bormi?")
    assert result.text == "iPhone 15 bormi?"
    assert result.confidence == 1.0
    assert result.original_type == "text"


def test_text_empty():
    result = normalize_text_message("")
    assert result.text == ""
    assert result.confidence == 1.0


# ── Media placeholders ──


def test_placeholder_voice():
    result = normalize_media_placeholder("voice")
    assert "voice" in result.text.lower() or "ovozli" in result.text.lower()
    assert result.confidence == 0.0
    assert result.original_type == "voice"


def test_placeholder_photo():
    result = normalize_media_placeholder("photo")
    assert "photo" in result.text.lower() or "rasm" in result.text.lower()
    assert result.confidence == 0.0
    assert result.original_type == "photo"


def test_placeholder_sticker():
    result = normalize_media_placeholder("sticker")
    assert result.original_type == "sticker"


def test_placeholder_unknown_type():
    result = normalize_media_placeholder("poll")
    assert "poll" in result.text
    assert result.confidence == 0.0


# ── Voice transcription ──


def _make_llm_response(text: str) -> LLMResponse:
    """Create a mock LLMResponse with the given text."""
    return LLMResponse(
        text=text,
        model_used="gemini-3.1-flash-lite-preview",
        provider="gemini",
    )


@pytest.mark.asyncio
async def test_voice_transcription_success():
    """Voice messages call Flash-Lite and return transcript with confidence."""
    mock_resp = _make_llm_response("Salom, iPhone 15 bormi?")
    with patch("app.modules.extraction_runtime.media_semantics.generate_with_fallback", AsyncMock(return_value=mock_resp)):
        result = await normalize_voice_message(b"fake audio bytes", "audio/ogg")

    assert result.text == "Salom, iPhone 15 bormi?"
    assert result.confidence >= 0.6
    assert result.original_type == "voice"
    assert result.metadata["media_evidence"] == {
        "schema_version": "media_evidence.v1",
        "modality": "voice",
        "summary": "Salom, iPhone 15 bormi?",
        "observations": [
            {
                "kind": "transcript",
                "value": "Salom, iPhone 15 bormi?",
                "confidence": result.confidence,
                "fields": {},
            }
        ],
        "embedded_text": [],
        "transcript": "Salom, iPhone 15 bormi?",
        "customer_supplied": True,
        "confidence": result.confidence,
    }


@pytest.mark.asyncio
async def test_voice_transcription_uses_llm_gateway_when_context_is_supplied(db_session, workspace):
    captured = {}

    async def provider(request):
        captured["request"] = request
        return LLMProviderResponse(
            text='{"transcript":"Salom, kurs haqida ayting"}',
            model_used="fixture-media",
            token_usage={"input_tokens": 3, "output_tokens": 2},
        )

    result = await normalize_voice_message(
        b"fake audio bytes",
        "audio/ogg",
        gateway=LLMGateway(
            repository=CommercialSpineRepository(db_session),
            provider=provider,
        ),
        workspace_id=workspace.id,
        correlation_id="media:test:voice",
        source_refs=["message:voice:1"],
    )

    assert result.text == "Salom, kurs haqida ayting"
    assert captured["request"].prompt_id == "media.voice_transcription"
    assert captured["request"].content_parts[0]["kind"] == "inline_data"


@pytest.mark.asyncio
async def test_voice_short_transcript_low_confidence():
    """Very short transcriptions get low confidence."""
    mock_resp = _make_llm_response("Ha")
    with patch("app.modules.extraction_runtime.media_semantics.generate_with_fallback", AsyncMock(return_value=mock_resp)):
        result = await normalize_voice_message(b"fake", "audio/ogg")

    assert result.confidence == 0.3


@pytest.mark.asyncio
async def test_voice_empty_transcript_returns_placeholder():
    """Empty transcript falls back to placeholder."""
    mock_resp = _make_llm_response("")
    with patch("app.modules.extraction_runtime.media_semantics.generate_with_fallback", AsyncMock(return_value=mock_resp)):
        result = await normalize_voice_message(b"fake", "audio/ogg")

    assert result.confidence == 0.0
    assert result.original_type == "voice"


@pytest.mark.asyncio
async def test_voice_gemini_failure_returns_placeholder():
    """If LLM fails, return placeholder — never raise."""
    with patch(
        "app.modules.extraction_runtime.media_semantics.generate_with_fallback",
        AsyncMock(side_effect=Exception("LLM API down")),
    ):
        result = await normalize_voice_message(b"fake", "audio/ogg")

    assert result.confidence == 0.0
    assert "voice" in result.text.lower() or "ovozli" in result.text.lower()


# ── Image description ──


@pytest.mark.asyncio
async def test_image_description_success():
    """Photo messages call Flash-Lite vision and return typed semantics."""
    mock_resp = _make_llm_response(
        """
        {
          "visible_description": "A gold ring with a small clear stone on a hand.",
          "confidence": 0.92,
          "media_evidence": {
            "schema_version": "media_evidence.v1",
            "modality": "photo",
            "summary": "A gold ring with a small clear stone on a hand.",
            "observations": [
              {
                "kind": "visible_object",
                "value": "ring",
                "confidence": 0.90,
                "fields": {
                  "color": "gold",
                  "visible_attributes": ["small clear stone"]
                }
              }
            ],
            "embedded_text": [],
            "transcript": null,
            "customer_supplied": true,
            "confidence": 0.92
          }
        }
        """
    )
    with patch(
        "app.modules.extraction_runtime.media_semantics.generate_with_fallback",
        AsyncMock(return_value=mock_resp),
    ) as generate:
        result = await normalize_image_message(b"fake image bytes", "image/jpeg")

    assert "gold ring" in result.text
    assert result.confidence == 0.92
    assert result.original_type == "photo"
    assert result.metadata["media_evidence"] == {
        "schema_version": "media_evidence.v1",
        "modality": "photo",
        "summary": "A gold ring with a small clear stone on a hand.",
        "observations": [
            {
                "kind": "visible_object",
                "value": "ring",
                "confidence": 0.9,
                "fields": {
                    "color": "gold",
                    "visible_attributes": ["small clear stone"],
                },
            }
        ],
        "embedded_text": [],
        "transcript": None,
        "customer_supplied": True,
        "confidence": 0.92,
    }
    assert generate.await_args.kwargs["config"].response_mime_type == "application/json"
    assert generate.await_args.kwargs["config"].response_json_schema is not None


@pytest.mark.asyncio
async def test_image_description_uses_llm_gateway_when_context_is_supplied(db_session, workspace):
    captured = {}

    async def provider(request):
        captured["request"] = request
        return LLMProviderResponse(
            text="""
            {
              "visible_description": "A course schedule screenshot with evening groups.",
              "confidence": 0.88,
              "media_evidence": {
                "schema_version": "media_evidence.v1",
                "modality": "photo",
                "summary": "Course schedule screenshot",
                "observations": [
                  {
                    "kind": "embedded_text",
                    "value": "evening groups",
                    "confidence": 0.86,
                    "fields": {}
                  }
                ],
                "embedded_text": ["evening groups"],
                "transcript": null,
                "customer_supplied": true,
                "confidence": 0.88
              }
            }
            """,
            model_used="fixture-media",
            token_usage={"input_tokens": 4, "output_tokens": 3},
        )

    result = await normalize_image_message(
        b"fake image bytes",
        "image/jpeg",
        gateway=LLMGateway(
            repository=CommercialSpineRepository(db_session),
            provider=provider,
        ),
        workspace_id=workspace.id,
        correlation_id="media:test:image",
        source_refs=["message:image:1"],
    )

    assert "course schedule" in result.text
    assert result.confidence == 0.88
    assert captured["request"].prompt_id == "media.image_description"
    assert captured["request"].route_key == "media_rich"
    assert captured["request"].content_parts[0]["kind"] == "inline_data"


@pytest.mark.asyncio
async def test_image_empty_description_returns_placeholder():
    mock_resp = _make_llm_response("")
    with patch("app.modules.extraction_runtime.media_semantics.generate_with_fallback", AsyncMock(return_value=mock_resp)):
        result = await normalize_image_message(b"fake", "image/jpeg")

    assert result.confidence == 0.0
    assert result.original_type == "photo"


@pytest.mark.asyncio
async def test_image_gemini_failure_returns_placeholder():
    """If LLM vision fails, return placeholder — never raise."""
    with patch(
        "app.modules.extraction_runtime.media_semantics.generate_with_fallback",
        AsyncMock(side_effect=Exception("Quota exceeded")),
    ):
        result = await normalize_image_message(b"fake", "image/jpeg")

    assert result.confidence == 0.0
    assert "photo" in result.text.lower() or "rasm" in result.text.lower()
