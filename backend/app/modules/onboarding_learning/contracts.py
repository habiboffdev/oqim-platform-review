from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OnboardingLearningModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OnboardingLearningBootstrapInput(OnboardingLearningModel):
    schema_version: Literal["onboarding_learning_bootstrap_input.v1"] = (
        "onboarding_learning_bootstrap_input.v1"
    )
    workspace_id: int = Field(gt=0)
    profile: dict[str, Any]
    actor_ref: str = Field(min_length=1)


class OnboardingLearningBootstrapResult(OnboardingLearningModel):
    schema_version: Literal["onboarding_learning_bootstrap_result.v1"] = (
        "onboarding_learning_bootstrap_result.v1"
    )
    written_fact_ids: list[str] = Field(default_factory=list)
    queued_source_count: int = 0
