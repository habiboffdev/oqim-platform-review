from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.brain.embedding_service import ensure_embedding_dimensions
from app.brain.model_policy import get_model_route

EMBEDDING_ROUTE_KEY = "embedding_multimodal_primary"


@dataclass(frozen=True, slots=True)
class RetrievalIndexEmbeddingResult:
    embedding: list[float] | None
    embedding_model: str | None
    embedding_state: str
    degraded_reason: str | None


class RetrievalIndexEmbeddingService:
    """Retrieval Core boundary for source-unit/index embeddings."""

    def __init__(
        self,
        *,
        embedding_service: Any | None = None,
        route_key: str = EMBEDDING_ROUTE_KEY,
    ) -> None:
        self._embedding_service = embedding_service
        self._route_key = route_key

    async def embed_text(
        self,
        text: str,
        *,
        enabled: bool,
        context: str,
    ) -> RetrievalIndexEmbeddingResult:
        if not enabled:
            return RetrievalIndexEmbeddingResult(None, None, "pending", None)
        try:
            model_id = get_model_route(self._route_key).model_id
            raw_embedding = await self._service().embed_text(text)
            embedding = ensure_embedding_dimensions(raw_embedding, context=context)
        except Exception:
            return RetrievalIndexEmbeddingResult(None, None, "degraded", "embedding_unavailable")
        if embedding is None:
            return RetrievalIndexEmbeddingResult(
                None,
                model_id,
                "degraded",
                "embedding_dimension_mismatch",
            )
        return RetrievalIndexEmbeddingResult(embedding, model_id, "ready", None)

    async def embed_texts(
        self,
        texts: list[str],
        *,
        enabled: bool,
        context_prefix: str,
    ) -> list[RetrievalIndexEmbeddingResult]:
        if not texts:
            return []
        if not enabled:
            return [
                RetrievalIndexEmbeddingResult(None, None, "pending", None)
                for _text in texts
            ]
        try:
            model_id = get_model_route(self._route_key).model_id
        except Exception:
            return [
                RetrievalIndexEmbeddingResult(None, None, "degraded", "embedding_unavailable")
                for _text in texts
            ]

        try:
            raw_embeddings = await self._service().embed_texts_batch(texts)
        except Exception:
            return [
                await self._embed_text_with_model(
                    text,
                    model_id=model_id,
                    context=f"{context_prefix}:{index}",
                )
                for index, text in enumerate(texts)
            ]

        results: list[RetrievalIndexEmbeddingResult] = []
        for index, raw_embedding in enumerate(raw_embeddings[: len(texts)]):
            embedding = ensure_embedding_dimensions(
                raw_embedding,
                context=f"{context_prefix}:{index}",
            )
            if embedding is None:
                results.append(
                    RetrievalIndexEmbeddingResult(
                        None,
                        model_id,
                        "degraded",
                        "embedding_dimension_mismatch",
                    )
                )
            else:
                results.append(RetrievalIndexEmbeddingResult(embedding, model_id, "ready", None))
        if len(results) < len(texts):
            results.extend(
                RetrievalIndexEmbeddingResult(
                    None,
                    model_id,
                    "degraded",
                    "embedding_missing_from_batch",
                )
                for _index in range(len(results), len(texts))
            )
        return results

    async def _embed_text_with_model(
        self,
        text: str,
        *,
        model_id: str,
        context: str,
    ) -> RetrievalIndexEmbeddingResult:
        try:
            raw_embedding = await self._service().embed_text(text)
            embedding = ensure_embedding_dimensions(raw_embedding, context=context)
        except Exception:
            return RetrievalIndexEmbeddingResult(
                None,
                model_id,
                "degraded",
                "embedding_unavailable",
            )
        if embedding is None:
            return RetrievalIndexEmbeddingResult(
                None,
                model_id,
                "degraded",
                "embedding_dimension_mismatch",
            )
        return RetrievalIndexEmbeddingResult(embedding, model_id, "ready", None)

    def _service(self) -> Any:
        if self._embedding_service is not None:
            return self._embedding_service
        from app.brain.embedding_service import EmbeddingService

        self._embedding_service = EmbeddingService()
        return self._embedding_service
