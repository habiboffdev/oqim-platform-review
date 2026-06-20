"""Embedding service: Gemini multimodal embeddings.

Model: gemini-embedding-2.
Output: 3072-dim (full Matryoshka, maximum retrieval quality).

Uses Vertex AI (ADC) when no GEMINI_API_KEY is set.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from google import genai
from google.genai import types
from pgvector.sqlalchemy import HALFVEC

from app.brain.model_policy import get_model_route
from app.core.config import get_settings
from app.core.google_auth import build_genai_client_kwargs, log_google_auth_status
from app.core.logging import get_logger

logger = get_logger("brain.embedding")

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Get or create the google-genai client (lazy init)."""
    global _client
    if _client is None:
        settings = get_settings()
        client_kwargs, auth_status = build_genai_client_kwargs(settings)
        if "api_key" not in client_kwargs:
            # Gemini Embedding 2 is exposed through the global Vertex endpoint
            # on the current google-genai SDK. Keep this default aligned with
            # the runtime env, not stale regional SDK behavior.
            location = getattr(settings, "google_cloud_location", None)
            client_kwargs["location"] = location or "global"
        log_google_auth_status(logger, component="brain.embedding", status=auth_status)
        _client = genai.Client(**client_kwargs)
    return _client


MODEL = get_model_route("embedding_multimodal_primary").model_id
DIMENSIONS = 3072


def ensure_embedding_dimensions(
    embedding: Sequence[float] | None,
    *,
    context: str,
) -> list[float] | None:
    """Return a normalized embedding only when it matches the configured dimension.

    This shields optional-memory writes and similarity queries from provider drift or
    stale test fixtures returning the wrong vector length.
    """
    if embedding is None:
        return None

    values = [float(v) for v in embedding]
    if len(values) != DIMENSIONS:
        logger.warning(
            "Discarding embedding with wrong dimension for %s: expected=%d actual=%d",
            context,
            DIMENSIONS,
            len(values),
        )
        return None
    return values


class EmbeddingService:
    """Embedding service for text and media-grounded retrieval.

    Full 3072-dim output for maximum retrieval quality. Gemini Embedding 2 maps
    text, images, audio, video, and documents into the same vector space.
    """

    async def embed_text(
        self,
        text: str,
        intent: str = "document",
    ) -> list[float]:
        """Embed a text string. Returns 3072-dim vector."""
        client = _get_client()
        result = await client.aio.models.embed_content(
            model=MODEL,
            contents=_embedding_instruction_text(text, intent=intent),
            config=types.EmbedContentConfig(
                output_dimensionality=DIMENSIONS,
            ),
        )
        return list(result.embeddings[0].values)

    async def embed_query(self, query: str) -> list[float]:
        """Embed a search query with prompt-level retrieval intent."""
        return await self.embed_text(query, intent="query")

    async def embed_inline_data(self, data: bytes, *, mime_type: str) -> list[float]:
        """Embed inline media or document bytes in the same 3072-dim vector space.

        Gemini Embedding 2 accepts text, image, audio, video, and PDF inputs.
        Use this for catalog media, source screenshots, voice notes, videos, and
        document artifacts when bytes are available.
        """
        client = _get_client()
        inline_part = types.Part.from_bytes(data=data, mime_type=mime_type)
        result = await client.aio.models.embed_content(
            model=MODEL,
            contents=types.Content(parts=[inline_part]),
            config=types.EmbedContentConfig(
                output_dimensionality=DIMENSIONS,
            ),
        )
        return list(result.embeddings[0].values)

    async def embed_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> list[float]:
        """Embed an image. Returns 3072-dim vector in the same space as text.

        Use for: customer sends product photo → embed → cosine search finds matching products.
        """
        return await self.embed_inline_data(image_bytes, mime_type=mime_type)

    async def embed_pair(self, parts: list) -> list[float]:
        """Embed a conversation pair as a single 3072-dim vector.

        Parts must be types.Part objects (text and/or media).
        Wrapped in types.Content internally to produce ONE combined embedding.
        """
        client = _get_client()
        result = await client.aio.models.embed_content(
            model=MODEL,
            contents=_content_with_document_instruction(types.Content(parts=parts)),
            config=types.EmbedContentConfig(
                output_dimensionality=DIMENSIONS,
            ),
        )
        return list(result.embeddings[0].values)

    _EMBED_CONCURRENCY = 8

    async def _embed_one(self, client: genai.Client, contents) -> list[float]:
        """One embed_content call -> one 3072-dim vector.

        gemini-embedding-2 rejects multi-content requests ("only supports one
        content at a time"), so every embedding is its own request.
        """
        result = await client.aio.models.embed_content(
            model=MODEL,
            contents=contents,
            config=types.EmbedContentConfig(output_dimensionality=DIMENSIONS),
        )
        return list(result.embeddings[0].values)

    async def embed_pairs_batch(self, pairs: list) -> list[list[float]]:
        """Embed multiple pairs, one request each, concurrently (order preserved)."""
        if not pairs:
            return []
        client = _get_client()
        sem = asyncio.Semaphore(self._EMBED_CONCURRENCY)

        async def one(pair) -> list[float]:
            async with sem:
                return await self._embed_one(
                    client, _content_with_document_instruction(pair)
                )

        return list(await asyncio.gather(*(one(pair) for pair in pairs)))

    async def embed_texts_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple text strings, one request each, concurrently (order preserved).

        gemini-embedding-2 takes a single content per call, so throughput comes
        from bounded concurrency, not from a multi-content request. Each text goes
        through the same string-`contents` path as `embed_text` — wrapping the text
        in a `types.Content` here tripped the model's "one content at a time" guard
        and crashed SKILL.md learning.
        """
        if not texts:
            return []
        sem = asyncio.Semaphore(self._EMBED_CONCURRENCY)

        async def one(text: str) -> list[float]:
            async with sem:
                return await self.embed_text(text, intent="document")

        return list(await asyncio.gather(*(one(text) for text in texts)))


def halfvec_cosine(column, query_vec):
    """Cosine distance with halfvec cast — matches HNSW expression indexes at 3072 dims.

    pgvector HNSW indexes on 3072-dim vectors use halfvec expression:
        CREATE INDEX ... USING hnsw ((col::halfvec(3072)) halfvec_cosine_ops)
    Queries must cast to halfvec for the planner to use the index.
    """
    return column.cast(HALFVEC(DIMENSIONS)).cosine_distance(query_vec)


def _embedding_instruction_text(text: str, *, intent: str) -> str:
    if intent == "query":
        return f"Embed this search query for OQIM Business retrieval:\n{text}"
    return f"Embed this source document for OQIM Business retrieval:\n{text}"


def _content_with_document_instruction(content) -> types.Content:
    parts = list(getattr(content, "parts", []) or [])
    return types.Content(
        parts=[
            types.Part(
                text="Embed this multimodal source document for OQIM Business retrieval."
            ),
            *parts,
        ]
    )
