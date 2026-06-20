from __future__ import annotations

from typing import Any

from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import MemoryFactWriteInput
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.contracts import (
    OnboardingLearningBootstrapInput,
    OnboardingLearningBootstrapResult,
)


def trust_mode_from_onboarding_preferences(preferences: dict | None) -> str:
    # Two trust states only: 'autopilot' (run + send) or 'disabled' (off, the
    # default). The agent only goes live when the owner explicitly opts into
    # autopilot during onboarding.
    if not preferences:
        return "disabled"
    reply_mode = str(preferences.get("reply_mode") or "").strip().lower()
    safe_autopilot = preferences.get("safe_autopilot") is True
    if safe_autopilot or reply_mode in {"safe_autopilot", "autopilot"}:
        return "autopilot"
    return "disabled"


def build_personalized_onboarding_profile(
    *,
    business_profile: dict[str, Any] | None,
    preferences: dict[str, Any] | None,
    sources: dict[str, Any] | None,
    owner_rules: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not business_profile and not preferences and not sources and not owner_rules:
        return None
    return {
        "schema_version": "personalized_onboarding.v1",
        "business_profile": business_profile or {},
        "preferences": preferences or {},
        "sources": sources or {},
        "owner_rules": owner_rules or {},
    }


class OnboardingLearningBootstrapService:
    """Boot-time learner that seeds Business Brain from onboarding inputs."""

    def __init__(self, *, repository: CommercialSpineRepository) -> None:
        self._memory = BusinessBrainMemoryService(repository=repository)

    async def seed_business_brain(
        self,
        request: OnboardingLearningBootstrapInput,
    ) -> OnboardingLearningBootstrapResult:
        correlation_id = f"onboarding:{request.workspace_id}:personalized_profile"
        writes = [
            (
                "business_profile_fact",
                "business_profile",
                "workspace:profile",
                ["onboarding:personalized_profile"],
                request.profile.get("business_profile") or {},
            ),
            (
                "operating_preference_fact",
                "preferences",
                "workspace:profile",
                ["onboarding:personalized_profile"],
                request.profile.get("preferences") or {},
            ),
            (
                "business_source_fact",
                "sources",
                "workspace:sources",
                ["onboarding:sources"],
                request.profile.get("sources") or {},
            ),
            (
                "seller_rule_fact",
                "owner_rules",
                "workspace:rules",
                ["onboarding:owner_rules"],
                request.profile.get("owner_rules") or {},
            ),
        ]
        source_items = _structured_onboarding_source_items(request.profile)
        for index, item in enumerate(source_items):
            writes.append(
                (
                    "business_source_fact",
                    f"source:{index:03d}",
                    f"workspace:source:{index:03d}",
                    [f"onboarding:source:{index}"],
                    {
                        "kind": str(item.get("kind") or "source"),
                        "label": str(item.get("label") or ""),
                        "purpose": _source_item_purpose(item),
                        "input": dict(item),
                        "processing": {
                            "state": "queued",
                            "reason": "onboarding_source_waiting_for_ingestion",
                        },
                    },
                )
            )

        written_fact_ids: list[str] = []
        for fact_type, id_suffix, entity_ref, fact_source_refs, value in writes:
            if not value:
                continue
            result = await self._memory.write_memory_fact(
                MemoryFactWriteInput(
                    workspace_id=request.workspace_id,
                    fact_id=f"onboarding:{request.workspace_id}:{id_suffix}",
                    fact_type=fact_type,
                    entity_ref=entity_ref,
                    value=dict(value),
                    source_refs=fact_source_refs,
                    source="onboarding",
                    status="active",
                    approval_state="confirmed",
                    confidence=1.0,
                    risk_tier="low",
                    correlation_id=correlation_id,
                    idempotency_key=f"onboarding:{request.workspace_id}:{id_suffix}:v1",
                    actor_ref=request.actor_ref,
                )
            )
            written_fact_ids.append(result.fact.fact_id)

        return OnboardingLearningBootstrapResult(
            written_fact_ids=written_fact_ids,
            queued_source_count=len(source_items),
        )


def _structured_onboarding_source_items(profile: dict[str, Any]) -> list[dict[str, Any]]:
    sources = profile.get("sources")
    if not isinstance(sources, dict):
        return []
    items = sources.get("items")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict)]


def _source_item_purpose(item: dict[str, Any]) -> str:
    purpose = str(item.get("purpose") or "brain_data").strip().lower()
    if purpose in {"brain_data", "agent_data"}:
        return purpose
    return "brain_data"
