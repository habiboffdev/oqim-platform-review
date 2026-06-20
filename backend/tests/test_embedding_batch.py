"""Tests for EmbeddingService.embed_texts_batch — batch text embedding."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_embedding_client_uses_embedding_2_and_global_vertex_default():
    from app.brain import embedding_service

    embedding_service._client = None
    mock_settings = MagicMock()
    mock_settings.google_cloud_location = "global"

    with patch(
        "app.brain.embedding_service.build_genai_client_kwargs",
        return_value=({"vertexai": True, "project": "test-project"}, MagicMock()),
    ), patch("app.brain.embedding_service.get_settings", return_value=mock_settings), patch(
        "app.brain.embedding_service.genai.Client"
    ) as mock_client, patch("app.brain.embedding_service.log_google_auth_status"):
        embedding_service._get_client()

    assert embedding_service.MODEL == "gemini-embedding-2"
    assert mock_client.call_args.kwargs["location"] == "global"
    embedding_service._client = None


async def test_embedding_client_preserves_explicit_vertex_embedding_location():
    from app.brain import embedding_service

    embedding_service._client = None
    mock_settings = MagicMock()
    mock_settings.google_cloud_location = "us"

    with patch(
        "app.brain.embedding_service.build_genai_client_kwargs",
        return_value=({"vertexai": True, "project": "test-project"}, MagicMock()),
    ), patch("app.brain.embedding_service.get_settings", return_value=mock_settings), patch(
        "app.brain.embedding_service.genai.Client"
    ) as mock_client, patch("app.brain.embedding_service.log_google_auth_status"):
        embedding_service._get_client()

    assert mock_client.call_args.kwargs["location"] == "us"
    embedding_service._client = None


@patch("app.brain.embedding_service._get_client")
async def test_embed_texts_batch_one_request_per_text(mock_get_client):
    """gemini-embedding-2 takes ONE content per call → one request per text,
    each carrying a single string content, with results in input order."""
    from app.brain.embedding_service import EmbeddingService

    async def fake_embed(*, model, contents, config):
        # Regression guard: this model rejects a wrapped types.Content ("one
        # content at a time") — embed_texts_batch must pass a plain string.
        assert isinstance(contents, str)
        idx = int(contents.rsplit("text_", 1)[-1])
        emb = MagicMock()
        emb.values = [float(idx)] + [0.0] * 3071
        result = MagicMock()
        result.embeddings = [emb]
        return result

    mock_client = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(side_effect=fake_embed)
    mock_get_client.return_value = mock_client

    svc = EmbeddingService()
    texts = [f"text_{i}" for i in range(5)]
    result = await svc.embed_texts_batch(texts)

    assert len(result) == 5
    assert all(len(v) == 3072 for v in result)
    # Order preserved: result[i] is the embedding of texts[i].
    assert [v[0] for v in result] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert mock_client.aio.models.embed_content.call_count == 5


@patch("app.brain.embedding_service._get_client")
async def test_embed_texts_batch_scales_to_many_texts(mock_get_client):
    """Large input → exactly one request per text (no multi-content batching)."""
    from app.brain.embedding_service import EmbeddingService

    async def fake_embed(*, model, contents, config):
        assert isinstance(contents, str)
        emb = MagicMock()
        emb.values = [0.1] * 3072
        result = MagicMock()
        result.embeddings = [emb]
        return result

    mock_client = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(side_effect=fake_embed)
    mock_get_client.return_value = mock_client

    svc = EmbeddingService()
    texts = [f"text_{i}" for i in range(120)]
    result = await svc.embed_texts_batch(texts)

    assert len(result) == 120
    assert mock_client.aio.models.embed_content.call_count == 120


@patch("app.brain.embedding_service._get_client")
async def test_embed_texts_batch_empty(mock_get_client):
    """Empty input → empty output, no API call."""
    from app.brain.embedding_service import EmbeddingService

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    svc = EmbeddingService()
    result = await svc.embed_texts_batch([])

    assert result == []
    mock_client.aio.models.embed_content.assert_not_called()


@patch("app.brain.embedding_service._get_client")
async def test_embed_pairs_batch_one_request_per_pair(mock_get_client):
    """embed_pairs_batch shares the one-content-per-call contract."""
    from google.genai import types

    from app.brain.embedding_service import EmbeddingService

    async def fake_embed(*, model, contents, config):
        assert not isinstance(contents, list)
        emb = MagicMock()
        emb.values = [0.2] * 3072
        result = MagicMock()
        result.embeddings = [emb]
        return result

    mock_client = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(side_effect=fake_embed)
    mock_get_client.return_value = mock_client

    svc = EmbeddingService()
    pairs = [types.Content(parts=[types.Part(text=f"pair_{i}")]) for i in range(3)]
    result = await svc.embed_pairs_batch(pairs)

    assert len(result) == 3
    assert mock_client.aio.models.embed_content.call_count == 3


@patch("app.brain.embedding_service._get_client")
async def test_embed_pairs_batch_empty(mock_get_client):
    """Empty pairs → empty output, no API call."""
    from app.brain.embedding_service import EmbeddingService

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    svc = EmbeddingService()
    assert await svc.embed_pairs_batch([]) == []
    mock_client.aio.models.embed_content.assert_not_called()


@pytest.mark.parametrize(
    ("mime_type", "payload"),
    [
        ("image/png", b"fake-png"),
        ("audio/mpeg", b"fake-mp3"),
        ("video/mp4", b"fake-mp4"),
        ("application/pdf", b"fake-pdf"),
    ],
)
@patch("app.brain.embedding_service._get_client")
async def test_embed_inline_data_supports_gemini_embedding_2_modalities(
    mock_get_client,
    mime_type,
    payload,
):
    """gemini-embedding-2 embeds text, image, audio, video, and PDFs in one route."""
    from app.brain.embedding_service import EmbeddingService

    captured = {}

    async def fake_embed(*, model, contents, config):
        captured.update({"model": model, "contents": contents, "config": config})
        emb = MagicMock()
        emb.values = [0.3] * 3072
        result = MagicMock()
        result.embeddings = [emb]
        return result

    mock_client = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(side_effect=fake_embed)
    mock_get_client.return_value = mock_client

    svc = EmbeddingService()
    result = await svc.embed_inline_data(payload, mime_type=mime_type)

    assert len(result) == 3072
    assert captured["model"] == "gemini-embedding-2"
    assert captured["config"].output_dimensionality == 3072
    content = captured["contents"]
    assert len(content.parts) == 1
    assert content.parts[0].inline_data.mime_type == mime_type
    assert content.parts[0].inline_data.data == payload
