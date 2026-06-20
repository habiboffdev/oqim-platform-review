import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.workspace import Workspace
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.evals.buyer_intent_eval import run_buyer_intent_eval_suite


@pytest.mark.asyncio
async def test_buyer_intent_eval_proves_vertical_neutral_cases(
    db_session,
    workspace,
):
    report = await run_buyer_intent_eval_suite(
        repository=CommercialSpineRepository(db_session),
        workspace_id=workspace.id,
    )

    assert report.suite == "buyer-intent"
    assert report.live is False
    assert report.concurrency == 1
    assert report.pass_rate == 1.0
    assert report.total_runs == 5
    assert report.passed_runs == 5
    assert report.hard_failure_count == 0
    assert report.provider_error_count == 0
    assert report.rejected_candidate_count == 0
    assert report.median_case_duration_ms >= 0
    assert report.p95_case_duration_ms >= report.median_case_duration_ms
    assert report.max_case_duration_ms >= report.p95_case_duration_ms
    assert {result.case_id for result in report.results} == {
        "medicine_media_inquiry",
        "course_vague_offer",
        "real_estate_negotiation",
        "payment_claim",
        "warranty_faq",
    }
    assert {result.detected_intent for result in report.results} >= {
        "media_inquiry",
        "payment",
        "negotiation",
    }
    assert all(result.llm_trace_count == 1 for result in report.results)


@pytest.mark.asyncio
async def test_buyer_intent_eval_requires_session_factory_for_parallel_runs(
    db_session,
    workspace,
):
    with pytest.raises(ValueError, match="session_factory"):
        await run_buyer_intent_eval_suite(
            repository=CommercialSpineRepository(db_session),
            workspace_id=workspace.id,
            concurrency=2,
        )


@pytest.mark.asyncio
async def test_buyer_intent_eval_proves_parallel_profile_load(
    engine: AsyncEngine,
):
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as setup:
        workspace = Workspace(
            name="Buyer Intent Load Eval",
            phone_number="+998900000771",
            password_hash="x",
        )
        setup.add(workspace)
        await setup.commit()
        workspace_id = workspace.id

    async with session_factory() as session:
        report = await run_buyer_intent_eval_suite(
            repository=CommercialSpineRepository(session),
            workspace_id=workspace_id,
            repetitions=3,
            concurrency=5,
            session_factory=session_factory,
        )
        await session.rollback()

    assert report.concurrency == 5
    assert report.total_runs == 15
    assert report.passed_runs == 15
    assert report.hard_failure_count == 0
    assert report.provider_error_count == 0
    assert report.rejected_candidate_count == 0
    assert report.p95_case_duration_ms <= report.max_case_duration_ms
