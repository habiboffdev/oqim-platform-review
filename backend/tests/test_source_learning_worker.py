from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.contracts import OnboardingLearningBootstrapInput
from app.modules.onboarding_learning.service import OnboardingLearningBootstrapService
from app.modules.onboarding_learning.source_runtime import (
    OnboardingSourceLearningRuntimeService,
    OnboardingSourceRuntimeItem,
    OnboardingSourceRuntimeResult,
)
from app.services.source_learning_worker import (
    SourceLearningWorker,
    claim_due_source_learning_jobs,
)


async def _queue_source_for_worker(
    *,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile={
                "sources": {
                    "items": [
                        {
                            "kind": "text",
                            "label": "Worker manbasi",
                            "text": "Worker bu manbani requestdan tashqarida o‘qiydi.",
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )
    await OnboardingSourceLearningRuntimeService(
        repository=repository,
    ).queue_workspace_sources(
        workspace_id=workspace.id,
        limit=1,
        max_attempts=3,
    )


async def test_source_learning_worker_claims_queued_projection_once(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    await _queue_source_for_worker(db_session=db_session, workspace=workspace)

    first_claim = await claim_due_source_learning_jobs(
        db_session,
        lease_owner="source-learning-test",
        limit=5,
        now=datetime(2026, 5, 18, tzinfo=UTC),
    )
    second_claim = await claim_due_source_learning_jobs(
        db_session,
        lease_owner="source-learning-test-2",
        limit=5,
        now=datetime(2026, 5, 18, tzinfo=UTC),
    )
    projections = await CommercialSpineRepository(db_session).list_projections(
        workspace_id=workspace.id,
        projection_type="business_source_learning",
        limit=10,
    )

    assert len(first_claim) == 1
    assert first_claim[0].workspace_id == workspace.id
    assert first_claim[0].source_refs == ("onboarding:source:0",)
    assert second_claim == []
    assert projections[0].state["lease_owner"] == "source-learning-test"
    assert projections[0].state["leased_until"]


async def test_source_learning_worker_processes_claimed_source_refs(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    await _queue_source_for_worker(db_session=db_session, workspace=workspace)
    calls: list[dict[str, Any]] = []

    async def fake_process_workspace_sources(
        self: OnboardingSourceLearningRuntimeService,  # noqa: ARG001
        **kwargs: Any,
    ) -> OnboardingSourceRuntimeResult:
        calls.append(dict(kwargs))
        return OnboardingSourceRuntimeResult(
            processed_count=1,
            review_ready_count=1,
            learned_count=0,
            retrying_count=0,
            failed_count=0,
            skipped_count=0,
            items=[
                OnboardingSourceRuntimeItem(
                    source_ref="onboarding:source:0",
                    source_kind="text",
                    source_fact_id="source-fact",
                    status="review_ready",
                    attempt_count=1,
                    degraded_reasons=[],
                )
            ],
        )

    monkeypatch.setattr(
        OnboardingSourceLearningRuntimeService,
        "process_workspace_sources",
        fake_process_workspace_sources,
    )

    @contextlib.asynccontextmanager
    async def db_factory():
        yield db_session

    processed = await SourceLearningWorker(
        db_factory=db_factory,
        redis=None,
        batch_size=4,
        max_parallelism=1,
    ).run_once(now=datetime(2026, 5, 18, tzinfo=UTC))

    assert processed == 1
    assert calls[0]["workspace_id"] == workspace.id
    assert calls[0]["source_refs"] == {"onboarding:source:0"}
