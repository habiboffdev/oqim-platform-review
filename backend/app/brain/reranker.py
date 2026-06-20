"""Vertex AI Ranking API client.

Replaces the LLM reranker in catalog/knowledge search.
~130ms latency, ~$0.001/query, deterministic [0,1] scores.

Uses the Discovery Engine RankService. Requires:
  - google-cloud-discoveryengine package
  - GOOGLE_CLOUD_PROJECT set in environment
  - optional DISCOVERY_ENGINE_RANKING_LOCATION / CONFIG / MODEL overrides
"""

import asyncio

from google.cloud import discoveryengine_v1 as de

from app.core.config import get_settings
from app.core.google_auth import log_google_auth_status, resolve_google_auth
from app.core.logging import get_logger

logger = get_logger("brain.reranker")

_client: de.RankServiceClient | None = None
_rank_disabled_reason: str | None = None


def _get_rank_client() -> de.RankServiceClient:
    """Get or create the Vertex AI Ranking client (lazy init)."""
    global _client
    if _client is None:
        resolution = resolve_google_auth()
        log_google_auth_status(logger, component="brain.reranker", status=resolution.status)
        if resolution.credentials is not None:
            _client = de.RankServiceClient(credentials=resolution.credentials)
        else:
            _client = de.RankServiceClient()
    return _client


async def rerank(
    query: str,
    candidates: list[dict],
    text_field: str = "text",
    top_n: int = 5,
) -> list[dict]:
    """Rerank candidates using Vertex AI Ranking API.

    Args:
        query: The search query.
        candidates: List of dicts, each with at least a `text_field` key.
        text_field: Which field in the candidate dict contains the text to rank.
        top_n: Number of top results to return.

    Returns:
        Reranked list of candidate dicts with `relevance_score` added.
    """
    if not candidates:
        return []

    global _rank_disabled_reason
    if _rank_disabled_reason:
        logger.debug(
            "Vertex AI reranking disabled for this process, returning unranked top-%d: %s",
            top_n,
            _rank_disabled_reason,
        )
        return candidates[:top_n]

    settings = get_settings()
    client = _get_rank_client()

    ranking_config = _ranking_config_path(settings)
    ranking_model = _ranking_model(settings)

    records = [
        de.RankingRecord(id=str(i), content=cand.get(text_field, ""))
        for i, cand in enumerate(candidates)
    ]

    try:
        # RankServiceClient.rank() is synchronous gRPC — offload to thread
        response = await asyncio.to_thread(
            client.rank,
            request=de.RankRequest(
                ranking_config=ranking_config,
                model=ranking_model,
                query=query,
                records=records,
                top_n=top_n,
            )
        )

        results = []
        for record in response.records:
            idx = int(record.id)
            if idx < len(candidates):
                cand = candidates[idx].copy()
                cand["relevance_score"] = record.score
                results.append(cand)

        logger.info("Reranked %d → %d candidates for query: %s", len(candidates), len(results), query[:50])
        return results

    except Exception as exc:
        message = str(exc)
        if "Reauthentication is needed" in message or "default credentials" in message.lower():
            logger.warning(
                "Vertex AI reranking auth unavailable, returning unranked top-%d: %s",
                top_n,
                message,
            )
        elif "SERVICE_DISABLED" in message or "discoveryengine.googleapis.com" in message:
            _rank_disabled_reason = "Discovery Engine API disabled or unavailable"
            logger.warning(
                "Vertex AI reranking disabled, returning unranked top-%d: %s",
                top_n,
                _rank_disabled_reason,
            )
        else:
            logger.exception("Vertex AI reranking failed, returning unranked top-%d", top_n)
        # Graceful fallback — return unranked candidates
        return candidates[:top_n]


def _ranking_config_path(settings) -> str:
    project = settings.google_cloud_project
    location = (settings.discovery_engine_ranking_location or "global").strip()
    config = (settings.discovery_engine_ranking_config or "default_ranking_config").strip()
    return f"projects/{project}/locations/{location}/rankingConfigs/{config}"


def _ranking_model(settings) -> str:
    return (settings.discovery_engine_ranking_model or "semantic-ranker-default-v1").strip()
