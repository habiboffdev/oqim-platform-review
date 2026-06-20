from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.llm_policy import FLASH_CHAIN, FLASH_LITE_CHAIN
from app.models.agent_skill import AgentSkill
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.learned_skill_candidate import LearnedSkillCandidate
from app.models.message import Message, SenderType
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import AgentSkillInput
from app.modules.agent_documents.renderer import render_skill_md
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.brain.contracts import (
    DistilledBatch,
    DistilledItem,
    LearnedSkill,
    SkillLearnReport,
    SynthesizedSkill,
)
from app.modules.brain.skill_learner import (
    DistilledPair,
    SkillLearnerService,
    TurnPair,
)
from app.modules.retrieval_core.indexing import RetrievalIndexEmbeddingResult


def _embedding_results(vectors: list[list[float]]) -> list[RetrievalIndexEmbeddingResult]:
    return [
        RetrievalIndexEmbeddingResult(vector, "test-embedding", "ready", None)
        for vector in vectors
    ]


def test_distilled_batch_holds_items() -> None:
    batch = DistilledBatch(items=[DistilledItem(index=0, summary="Owner quoted a price range when asked cost.", dimension="price")])
    assert batch.items[0].index == 0
    assert batch.items[0].dimension == "price"


def test_learned_skill_extends_synthesized_with_evidence() -> None:
    s = LearnedSkill(slug="price-handling", name="Price handling", trigger="customer asks price",
                     action="quote a range, confirm stock first", example_phrase="100-120k so'm",
                     dimension="price", confidence=0.9, evidence_conv_ids=[7, 12])
    assert isinstance(s, SynthesizedSkill)
    assert s.evidence_conv_ids == [7, 12]
    assert s.confidence == 0.9


def test_skill_learn_report_counts() -> None:
    r = SkillLearnReport(pairs_used=120, clusters=8, candidates=15)
    assert (r.pairs_used, r.clusters, r.candidates) == (120, 8, 15)


@pytest.mark.asyncio
async def test_learned_skill_candidate_row_roundtrips(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    cand = LearnedSkillCandidate(
        workspace_id=workspace.id,
        slug="price-handling",
        name="Price handling",
        trigger="customer asks price",
        action="quote a range, confirm stock first",
        example_phrase="100-120k so'm",
        dimension="price",
        confidence=0.9,
        evidence_conv_ids=[7, 12],
    )
    db_session.add(cand)
    await db_session.flush()
    row = (await db_session.execute(
        select(LearnedSkillCandidate).where(LearnedSkillCandidate.workspace_id == workspace.id)
    )).scalar_one()
    assert row.slug == "price-handling"
    assert row.status == "proposed"      # default
    assert row.source == "learned"       # default
    assert row.evidence_conv_ids == [7, 12]


async def _conversation(session: AsyncSession, workspace: Workspace) -> Conversation:
    customer = Customer(workspace_id=workspace.id, display_name="Mijoz")
    session.add(customer)
    await session.flush()
    conv = Conversation(workspace_id=workspace.id, customer_id=customer.id)
    session.add(conv)
    await session.flush()
    return conv


async def _msg(session, conv, *, sender, content, seq, when) -> Message:
    m = Message(conversation_id=conv.id, sender_type=sender, content=content,
                conversation_seq=seq, created_at=when, telegram_timestamp=when)
    session.add(m)
    await session.flush()
    return m


@pytest.mark.asyncio
async def test_acquire_pairs_matches_customer_then_owner(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    conv = await _conversation(db_session, workspace)
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    await _msg(db_session, conv, sender=SenderType.CUSTOMER.value, content="Narxi qancha? Tel: +998901234567", seq=1, when=t0)
    await _msg(db_session, conv, sender=SenderType.SELLER.value, content="100-120k so'm", seq=2, when=t0 + timedelta(minutes=2))
    service = SkillLearnerService(db_session)
    pairs = await service.acquire_pairs(workspace_id=workspace.id)
    assert len(pairs) == 1
    assert pairs[0].owner_text == "100-120k so'm"
    assert "+998901234567" not in pairs[0].customer_text  # PII stripped
    assert pairs[0].conversation_id == conv.id


@pytest.mark.asyncio
async def test_acquire_pairs_skips_when_gap_too_large(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    conv = await _conversation(db_session, workspace)
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    await _msg(db_session, conv, sender=SenderType.CUSTOMER.value, content="Bormi?", seq=1, when=t0)
    await _msg(db_session, conv, sender=SenderType.SELLER.value, content="Ha bor", seq=2, when=t0 + timedelta(minutes=30))
    service = SkillLearnerService(db_session)
    pairs = await service.acquire_pairs(workspace_id=workspace.id, window_minutes=10)
    assert pairs == []


@pytest.mark.asyncio
async def test_acquire_pairs_isolated_by_workspace(
    db_session: AsyncSession, workspace_b: Workspace
) -> None:
    service = SkillLearnerService(db_session)
    assert await service.acquire_pairs(workspace_id=workspace_b.id) == []


@pytest.mark.asyncio
async def test_distill_returns_one_summary_per_pair(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    pairs = [
        TurnPair(1, "Narxi qancha?", "100-120k so'm"),
        TurnPair(2, "Yetkazib berasizmi?", "Ha, 2 kunda"),
    ]
    fake = {"items": [
        {"index": 0, "summary": "Owner quoted a price range when asked cost.", "dimension": "price"},
        {"index": 1, "summary": "Owner promised 2-day delivery when asked.", "dimension": "delivery"},
    ]}
    service = SkillLearnerService(db_session)
    with patch("app.modules.brain.skill_learner.generate_structured_json",
               AsyncMock(return_value=fake)) as mock_llm:
        distilled = await service.distill(workspace_id=workspace.id, pairs=pairs)
    assert mock_llm.call_args.kwargs["workspace_id"] == workspace.id
    assert mock_llm.call_args.kwargs["chain"] is FLASH_LITE_CHAIN
    assert mock_llm.call_args.kwargs["system"].startswith("Distill each observed turn pair")
    prompt_cache = mock_llm.call_args.kwargs["prompt_cache"]
    assert prompt_cache["prompt_asset"]["prompt_id"] == "learning.skill_distill"
    assert prompt_cache["runtime_context"]["cache_scope"] == "learning.skill_distill"
    assert len(distilled) == 2
    assert distilled[0].summary.startswith("Owner quoted")
    assert distilled[0].conversation_id == 1
    assert distilled[1].dimension == "delivery"


@pytest.mark.asyncio
async def test_embed_and_cluster_separates_two_groups(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    distilled = [
        DistilledPair(1, "price A", "price", "", ""),
        DistilledPair(2, "price B", "price", "", ""),
        DistilledPair(3, "delivery A", "delivery", "", ""),
        DistilledPair(4, "delivery B", "delivery", "", ""),
    ]
    vectors = [
        [1.0, 0.0, 0.0, 0.0], [0.9, 0.1, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.9, 0.1],
    ]
    with patch("app.modules.brain.skill_learner.RetrievalIndexEmbeddingService") as mock_es:
        mock_es.return_value.embed_texts = AsyncMock(return_value=_embedding_results(vectors))
        service = SkillLearnerService(db_session)
        labels = await service.embed_and_cluster(distilled)
    assert len(labels) == 4
    assert labels[0] == labels[1]
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]


@pytest.mark.asyncio
async def test_embed_and_cluster_handles_tiny_input(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    distilled = [DistilledPair(1, "only one", "general", "", "")]
    with patch("app.modules.brain.skill_learner.RetrievalIndexEmbeddingService") as mock_es:
        mock_es.return_value.embed_texts = AsyncMock(return_value=_embedding_results([[1.0, 0.0]]))
        service = SkillLearnerService(db_session)
        labels = await service.embed_and_cluster(distilled)
    assert labels == [0]


@pytest.mark.asyncio
async def test_synthesize_one_skill_per_cluster_with_evidence(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    distilled = [
        DistilledPair(11, "price A", "price", "", "100k"),
        DistilledPair(12, "price B", "price", "", "120k"),
        DistilledPair(33, "delivery A", "delivery", "", "2 kun"),
    ]
    labels = [0, 0, 1]
    responses = [
        {"slug": "Price Handling", "name": "Price handling", "trigger": "asks price",
         "action": "quote a range", "example_phrase": "100-120k", "dimension": "price", "confidence": 0.9},
        {"slug": "delivery-promise", "name": "Delivery promise", "trigger": "asks delivery",
         "action": "promise 2 days", "example_phrase": "2 kun", "dimension": "delivery", "confidence": 0.8},
    ]
    service = SkillLearnerService(db_session)
    with patch("app.modules.brain.skill_learner.generate_structured_json",
               AsyncMock(side_effect=responses)) as mock_llm:
        skills = await service.synthesize(workspace_id=workspace.id, distilled=distilled, labels=labels)
    assert mock_llm.call_args.kwargs["chain"] is FLASH_CHAIN
    assert mock_llm.call_args.kwargs["system"].startswith("You are given distilled examples")
    prompt_cache = mock_llm.call_args.kwargs["prompt_cache"]
    assert prompt_cache["prompt_asset"]["prompt_id"] == "learning.skill_synthesis"
    assert prompt_cache["runtime_context"]["cache_scope"] == "learning.skill_synthesis"
    assert len(skills) == 2
    by_slug = {s.slug: s for s in skills}
    assert "price-handling" in by_slug                       # slug normalized to kebab
    assert set(by_slug["price-handling"].evidence_conv_ids) == {11, 12}  # code-attached evidence
    assert by_slug["delivery-promise"].evidence_conv_ids == [33]


@pytest.mark.asyncio
async def test_dedup_and_rank_drops_near_duplicate_keeps_higher_confidence(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    skills = [
        LearnedSkill(slug="a", name="A", trigger="asks price", action="quote range", confidence=0.7),
        LearnedSkill(slug="b", name="B", trigger="asks price", action="quote range", confidence=0.9),
        LearnedSkill(slug="c", name="C", trigger="asks delivery", action="promise 2 days", confidence=0.6),
    ]
    vecs = [
        [1.0, 0.0], [1.0, 0.0],
        [0.0, 1.0],
    ]
    with patch("app.modules.brain.skill_learner.RetrievalIndexEmbeddingService") as mock_es:
        mock_es.return_value.embed_texts = AsyncMock(return_value=_embedding_results(vecs))
        service = SkillLearnerService(db_session)
        ranked = await service.dedup_and_rank(skills)
    slugs = [s.slug for s in ranked]
    assert "b" in slugs and "a" not in slugs
    assert "c" in slugs
    assert ranked[0].slug == "b"


@pytest.mark.asyncio
async def test_learn_persists_candidates_end_to_end(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    conv = await _conversation(db_session, workspace)
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    seq = 1
    for i in range(6):
        await _msg(db_session, conv, sender=SenderType.CUSTOMER.value, content=f"savol {i}", seq=seq, when=t0 + timedelta(minutes=seq))
        seq += 1
        await _msg(db_session, conv, sender=SenderType.SELLER.value, content=f"javob {i}", seq=seq, when=t0 + timedelta(minutes=seq))
        seq += 1
    distill_resp = {"items": [{"index": i, "summary": f"owner answered {i}", "dimension": "general"} for i in range(6)]}
    synth_resp = {"slug": "general-help", "name": "General help", "trigger": "asks", "action": "answer", "example_phrase": "javob", "dimension": "general", "confidence": 0.8}
    with patch("app.modules.brain.skill_learner.generate_structured_json",
               AsyncMock(side_effect=[distill_resp, synth_resp, synth_resp, synth_resp, synth_resp])), \
         patch("app.modules.brain.skill_learner.RetrievalIndexEmbeddingService") as mock_es:
        mock_es.return_value.embed_texts = AsyncMock(
            side_effect=lambda texts, **_kwargs: _embedding_results(
                [[float(i), 0.0] for i in range(len(texts))]
            )
        )
        service = SkillLearnerService(db_session)
        report = await service.learn(workspace_id=workspace.id)
    await db_session.flush()
    rows = (await db_session.execute(select(LearnedSkillCandidate).where(LearnedSkillCandidate.workspace_id == workspace.id))).scalars().all()
    assert report.pairs_used == 6
    assert report.candidates == len(rows)
    assert all(r.status == "proposed" and r.source == "learned" for r in rows)


@pytest.mark.asyncio
async def test_learn_returns_empty_when_too_few_pairs(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    service = SkillLearnerService(db_session)
    with patch("app.modules.brain.skill_learner.generate_structured_json", AsyncMock()) as mock_llm:
        report = await service.learn(workspace_id=workspace.id)
    mock_llm.assert_not_awaited()
    assert report == SkillLearnReport(pairs_used=0, clusters=0, candidates=0)


@pytest.mark.asyncio
async def test_learn_then_approve_promotes_to_agent_skill(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    conv = await _conversation(db_session, workspace)
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    seq = 1
    for i in range(6):
        await _msg(db_session, conv, sender=SenderType.CUSTOMER.value, content=f"savol {i}", seq=seq, when=t0 + timedelta(minutes=seq))
        seq += 1
        await _msg(db_session, conv, sender=SenderType.SELLER.value, content=f"javob {i}", seq=seq, when=t0 + timedelta(minutes=seq))
        seq += 1
    distill_resp = {"items": [{"index": i, "summary": f"owner answered {i}", "dimension": "general"} for i in range(6)]}
    synth_resp = {"slug": "general-help", "name": "General help", "trigger": "asks", "action": "answer", "example_phrase": "javob", "dimension": "general", "confidence": 0.8}
    with patch("app.modules.brain.skill_learner.generate_structured_json",
               AsyncMock(side_effect=[distill_resp] + [synth_resp] * 8)), \
         patch("app.modules.brain.skill_learner.RetrievalIndexEmbeddingService") as mock_es:
        mock_es.return_value.embed_texts = AsyncMock(
            side_effect=lambda texts, **_kwargs: _embedding_results(
                [[float(i), 0.0] for i in range(len(texts))]
            )
        )
        service = SkillLearnerService(db_session)
        report = await service.learn(workspace_id=workspace.id)
    await db_session.flush()
    assert report.candidates >= 1

    cand = (await db_session.execute(select(LearnedSkillCandidate).where(LearnedSkillCandidate.workspace_id == workspace.id))).scalars().first()
    docs = AgentDocumentService(db_session)
    await docs.upsert_skill(workspace_id=workspace.id, payload=AgentSkillInput(
        slug=cand.slug, name=cand.name, description=cand.action, instructions=cand.action, when_to_use=cand.trigger,
    ))
    await db_session.flush()
    skill = (await db_session.execute(select(AgentSkill).where(AgentSkill.workspace_id == workspace.id, AgentSkill.slug == cand.slug))).scalar_one()
    rendered = render_skill_md(skill)
    assert "SKILL.md" in rendered.markdown
    assert cand.name in rendered.markdown


async def _conversation_with_contact(
    session: AsyncSession, workspace: Workspace, *, contact_type: str
) -> Conversation:
    customer = Customer(workspace_id=workspace.id, display_name="C", contact_type=contact_type)
    session.add(customer)
    await session.flush()
    conv = Conversation(workspace_id=workspace.id, customer_id=customer.id)
    session.add(conv)
    await session.flush()
    return conv


@pytest.mark.asyncio
async def test_acquire_pairs_filters_by_contact_type(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    # A seller agent must learn from customer chats, not the owner's personal ones.
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    cust = await _conversation_with_contact(db_session, workspace, contact_type="customer")
    await _msg(db_session, cust, sender=SenderType.CUSTOMER.value, content="Narxi qancha?", seq=1, when=t0)
    await _msg(db_session, cust, sender=SenderType.SELLER.value, content="100k", seq=2, when=t0 + timedelta(minutes=1))
    pers = await _conversation_with_contact(db_session, workspace, contact_type="personal")
    await _msg(db_session, pers, sender=SenderType.CUSTOMER.value, content="KAIST haqida", seq=1, when=t0)
    await _msg(db_session, pers, sender=SenderType.SELLER.value, content="Ariza berdim", seq=2, when=t0 + timedelta(minutes=1))
    service = SkillLearnerService(db_session)

    customer_only = await service.acquire_pairs(workspace_id=workspace.id, contact_types=("customer",))
    assert [p.conversation_id for p in customer_only] == [cust.id]

    personal_only = await service.acquire_pairs(workspace_id=workspace.id, contact_types=("personal",))
    assert [p.conversation_id for p in personal_only] == [pers.id]

    unfiltered = await service.acquire_pairs(workspace_id=workspace.id)
    assert {p.conversation_id for p in unfiltered} == {cust.id, pers.id}


@pytest.mark.asyncio
async def test_learning_scope_maps_agent_kind_to_contact_types(
    db_session: AsyncSession, workspace: Workspace
) -> None:
    from app.models.agent import Agent

    seller = Agent(workspace_id=workspace.id, name="Sotuvchi", agent_type="seller")
    personal = Agent(workspace_id=workspace.id, name="Shaxsiy", agent_type="custom")
    db_session.add_all([seller, personal])
    await db_session.flush()
    service = SkillLearnerService(db_session)

    assert await service._learning_scope(workspace.id, seller.id) == ("seller_agent", ("customer",))
    assert await service._learning_scope(workspace.id, personal.id) == ("custom_agent", ("personal", "work"))
    # No agent → no filter (legacy/ad-hoc), seller framing.
    assert await service._learning_scope(workspace.id, None) == ("seller_agent", None)
