"""Voice quality evaluation using LLM-as-judge.

Requires: corrections.jsonl dataset and API keys.
Run: cd backend && python -m pytest evals/test_voice_quality.py --timeout=120 -v

Generate the dataset first:
    python evals/scripts/mine_corrections.py
"""
import json
import pytest
from pathlib import Path

DATASET = Path(__file__).parent / "datasets" / "corrections.jsonl"


def load_corrections():
    if not DATASET.exists():
        return []
    with open(DATASET) as f:
        return [json.loads(line) for line in f if line.strip()]


corrections = load_corrections()


@pytest.mark.skipif(not corrections, reason="No corrections dataset — run mine_corrections.py first")
@pytest.mark.parametrize("case", corrections[:10], ids=lambda c: f"correction-{c.get('id', 'unknown')}")
async def test_voice_quality(case):
    """Placeholder: LLM-as-judge voice quality eval.

    TODO: Once we have real corrections data, implement:
    1. Generate a draft for the case's situation
    2. Judge the draft against the seller's correction
    3. Score on 5 dimensions (voice, factual, intent, language, situation)
    """
    assert case.get("wrong") != case.get("right"), "Correction should differ from original"
