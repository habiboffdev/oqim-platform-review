"""Search quality evaluation — Recall@5 on labeled queries.

Run: cd backend && python -m pytest evals/test_search_quality.py --timeout=120 -v
"""
import json
import pytest
from pathlib import Path

DATASET = Path(__file__).parent / "datasets" / "search_queries.jsonl"

# Seed with a few labeled queries
SEED_QUERIES = [
    {"query": "iPhone 15", "type": "exact_product", "note": "Should find iPhone 15 variants"},
    {"query": "arzon telefon", "type": "price_range", "note": "Should find budget phones"},
    {"query": "yetkazib berasizmi", "type": "not_catalog", "note": "Delivery question, not a product search"},
]


def load_queries():
    if not DATASET.exists():
        return SEED_QUERIES
    with open(DATASET) as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.mark.parametrize("case", load_queries(), ids=lambda c: c.get("query", "unknown"))
async def test_search_relevance(case):
    """Placeholder: Search relevance eval.

    TODO: Once catalog has real data, implement:
    1. Run catalog_search with the query
    2. Check if expected products appear in top 5
    3. Calculate Recall@5
    """
    assert "query" in case
    assert "type" in case
