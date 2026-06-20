"""End-to-end smoke test — known messages → verify draft quality.

Run: cd backend && python -m pytest evals/test_e2e_smoke.py --timeout=120 -v
"""
import pytest

SMOKE_CASES = [
    {"message": "Salom", "expect_intent": "greeting"},
    {"message": "iPhone 15 bormi?", "expect_intent": "product_inquiry"},
    {"message": "Narxi qancha?", "expect_intent": "price_question"},
    {"message": "Yetkazib berasizmi?", "expect_intent": "delivery_question"},
    {"message": "Rahmat", "expect_intent": "closing"},
]


@pytest.mark.parametrize("case", SMOKE_CASES, ids=lambda c: c.get("message", "unknown"))
async def test_e2e_smoke(case):
    """Placeholder: E2E draft generation smoke test.

    TODO: Once test workspace exists, implement:
    1. Call generate_draft with the message
    2. Verify draft is non-empty
    3. Verify intent matches expected
    4. Verify token budget < 5000
    """
    assert "message" in case
    assert "expect_intent" in case
