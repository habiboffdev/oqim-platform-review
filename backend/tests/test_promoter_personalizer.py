"""Opener personalizer: one managed-prompt generation, mocked LLM."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.modules.bi_promoter.personalizer import personalize_opener

pytestmark = pytest.mark.asyncio


async def test_personalize_returns_text_and_passes_context():
    fake = AsyncMock(return_value=type("R", (), {"text": "Salom Ali! Yangi intake..."})())
    with patch("app.modules.bi_promoter.personalizer.generate_with_fallback", fake):
        out = await personalize_opener(
            workspace_id=1, base_message="Yangi intake ochildi",
            contact_name="Ali", crm_context="HR kursi bilan qiziqqan")

    assert out == "Salom Ali! Yangi intake..."
    # the prompt carries the owner's base message + the contact context
    sent = fake.await_args.kwargs["contents"]
    assert "Yangi intake ochildi" in sent
    assert "Ali" in sent
    assert "HR kursi" in sent
    assert fake.await_args.kwargs["operation"] == "promoter_opener"


async def test_personalize_falls_back_to_base_on_empty_generation():
    fake = AsyncMock(return_value=type("R", (), {"text": ""})())
    with patch("app.modules.bi_promoter.personalizer.generate_with_fallback", fake):
        out = await personalize_opener(
            workspace_id=1, base_message="Yangi intake ochildi",
            contact_name="Ali", crm_context="")
    assert out == "Yangi intake ochildi"  # never send an empty DM


async def test_personalize_pins_temperature_for_gemini3():
    fake = AsyncMock(return_value=type("R", (), {"text": "Salom!"})())
    with patch("app.modules.bi_promoter.personalizer.generate_with_fallback", fake):
        await personalize_opener(
            workspace_id=1, base_message="Yangi intake",
            contact_name="Ali", crm_context="")
    assert fake.await_args.kwargs["config"].temperature == 1.0


async def test_personalize_strips_em_dashes_via_house_normalizer():
    fake = AsyncMock(return_value=type("R", (), {"text": "Salom Ali — yangi intake ochildi"})())
    with patch("app.modules.bi_promoter.personalizer.generate_with_fallback", fake):
        out = await personalize_opener(
            workspace_id=1, base_message="x", contact_name="Ali", crm_context="")
    assert "—" not in out
    assert out == "Salom Ali, yangi intake ochildi"  # em-dash -> ", " (house normalizer)
