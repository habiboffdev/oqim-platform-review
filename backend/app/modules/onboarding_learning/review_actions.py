from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.commerce_catalog.service import CommerceCatalogCoreService
from app.modules.commercial_spine.contracts import (
    ApprovalState,
    BusinessBrainProjection,
    BusinessBrainUpdate,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository

ReviewAction = Literal["approve", "reject", "edit", "merge"]
ReviewTargetType = Literal["fact", "product"]


class OnboardingLearnedReviewActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OnboardingLearnedReviewActionRequest(OnboardingLearnedReviewActionModel):
    schema_version: Literal["onboarding_learned_review_action_request.v1"] = (
        "onboarding_learned_review_action_request.v1"
    )
    workspace_id: int = Field(gt=0)
    action: ReviewAction
    target_type: ReviewTargetType
    target_ref: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1)
    value_patch: dict[str, Any] = Field(default_factory=dict)
    merge_into_ref: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def merge_requires_target(self) -> OnboardingLearnedReviewActionRequest:
        if self.action == "merge" and not self.merge_into_ref:
            raise ValueError("merge_into_ref is required for merge action")
        return self


class OnboardingLearnedReviewActionResult(OnboardingLearnedReviewActionModel):
    schema_version: Literal["onboarding_learned_review_action_result.v1"] = (
        "onboarding_learned_review_action_result.v1"
    )
    action: ReviewAction
    target_type: ReviewTargetType
    target_ref: str
    applied_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    edited_count: int = Field(ge=0)
    merged_count: int = Field(ge=0)
    fact_ids: list[str] = Field(default_factory=list)


class OnboardingLearnedReviewActionService:
    """Owner actions for converting learned proposals into trusted Brain truth."""

    def __init__(self, *, repository: CommercialSpineRepository) -> None:
        self._repository = repository

    async def apply(
        self,
        request: OnboardingLearnedReviewActionRequest,
    ) -> OnboardingLearnedReviewActionResult:
        facts = await self._target_facts(request)
        if not facts:
            raise ValueError("learned review target not found")

        applied = 0
        rejected = 0
        edited = 0
        merged = 0
        changed_fact_ids: list[str] = []
        for fact in facts:
            if fact.status != "proposed":
                continue
            value = dict(fact.value)
            if request.action == "approve":
                value = _approved_value(fact_type=fact.fact_type, value=value)
                await self._update_fact(
                    request=request,
                    fact=fact,
                    status="active",
                    value=value,
                    approval_state="confirmed",
                    extraction_lifecycle_state="approved",
                )
                applied += 1
            elif request.action == "reject":
                await self._update_fact(
                    request=request,
                    fact=fact,
                    status="rejected",
                    value=value,
                    approval_state="rejected",
                    extraction_lifecycle_state="rejected",
                )
                rejected += 1
            elif request.action == "edit":
                edited_value = _deep_merge(value, request.value_patch)
                edited_value = _approved_value(
                    fact_type=fact.fact_type,
                    value=edited_value,
                )
                await self._update_fact(
                    request=request,
                    fact=fact,
                    status="active",
                    value=edited_value,
                    approval_state="confirmed",
                    extraction_lifecycle_state="approved",
                )
                edited += 1
                applied += 1
            else:
                merged_value = {
                    **value,
                    "merged_into_ref": request.merge_into_ref,
                    "merge_state": "merged",
                }
                await self._update_fact(
                    request=request,
                    fact=fact,
                    status="rejected",
                    value=merged_value,
                    approval_state="confirmed",
                    extraction_lifecycle_state="merged",
                )
                merged += 1
            changed_fact_ids.append(fact.fact_id)
        if request.target_type == "product" and (applied or edited):
            await CommerceCatalogCoreService(self._repository.session).project_from_business_brain(
                workspace_id=request.workspace_id,
                commit=False,
                rebuild_retrieval_index=True,
            )

        return OnboardingLearnedReviewActionResult(
            action=request.action,
            target_type=request.target_type,
            target_ref=request.target_ref,
            applied_count=applied,
            rejected_count=rejected,
            edited_count=edited,
            merged_count=merged,
            fact_ids=changed_fact_ids,
        )

    async def _target_facts(
        self,
        request: OnboardingLearnedReviewActionRequest,
    ):
        if request.target_type == "fact":
            fact = await self._repository.get_fact(
                workspace_id=request.workspace_id,
                fact_id=request.target_ref,
            )
            return tuple([fact] if fact is not None else [])
        return await self._repository.list_facts(
            workspace_id=request.workspace_id,
            entity_ref=request.target_ref,
            statuses=("proposed",),
            limit=100,
        )

    async def _update_fact(
        self,
        *,
        request: OnboardingLearnedReviewActionRequest,
        fact: Any,
        status: str,
        value: dict[str, Any],
        approval_state: ApprovalState,
        extraction_lifecycle_state: str,
    ) -> None:
        updated = await self._repository.update_fact_state(
            workspace_id=request.workspace_id,
            fact_id=fact.fact_id,
            status=status,
            value=value,
        )
        if updated is None:
            raise ValueError("learned review target not found")
        reviewed_at = datetime.now(UTC)
        if status == "active" and fact.supersedes_fact_id:
            await self._repository.mark_fact_status(
                workspace_id=request.workspace_id,
                fact_id=fact.supersedes_fact_id,
                status="superseded",
                valid_until=reviewed_at,
            )
        await self._repository.persist_update(
            BusinessBrainUpdate(
                update_id=(
                    f"learned-review:{request.action}:{fact.fact_id}:"
                    f"{request.correlation_id}"
                ),
                workspace_id=request.workspace_id,
                target_ref=f"fact:{fact.fact_id}",
                proposed_value=value,
                source="manual",
                approval_state=approval_state,
                risk_tier=fact.risk_tier,
                evidence_refs=list(fact.source_refs),
                idempotency_key=(
                    f"learned-review:{request.action}:{fact.fact_id}:"
                    f"{request.correlation_id}"
                ),
                applied_at=reviewed_at,
                actor_type="owner",
                actor_ref=request.actor_ref,
                correlation_id=request.correlation_id,
            )
        )
        await self._sync_extraction_candidate_lifecycle(
            request=request,
            fact=fact,
            lifecycle_state=extraction_lifecycle_state,
        )

    async def _sync_extraction_candidate_lifecycle(
        self,
        *,
        request: OnboardingLearnedReviewActionRequest,
        fact: Any,
        lifecycle_state: str,
    ) -> None:
        projections = await self._repository.list_projections(
            workspace_id=request.workspace_id,
            projection_type="extraction_candidate",
            limit=250,
        )
        for projection in projections:
            if not _projection_matches_fact(projection, fact, request):
                continue
            state = dict(projection.state)
            state.update(
                {
                    "lifecycle_state": lifecycle_state,
                    "owner_review_action": request.action,
                    "owner_reviewed_at": datetime.now(UTC).isoformat(),
                    "owner_actor_ref": request.actor_ref,
                    "owner_correlation_id": request.correlation_id,
                    "owner_reviewed_fact_id": fact.fact_id,
                }
            )
            await self._repository.upsert_projection(
                BusinessBrainProjection(
                    projection_ref=projection.projection_ref,
                    workspace_id=projection.workspace_id,
                    projection_type=projection.projection_type,
                    entity_ref=projection.entity_ref,
                    state=state,
                    source_refs=list(projection.source_refs),
                    degraded=projection.degraded,
                    degraded_reasons=list(projection.degraded_reasons),
                )
            )


def _approved_value(*, fact_type: str, value: dict[str, Any]) -> dict[str, Any]:
    approved = dict(value)
    if fact_type == "catalog_media":
        approved["approved"] = True
    return approved


def _projection_matches_fact(
    projection: BusinessBrainProjection,
    fact: Any,
    request: OnboardingLearnedReviewActionRequest,
) -> bool:
    candidate = projection.state.get("candidate")
    if not isinstance(candidate, dict):
        return False
    value = candidate.get("value")
    if isinstance(value, dict) and value.get("fact_id") == fact.fact_id:
        return True
    if isinstance(value, dict) and value.get("product_ref") == fact.fact_id:
        return True
    if (
        request.target_type == "product"
        and fact.fact_id != request.target_ref
        and fact.entity_ref != request.target_ref
        and fact.supersedes_fact_id != request.target_ref
    ):
        return False
    entity_ref = str(candidate.get("entity_ref") or "")
    return bool(entity_ref and entity_ref == fact.entity_ref)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged
