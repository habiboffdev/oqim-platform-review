from __future__ import annotations

import base64
import contextlib
import asyncio
import json
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial_spine import BusinessBrainFactRecord, BusinessBrainIndexRecord
from app.models.workspace import Workspace
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import ContextualRetrievalRequest
from app.modules.commercial_spine.contracts import BusinessBrainProjection
from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.contracts import OnboardingLearningBootstrapInput
from app.modules.onboarding_learning.service import OnboardingLearningBootstrapService
from app.modules.onboarding_learning.source_runtime import (
    OnboardingSourceLearningRuntimeService,
    OnboardingSourceRuntimeItem,
    _SourceJob,
    _runtime_source_payload,
)
from app.modules.onboarding_learning.source_progress import (
    build_onboarding_source_learning_projection,
)
from app.modules.onboarding_learning.source_ingestion import (
    OnboardingSourceIngestionService,
)

_PDF_BYTES = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >> endobj
4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
5 0 obj << /Length 97 >> stream
BT /F1 18 Tf 72 720 Td (Atlas koylak narxi 250000 UZS. Rang: yashil. Olcham: M.) Tj ET
endstream endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000241 00000 n
0000000311 00000 n
trailer << /Root 1 0 R /Size 6 >>
startxref
459
%%EOF
"""


@pytest.fixture(autouse=True)
def _fake_source_unit_embeddings(monkeypatch) -> None:
    async def fake_embed_text(_self, _text: str, intent: str = "document") -> list[float]:
        value = 0.03 if intent == "document" else 0.04
        return [value] * 3072

    monkeypatch.setattr(
        "app.brain.embedding_service.EmbeddingService.embed_text",
        fake_embed_text,
    )


def _is_contextualization_request(request) -> bool:
    return request.output_schema_name == "SourceUnitContextualizationOutput"


def _contextualization_response(request) -> LLMProviderResponse:
    return LLMProviderResponse(
        text=json.dumps(
            {
                "schema_version": "source_unit_contextualization_output.v1",
                "context": (
                    "Onboarding runtime source unit for retrieval. "
                    f"{str(request.input_payload.get('source_text') or '')[:240]}"
                ),
            }
        ),
        model_used="test-contextualizer",
    )


def test_runtime_source_payload_routes_uploaded_csv_and_xlsx_to_spreadsheet() -> None:
    csv_kind, csv_payload, csv_content = _runtime_source_payload({
        "kind": "file",
        "input": {
            "file_name": "price-list.csv",
            "content_type": "text/csv",
            "content_base64": base64.b64encode(b"name,price\nAtlas,250000").decode(),
        },
    })
    xlsx_kind, xlsx_payload, xlsx_content = _runtime_source_payload({
        "kind": "file",
        "input": {
            "file_name": "catalog.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "content_base64": base64.b64encode(b"xlsx-bytes").decode(),
        },
    })

    assert csv_kind == "spreadsheet"
    assert csv_payload["file_name"] == "price-list.csv"
    assert csv_content is not None
    assert xlsx_kind == "spreadsheet"
    assert xlsx_payload["file_name"] == "catalog.xlsx"
    assert xlsx_content is not None


async def test_onboarding_source_runtime_processes_sources_with_bounded_parallelism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    max_active = 0
    started: list[str] = []

    class _FakeSession:
        async def commit(self) -> None:
            return None

    @contextlib.asynccontextmanager
    async def session_factory():
        yield _FakeSession()

    async def fake_process(
        self: OnboardingSourceLearningRuntimeService,  # noqa: ARG001
        *,
        fact: Any,
        source_ref: str,
        projection: BusinessBrainProjection | None,  # noqa: ARG001
        correlation_id: str,  # noqa: ARG001
        max_attempts: int,  # noqa: ARG001
    ) -> OnboardingSourceRuntimeItem:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started.append(source_ref)
        await asyncio.sleep(0.01)
        active -= 1
        return OnboardingSourceRuntimeItem(
            source_ref=source_ref,
            source_kind="text",
            source_fact_id=str(getattr(fact, "fact_id")),
            status="learned",
            attempt_count=1,
            degraded_reasons=[],
        )

    monkeypatch.setattr(
        OnboardingSourceLearningRuntimeService,
        "_process_source_fact",
        fake_process,
    )
    runtime = OnboardingSourceLearningRuntimeService(
        repository=CommercialSpineRepository(_FakeSession()),  # type: ignore[arg-type]
        session_factory=session_factory,
        max_parallelism=2,
    )
    jobs = [
        _SourceJob(
            fact=type("Fact", (), {"fact_id": f"fact-{index}"})(),
            source_ref=f"onboarding:source:{index}",
            source_kind="text",
            source_fact_id=f"fact-{index}",
        )
        for index in range(5)
    ]

    result = await runtime._process_jobs(
        jobs=jobs,
        correlation_id="parallel-proof",
        max_attempts=2,
    )

    assert [item.source_ref for item in result] == [
        f"onboarding:source:{index}" for index in range(5)
    ]
    assert set(started) == {f"onboarding:source:{index}" for index in range(5)}
    assert max_active == 2


def test_runtime_source_payload_accepts_past_conversation_source() -> None:
    kind, payload, content = _runtime_source_payload({
        "kind": "past_conversation",
        "input": {
            "conversation_id": 44,
            "turns": [
                {"sender_type": "customer", "content": "Kurs qancha?"},
                {"sender_type": "seller", "content": "6 hafta, 700 ming so'm."},
            ],
        },
    })

    assert kind == "past_conversation"
    assert payload["conversation_id"] == 44
    assert len(payload["turns"]) == 2
    assert content is None


async def test_onboarding_source_runtime_learns_queued_source_into_reviewable_brain(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    calls = []

    async def provider(request) -> LLMProviderResponse:
        calls.append(request)
        if _is_contextualization_request(request):
            return _contextualization_response(request)
        unit_ref = request.input_payload["source_units"][0]["unit_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "fact_id": "knowledge:onboarding:delivery",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "business:delivery",
                            "value": {
                                "topic": "yetkazib berish",
                                "answer": "Toshkent ichida yetkazib berish bor.",
                            },
                            "confidence": 0.83,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile={
                "sources": {
                    "items": [
                        {
                            "kind": "text",
                            "label": "Yetkazib berish qoidasi",
                            "text": "Toshkent ichida yetkazib berish bor.",
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )

    result = await OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-runtime",
    )

    learned_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:onboarding:delivery",
    )
    source_facts = await repository.list_facts(
        workspace_id=workspace.id,
        fact_type="business_source_fact",
        limit=20,
    )
    learning_projections = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="business_source_learning",
        limit=20,
    )
    source_learning = build_onboarding_source_learning_projection(
        source_facts=source_facts,
        source_learning_projections=learning_projections,
    )
    retrieval = await BusinessBrainMemoryService(repository=repository).retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["business_source_fact"],
            query_text="yetkazib berish",
            include_source_units=True,
        )
    )

    assert result.processed_count == 1
    assert result.review_ready_count == 1
    assert result.failed_count == 0
    assert any(_is_contextualization_request(call) for call in calls)
    assert retrieval.candidates[0].source_units[0].source_text.startswith(
        "LLM contextualized source unit"
    )
    assert retrieval.candidates[0].source_units[0].embedding_state == "ready"
    assert learned_fact is not None
    assert learned_fact.status == "proposed"
    # Onboarding no longer embeds inline. persist_fact left the learned fact queued
    # (index_state='pending'); the BrainIndexReconciler — not onboarding — embeds it
    # on its next tick. So right after learning there are NO index records yet.
    pending_state = await db_session.scalar(
        select(BusinessBrainFactRecord.index_state).where(
            BusinessBrainFactRecord.workspace_id == workspace.id,
            BusinessBrainFactRecord.fact_id == "knowledge:onboarding:delivery",
        )
    )
    assert pending_state == "pending"
    indexed_structured = await db_session.scalar(
        select(func.count())
        .select_from(BusinessBrainIndexRecord)
        .where(
            BusinessBrainIndexRecord.workspace_id == workspace.id,
            BusinessBrainIndexRecord.fact_id == "knowledge:onboarding:delivery",
        )
    )
    assert indexed_structured == 0
    assert source_learning["status"] == "needs_review"
    assert source_learning["summary"]["needs_review"] == 1
    assert source_learning["sources"][0]["source_ref"] == "onboarding:source:0"
    assert source_learning["sources"][0]["status"] == "needs_review"
    event_stages = [event.get("stage") for event in source_learning["events"]]
    assert "ingesting" in event_stages
    assert "extracting" in event_stages
    assert "review_ready" in event_stages
    extracting_event = next(
        event for event in source_learning["events"] if event.get("stage") == "extracting"
    )
    assert extracting_event["detail_uz"].startswith("1 ta dalil tayyor")
    assert source_learning["events"][-1]["title_uz"] == "Tasdiq kutmoqda: Yetkazib berish qoidasi"
    assert "1 ta bilim taklifi" in source_learning["events"][-1]["detail_uz"]
    learning_projection = next(
        projection for projection in learning_projections
        if projection.projection_ref == "business_source_learning:onboarding:source:0"
    )
    runtime_events = learning_projection.state["events"]
    extracting_runtime_event = next(
        event for event in runtime_events if event["stage"] == "extracting"
    )
    review_ready_runtime_event = next(
        event for event in runtime_events if event["stage"] == "review_ready"
    )
    assert extracting_runtime_event["source_unit_count"] == 1
    assert review_ready_runtime_event["memory_candidate_count"] == 1


async def test_onboarding_source_runtime_queued_marker_still_processes(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        if _is_contextualization_request(request):
            return _contextualization_response(request)
        unit_ref = request.input_payload["source_units"][0]["unit_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "fact_id": "knowledge:onboarding:queue-proof",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "business:delivery",
                            "value": {
                                "topic": "yetkazib berish",
                                "answer": "Yangi manba navbatdan keyin ham o‘qiladi.",
                            },
                            "confidence": 0.82,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile={
                "sources": {
                    "items": [
                        {
                            "kind": "text",
                            "label": "Navbatdagi manba",
                            "text": "Yangi manba navbatdan keyin ham o‘qiladi.",
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )
    runtime = OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    queued = await runtime.queue_workspace_sources(
        workspace_id=workspace.id,
        limit=1,
        max_attempts=3,
    )
    source_facts = await repository.list_facts(
        workspace_id=workspace.id,
        fact_type="business_source_fact",
        limit=20,
    )
    learning_projections = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="business_source_learning",
        limit=20,
    )
    queued_learning = build_onboarding_source_learning_projection(
        source_facts=source_facts,
        source_learning_projections=learning_projections,
    )

    assert queued.items[0].status == "queued"
    assert queued_learning["status"] == "learning"
    assert queued_learning["events"][-1]["stage"] == "queued"
    assert queued_learning["events"][-1]["title_uz"] == "Navbatga qo‘yildi: Navbatdagi manba"

    result = await runtime.process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-runtime-background",
        limit=1,
    )

    assert result.review_ready_count == 1
    assert result.items[0].status == "review_ready"


async def test_onboarding_source_runtime_enqueue_does_not_regress_active_queue(
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
                            "label": "Idempotent manba",
                            "text": "Bu manba qayta navbatga tushmasligi kerak.",
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )
    runtime = OnboardingSourceLearningRuntimeService(repository=repository)

    first = await runtime.queue_workspace_sources(workspace_id=workspace.id, limit=1)
    second = await runtime.queue_workspace_sources(workspace_id=workspace.id, limit=1)
    projections = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="business_source_learning",
        limit=20,
    )

    assert first.items[0].status == "queued"
    assert second.items[0].status == "skipped"
    assert projections[0].state["status"] == "queued"
    assert projections[0].state["stage"] == "queued"


async def test_onboarding_source_runtime_learns_uploaded_pdf_bytes_from_queue(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        if _is_contextualization_request(request):
            return _contextualization_response(request)
        assert request.content_parts == [
            {
                "kind": "inline_data",
                "mime_type": "application/pdf",
                "data_base64": base64.b64encode(_PDF_BYTES).decode(),
                "file_name": "catalog.pdf",
                "upload_strategy": "file_api",
            }
        ]
        assert "Atlas koylak" in request.input_payload["source_units"][0]["text"]
        unit_ref = request.input_payload["source_units"][0]["unit_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "fact_id": "knowledge:onboarding:atlas-price",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "catalog:atlas-koylak",
                            "value": {
                                "topic": "Atlas ko'ylak",
                                "answer": "Atlas ko'ylak narxi 250000 UZS.",
                            },
                            "confidence": 0.86,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile={
                "sources": {
                    "items": [
                        {
                            "kind": "file",
                            "label": "Katalog PDF",
                            "file_name": "catalog.pdf",
                            "content_type": "application/pdf",
                            "content_base64": base64.b64encode(_PDF_BYTES).decode(),
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )

    result = await OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-pdf-runtime",
    )
    learned_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:onboarding:atlas-price",
    )

    assert result.review_ready_count == 1
    assert learned_fact is not None
    assert learned_fact.status == "proposed"
    assert learned_fact.source_refs[0].startswith(
        "source_unit:business_source:onboarding:source:0:ingested"
    )


async def test_onboarding_source_runtime_sends_uploaded_image_bytes_to_source_learning(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    image_bytes = b"fake-image-bytes"

    async def provider(request) -> LLMProviderResponse:
        if _is_contextualization_request(request):
            return _contextualization_response(request)
        assert request.content_parts == [
            {
                "kind": "inline_data",
                "mime_type": "image/png",
                "data_base64": base64.b64encode(image_bytes).decode(),
            }
        ]
        assert request.input_payload["source_units"] == []
        media_ref = request.input_payload["media_assets"][0]["media_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "fact_id": "knowledge:onboarding:screenshot-program",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "program:screenshot",
                            "value": {
                                "topic": "Program screenshot",
                                "summary": "Uploaded screenshot can be read by Gemini as image evidence.",
                            },
                            "confidence": 0.78,
                            "risk_tier": "medium",
                            "evidence_refs": [media_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile={
                "sources": {
                    "items": [
                        {
                            "kind": "screenshot",
                            "label": "program-screenshot.png",
                            "file_name": "program-screenshot.png",
                            "content_type": "image/png",
                            "content_base64": base64.b64encode(image_bytes).decode(),
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )

    result = await OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-image-runtime",
    )
    learned_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:onboarding:screenshot-program",
    )

    assert result.review_ready_count == 1
    assert learned_fact is not None
    assert learned_fact.source_refs[0].startswith(
        "source_media:onboarding:source:0:screenshot"
    )


async def test_onboarding_source_runtime_fetches_telegram_channel_messages_before_learning(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        if _is_contextualization_request(request):
            return _contextualization_response(request)
        unit = request.input_payload["source_units"][0]
        assert "Yashil atlas ko'ylak" in unit["text"]
        assert request.input_payload["media_assets"][0]["url"] == "https://cdn.example/atlas.jpg"
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "fact_id": "knowledge:onboarding:channel-atlas",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "catalog:atlas-koylak",
                            "value": {
                                "topic": "Telegram kanal katalogi",
                                "answer": "Yashil atlas ko'ylak kanalda bor.",
                            },
                            "confidence": 0.82,
                            "risk_tier": "medium",
                            "evidence_refs": [
                                unit["unit_ref"],
                                request.input_payload["media_assets"][0]["media_ref"],
                            ],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    async def fetch_channel_messages(*, workspace_id: int, source_payload: dict) -> list[dict]:
        assert workspace_id == workspace.id
        assert source_payload["handle"] == "@nafis_shop"
        assert source_payload["date_from"] == "2026-05-01"
        assert source_payload["date_to"] == "2026-05-18"
        return [
            {
                "message_id": "701",
                "text": "Yashil atlas ko'ylak. Narxi 250000 UZS.",
                "media_type": "photo",
                "media_metadata": {
                    "mime_type": "image/jpeg",
                    "url": "https://cdn.example/atlas.jpg",
                },
            }
        ]

    repository = CommercialSpineRepository(db_session)
    await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile={
                "sources": {
                    "items": [
                        {
                            "kind": "telegram_channel",
                            "label": "Kanal",
                            "handle": "@nafis_shop",
                            "date_from": "2026-05-01",
                            "date_to": "2026-05-18",
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )

    result = await OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
        fetch_telegram_channel_messages=fetch_channel_messages,
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-channel-runtime",
    )

    assert result.review_ready_count == 1
    assert await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:onboarding:channel-atlas",
    )


async def test_onboarding_source_runtime_transcribes_voice_file_before_learning(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def transcribe_audio(content: bytes, content_type: str) -> str:
        assert content == b"voice-bytes"
        assert content_type == "audio/ogg"
        return "Yetkazib berish so'ralsa, avval tuman va telefon so'ra."

    async def provider(request) -> LLMProviderResponse:
        if _is_contextualization_request(request):
            return _contextualization_response(request)
        assert request.content_parts == [
            {
                "kind": "inline_data",
                "mime_type": "audio/ogg",
                "data_base64": base64.b64encode(b"voice-bytes").decode(),
            }
        ]
        unit = request.input_payload["source_units"][0]
        assert request.input_payload["source_kind"] == "voice_note"
        assert "avval tuman va telefon" in unit["text"]
        assert request.input_payload["media_assets"][0]["media_type"] == "audio"
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "fact_id": "seller_rule:onboarding:delivery-phone",
                            "fact_type": "seller_rule_fact",
                            "entity_ref": "seller_rule:delivery-phone",
                            "value": {
                                "rule": "Yetkazib berish so'ralsa, avval tuman va telefon so'ra.",
                            },
                            "confidence": 0.88,
                            "risk_tier": "medium",
                            "evidence_refs": [
                                unit["unit_ref"],
                                request.input_payload["media_assets"][0]["media_ref"],
                            ],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile={
                "sources": {
                    "items": [
                        {
                            "kind": "voice_note",
                            "label": "Yetkazish qoidasi",
                            "file_name": "rule.ogg",
                            "content_type": "audio/ogg",
                            "content_base64": base64.b64encode(b"voice-bytes").decode(),
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )

    gateway = LLMGateway(repository=repository, provider=provider)
    result = await OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=gateway,
        ingestion=OnboardingSourceIngestionService(
            repository=repository,
            transcribe_audio=transcribe_audio,
            gateway=gateway,
        ),
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-voice-runtime",
    )
    learned_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="seller_rule:onboarding:delivery-phone",
    )

    assert result.review_ready_count == 1
    assert learned_fact is not None
    assert learned_fact.status == "proposed"


async def test_onboarding_source_runtime_is_workspace_scoped_on_retry(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    other_workspace = Workspace(
        phone_number="+998901111111",
        name="Other shop",
        type="fashion",
    )
    db_session.add(other_workspace)
    await db_session.flush()

    async def provider(_request) -> LLMProviderResponse:
        raise TimeoutError("provider timed out")

    repository = CommercialSpineRepository(db_session)
    for target in (workspace, other_workspace):
        await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
            OnboardingLearningBootstrapInput(
                workspace_id=target.id,
                profile={
                    "sources": {
                        "items": [
                            {
                                "kind": "text",
                                "label": "FAQ",
                                "text": f"Workspace {target.id} FAQ",
                            }
                        ]
                    }
                },
                actor_ref="owner",
            )
        )

    result = await OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-tenant",
        max_attempts=2,
    )
    first_projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref="business_source_learning:onboarding:source:0",
    )
    other_projection = await repository.get_projection(
        workspace_id=other_workspace.id,
        projection_ref="business_source_learning:onboarding:source:0",
    )

    assert result.retrying_count == 1
    assert first_projection is not None
    assert first_projection.state["attempt_count"] == 1
    assert other_projection is None


async def test_onboarding_source_runtime_marks_retrying_then_failed_after_gateway_errors(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def failing_provider(_request) -> LLMProviderResponse:
        raise TimeoutError("provider timed out")

    repository = CommercialSpineRepository(db_session)
    await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile={
                "sources": {
                    "items": [
                        {
                            "kind": "text",
                            "label": "FAQ",
                            "text": "Ish vaqti har kuni 10:00 dan 21:00 gacha.",
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )
    runtime = OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=failing_provider),
    )

    first = await runtime.process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-retry-1",
        max_attempts=2,
    )
    second = await runtime.process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-retry-2",
        max_attempts=2,
    )
    source_facts = await repository.list_facts(
        workspace_id=workspace.id,
        fact_type="business_source_fact",
        limit=20,
    )
    learning_projections = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="business_source_learning",
        limit=20,
    )
    source_learning = build_onboarding_source_learning_projection(
        source_facts=source_facts,
        source_learning_projections=learning_projections,
    )
    projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref="business_source_learning:onboarding:source:0",
    )

    assert first.retrying_count == 1
    assert first.failed_count == 0
    assert second.retrying_count == 0
    assert second.failed_count == 1
    assert projection is not None
    assert projection.state["status"] == "failed"
    assert projection.state["attempt_count"] == 2
    assert projection.degraded_reasons == ["provider_timeout"]
    assert source_learning["status"] == "failed"
    assert source_learning["summary"]["failed"] == 1
    assert source_learning["sources"][0]["retryable"] is True


async def test_onboarding_source_runtime_can_force_retry_failed_source(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def failing_provider(_request) -> LLMProviderResponse:
        raise TimeoutError("provider timed out")

    async def recovering_provider(request) -> LLMProviderResponse:
        if _is_contextualization_request(request):
            return _contextualization_response(request)
        unit_ref = request.input_payload["source_units"][0]["unit_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "fact_id": "knowledge:onboarding:retry-success",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "business:hours",
                            "value": {
                                "topic": "ish vaqti",
                                "answer": "Ish vaqti har kuni 10:00 dan 21:00 gacha.",
                            },
                            "confidence": 0.86,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile={
                "sources": {
                    "items": [
                        {
                            "kind": "text",
                            "label": "FAQ",
                            "text": "Ish vaqti har kuni 10:00 dan 21:00 gacha.",
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )
    failing_runtime = OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=failing_provider),
    )
    await failing_runtime.process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-force-retry-1",
        max_attempts=1,
    )

    recovered = await OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=recovering_provider),
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-force-retry-2",
        max_attempts=2,
        source_refs={"onboarding:source:0"},
        force=True,
    )
    learned_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:onboarding:retry-success",
    )
    projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref="business_source_learning:onboarding:source:0",
    )

    assert recovered.review_ready_count == 1
    assert recovered.failed_count == 0
    assert learned_fact is not None
    assert learned_fact.status == "proposed"
    assert projection is not None
    assert projection.state["gateway_status"] == "ok"
    assert projection.state["memory_candidate_count"] == 1
    assert projection.degraded is False


async def test_onboarding_source_runtime_recovers_stale_learning_projection_after_restart(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        if _is_contextualization_request(request):
            return _contextualization_response(request)
        unit_ref = request.input_payload["source_units"][0]["unit_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "fact_id": "knowledge:onboarding:restart-recovered",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "business:restart-recovered",
                            "value": {
                                "topic": "restart recovery",
                                "answer": "Source learning resumes after a stale learning projection.",
                            },
                            "confidence": 0.84,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile={
                "sources": {
                    "items": [
                        {
                            "kind": "text",
                            "label": "Restart recovery FAQ",
                            "text": "Source learning resumes after restart.",
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )
    await repository.upsert_projection(
        BusinessBrainProjection(
            projection_ref="business_source_learning:onboarding:source:0",
            workspace_id=workspace.id,
            projection_type="business_source_learning",
            entity_ref="workspace:source:onboarding:source:0",
            state={
                "source_ref": "onboarding:source:0",
                "source_kind": "text",
                "source_fact_id": "onboarding:source:0",
                "status": "learning",
                "attempt_count": 1,
                "max_attempts": 3,
            },
            source_refs=["onboarding:source:0"],
        )
    )

    recovered = await OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-restart-recovery",
        max_attempts=3,
    )
    learned_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:onboarding:restart-recovered",
    )
    projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref="business_source_learning:onboarding:source:0",
    )

    assert recovered.review_ready_count == 1
    assert learned_fact is not None
    assert projection is not None
    assert projection.state["status"] == "review_ready"
    assert projection.state["attempt_count"] == 2
    assert projection.degraded is False


async def test_onboarding_source_runtime_marks_rate_limit_as_retryable_provider_pressure(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    class ProviderRateLimitError(RuntimeError):
        pass

    async def rate_limited_provider(_request) -> LLMProviderResponse:
        raise ProviderRateLimitError("429 from provider")

    repository = CommercialSpineRepository(db_session)
    await OnboardingLearningBootstrapService(repository=repository).seed_business_brain(
        OnboardingLearningBootstrapInput(
            workspace_id=workspace.id,
            profile={
                "sources": {
                    "items": [
                        {
                            "kind": "text",
                            "label": "Rate limit FAQ",
                            "text": "This source should retry after provider pressure.",
                        }
                    ]
                }
            },
            actor_ref="owner",
        )
    )

    result = await OnboardingSourceLearningRuntimeService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=rate_limited_provider),
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id="corr-onboarding-source-rate-limit",
        max_attempts=2,
    )
    projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref="business_source_learning:onboarding:source:0",
    )
    source_learning = build_onboarding_source_learning_projection(
        source_facts=await repository.list_facts(
            workspace_id=workspace.id,
            fact_type="business_source_fact",
            limit=20,
        ),
        source_learning_projections=await repository.list_projections(
            workspace_id=workspace.id,
            projection_type="business_source_learning",
            limit=20,
        ),
    )

    assert result.retrying_count == 1
    assert result.items[0].degraded_reasons == ["provider_rate_limited"]
    assert projection is not None
    assert projection.state["status"] == "retrying"
    assert projection.degraded_reasons == ["provider_rate_limited"]
    assert source_learning["sources"][0]["retryable"] is True
