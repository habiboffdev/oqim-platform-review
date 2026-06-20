from unittest.mock import Mock, patch

import pytest

from app.brain import reranker


def test_ranking_config_uses_explicit_discovery_engine_settings():
    settings = Mock(
        google_cloud_project="oqim-494421",
        discovery_engine_ranking_location="global",
        discovery_engine_ranking_config="semantic-ranker-prod",
        discovery_engine_ranking_model="semantic-ranker-default-v1",
    )

    assert (
        reranker._ranking_config_path(settings)
        == "projects/oqim-494421/locations/global/rankingConfigs/semantic-ranker-prod"
    )
    assert reranker._ranking_model(settings) == "semantic-ranker-default-v1"


@pytest.mark.asyncio
async def test_rerank_disables_process_after_discovery_engine_service_disabled():
    reranker._rank_disabled_reason = None
    reranker._client = None
    mock_client = Mock()
    mock_client.rank.side_effect = RuntimeError("SERVICE_DISABLED discoveryengine.googleapis.com")

    candidates = [{"text": "a"}, {"text": "b"}]
    with patch("app.brain.reranker._get_rank_client", return_value=mock_client):
        first = await reranker.rerank("query", candidates, top_n=1)
        second = await reranker.rerank("query", candidates, top_n=1)

    assert first == candidates[:1]
    assert second == candidates[:1]
    assert mock_client.rank.call_count == 1
    reranker._rank_disabled_reason = None
