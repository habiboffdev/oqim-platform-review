"""Tests for multimodal media semantic extraction basics."""

from app.modules.extraction_runtime.media_semantics import (
    NormalizedMessage,
    normalize_media_placeholder,
    normalize_text_message,
)


def test_text_passes_through():
    result = normalize_text_message("Salom! iPhone bormi?")
    assert result.text == "Salom! iPhone bormi?"
    assert result.confidence == 1.0
    assert result.original_type == "text"


def test_voice_placeholder():
    result = normalize_media_placeholder("voice")
    assert "voice" in result.text
    assert result.confidence == 0.0
    assert result.original_type == "voice"


def test_photo_placeholder():
    result = normalize_media_placeholder("photo")
    assert "photo" in result.text
    assert result.confidence == 0.0


def test_unknown_media_type():
    result = normalize_media_placeholder("gif")
    assert "[gif]" in result.text


def test_normalized_message_defaults():
    msg = NormalizedMessage(text="hello")
    assert msg.confidence == 1.0
    assert msg.original_type == "text"
    assert msg.metadata == {}
