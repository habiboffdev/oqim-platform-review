from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import ContextualRetrievalRequest
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.contracts import OnboardingLearningBootstrapInput
from app.modules.onboarding_learning.service import (
    OnboardingLearningBootstrapService,
    build_personalized_onboarding_profile,
    trust_mode_from_onboarding_preferences,
)


def _repository(db_session: AsyncSession) -> CommercialSpineRepository:
    return CommercialSpineRepository(db_session)


def test_personalized_profile_is_absent_when_onboarding_has_no_brain_inputs() -> None:
    assert build_personalized_onboarding_profile(
        business_profile=None,
        preferences=None,
        sources=None,
        owner_rules=None,
    ) is None


def test_autopilot_preference_maps_to_workspace_trust_mode() -> None:
    assert trust_mode_from_onboarding_preferences({"safe_autopilot": True}) == "autopilot"
    assert trust_mode_from_onboarding_preferences({"reply_mode": "autopilot"}) == "autopilot"
    assert trust_mode_from_onboarding_preferences({"reply_mode": "draft"}) == "disabled"
    assert trust_mode_from_onboarding_preferences(None) == "disabled"


async def test_onboarding_learning_bootstrap_seeds_business_brain_boundary(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    profile = build_personalized_onboarding_profile(
        business_profile={"offer_summary": "Ayollar kiyimi", "tone": "short_warm"},
        preferences={"reply_mode": "draft"},
        sources={
            "items": [
                {
                    "kind": "website",
                    "label": "Sayt",
                    "url": "https://nafis.example/shop",
                    "purpose": "agent_data",
                }
            ],
        },
        owner_rules={"notes": "Yetkazish so'ralsa, manzil so'ra."},
    )
    assert profile is not None

    result = await OnboardingLearningBootstrapService(
        repository=_repository(db_session),
    ).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile=profile,
            actor_ref=f"workspace:{workspace.id}",
        )
    )

    assert result.queued_source_count == 1
    assert f"onboarding:{workspace.id}:business_profile" in result.written_fact_ids
    assert f"onboarding:{workspace.id}:source:000" in result.written_fact_ids

    memory = BusinessBrainMemoryService(repository=_repository(db_session))
    retrieved = await memory.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["business_profile_fact", "business_source_fact"],
            requested_slots=["business_profile_fact", "business_source_fact"],
            limit=20,
        )
    )

    facts = {candidate.fact_id: candidate for candidate in retrieved.candidates}
    queued_source = facts[f"onboarding:{workspace.id}:source:000"]
    assert facts[f"onboarding:{workspace.id}:business_profile"].value == {
        "offer_summary": "Ayollar kiyimi",
        "tone": "short_warm",
    }
    assert queued_source.value["kind"] == "website"
    assert queued_source.value["purpose"] == "agent_data"
    assert queued_source.value["processing"]["state"] == "queued"
    assert queued_source.value["input"]["url"] == "https://nafis.example/shop"
