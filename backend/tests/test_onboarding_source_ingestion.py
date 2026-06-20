from __future__ import annotations

import json
from io import BytesIO
from typing import ClassVar

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import (
    ContextualRetrievalRequest,
    ConversationPairMiningInput,
    SourceUnitRebuildRequest,
)
from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.source_ingestion import (
    OnboardingSourceIngestionRequest,
    OnboardingSourceIngestionService,
    SourceFetchResult,
)
from app.modules.retrieval_core.contracts import RetrievalContextRequest
from app.modules.retrieval_core.service import RetrievalCoreService

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


def _image_only_pdf_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 32), (30, 120, 210)).save(buffer, format="PDF")
    return buffer.getvalue()


async def test_onboarding_ingests_website_and_pdf_into_contextual_retrieval(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def fetch_source(url: str) -> SourceFetchResult:
        assert url == "https://nafis.example/products"
        return SourceFetchResult(
            content=(
                b"<html><head><title>Nafis Shop</title></head><body>"
                b"<meta property='og:image' content='/media/hero.jpg'>"
                b"<h1>Binafsha sumka</h1>"
                b"<img src='/media/binafsha-sumka.jpg' alt='Binafsha charm sumka'>"
                b"<p>Narxi 180000 UZS. Material: charm. Rang: binafsha.</p>"
                b"</body></html>"
            ),
            content_type="text/html",
            final_url=url,
        )

    repository = CommercialSpineRepository(db_session)
    service = OnboardingSourceIngestionService(
        repository=repository,
        fetch_source=fetch_source,
    )
    website = await service.ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:source:website",
            source_kind="website",
            source_payload={"url": "https://nafis.example/products"},
            correlation_id="corr-source-website",
            idempotency_key="source-website",
        )
    )
    pdf = await service.ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:source:pdf",
            source_kind="pdf",
            source_payload={"file_name": "catalog.pdf"},
            content_bytes=_PDF_BYTES,
            correlation_id="corr-source-pdf",
            idempotency_key="source-pdf",
        )
    )

    memory = BusinessBrainMemoryService(repository=repository)
    website_result = await memory.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["business_source_fact"],
            query_text="binafsha sumka charm narxi",
            include_source_units=True,
            limit=5,
        )
    )
    pdf_result = await memory.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["business_source_fact"],
            query_text="atlas koylak yashil narxi",
            include_source_units=True,
            limit=5,
        )
    )

    assert website.degraded_reasons == []
    assert pdf.degraded_reasons == []
    assert [asset.url for asset in website.media_assets] == [
        "https://nafis.example/media/hero.jpg",
        "https://nafis.example/media/binafsha-sumka.jpg",
    ]
    assert website.source_units[0].source_text.startswith("Contextual source unit")
    assert "Source kind: website" in website.source_units[0].source_text
    assert "Source hint: https://nafis.example/products" in website.source_units[0].source_text
    assert "Binafsha sumka" in website.source_units[0].source_text
    assert pdf.source_units[0].source_text.startswith("Contextual source unit")
    assert "Source kind: pdf" in pdf.source_units[0].source_text
    assert "Source hint: catalog.pdf" in pdf.source_units[0].source_text
    assert "Atlas koylak" in pdf.source_units[0].source_text
    assert website_result.candidates[0].fact_id == website.source_fact_id
    assert pdf_result.candidates[0].fact_id == pdf.source_fact_id
    media_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=f"business_source_media:{website.media_assets[1].media_ref}",
    )
    assert media_fact is not None
    assert media_fact.fact_type == "business_source_media_fact"
    assert media_fact.value["alt_text"] == "Binafsha charm sumka"


async def test_onboarding_source_units_can_use_llm_chunk_context_for_recall(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    calls = []

    async def provider(request) -> LLMProviderResponse:
        calls.append(request)
        assert request.workflow_name == "onboarding.source_unit_contextualization"
        assert request.prompt_id == "business_brain.source_unit_contextualization"
        prompt = request.input_payload["prompt"]
        assert prompt["prompt_id"] == "business_brain.source_unit_contextualization"
        assert prompt["registry_state"] == "loaded"
        assert "Return only JSON matching `SourceUnitContextualizationOutput`" in prompt["body"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "source_unit_contextualization_output.v1",
                    "context": (
                        "PDF catalog product: Apple Lightning Digital AV Adapter. "
                        "Customers may ask for HDMI perehodnik or TV adapter."
                    ),
                }
            ),
            model_used="test-contextualizer",
        )

    repository = CommercialSpineRepository(db_session)
    gateway = LLMGateway(repository=repository, provider=provider)
    service = OnboardingSourceIngestionService(
        repository=repository,
        gateway=gateway,
    )

    source = await service.ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:source:adapter-pdf",
            source_kind="text",
            source_payload={
                "file_name": "adapter.pdf",
                "text": "Apple Lightning Digital AV Adapter. TV display adapter.",
            },
            contextualize_source_units=True,
            correlation_id="corr-source-adapter-context",
            idempotency_key="source-adapter-context",
        )
    )

    result = await BusinessBrainMemoryService(repository=repository).retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["business_source_fact"],
            query_text="hdmi perehodnik",
            include_source_units=True,
        )
    )

    assert len(calls) == 1
    assert source.degraded_reasons == []
    assert source.source_units[0].source_text.startswith(
        "LLM contextualized source unit"
    )
    assert "HDMI perehodnik" in source.source_units[0].source_text
    assert "Original contextual source unit" in source.source_units[0].source_text
    assert [candidate.fact_id for candidate in result.candidates] == [
        source.source_fact_id
    ]


async def test_onboarding_source_unit_context_failure_falls_back_to_embedding(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    class FakeEmbeddingService:
        texts: ClassVar[list[str]] = []

        async def embed_text(self, text: str) -> list[float]:
            self.texts.append(text)
            return [0.7, *([0.0] * 3071)]

    monkeypatch.setattr(
        "app.brain.embedding_service.EmbeddingService",
        FakeEmbeddingService,
    )

    async def provider(request) -> LLMProviderResponse:
        raise TimeoutError()

    repository = CommercialSpineRepository(db_session)
    service = OnboardingSourceIngestionService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    source = await service.ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:source:context-timeout",
            source_kind="text",
            source_payload={"text": "Toshkent ichida yetkazib berish 24 soat."},
            contextualize_source_units=True,
            embed_source_units=True,
            correlation_id="corr-source-context-timeout",
            idempotency_key="source-context-timeout",
        )
    )

    assert source.degraded_reasons == ["contextualization:timeout"]
    assert len(FakeEmbeddingService.texts) == 1
    assert FakeEmbeddingService.texts[0].startswith("Contextual source unit")
    assert "LLM contextualized source unit" not in FakeEmbeddingService.texts[0]
    assert source.source_units[0].embedding_state == "ready"
    assert source.source_units[0].degraded_reason == "contextualization:timeout"
    assert source.source_units[0].source_text.startswith("Contextual source unit")


def _xlsx_bytes(rows: list[list[object]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    for row in rows:
        worksheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


async def test_onboarding_ingests_spreadsheets_as_normalized_source_units(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    service = OnboardingSourceIngestionService(repository=repository)

    csv_source = await service.ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:source:csv-price-list",
            source_kind="spreadsheet",
            source_payload={
                "file_name": "price-list.csv",
                "content_type": "text/csv",
            },
            content_bytes=(
                b"name,price,currency,stock\n"
                b"Atlas koylak,250000,UZS,7\n"
                b"Binafsha sumka,180000,UZS,3\n"
            ),
            correlation_id="corr-source-csv",
            idempotency_key="source-csv",
        )
    )
    xlsx_source = await service.ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:source:xlsx-sku-list",
            source_kind="spreadsheet",
            source_payload={
                "file_name": "sku-list.xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
            content_bytes=_xlsx_bytes([
                ["sku", "title", "size"],
                ["RING-17", "Madelyn uzuk", 17],
            ]),
            correlation_id="corr-source-xlsx",
            idempotency_key="source-xlsx",
        )
    )

    csv_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=csv_source.source_fact_id,
    )
    xlsx_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=xlsx_source.source_fact_id,
    )

    assert csv_source.degraded_reasons == []
    assert xlsx_source.degraded_reasons == []
    assert "Columns: name | price | currency | stock" in csv_source.source_units[0].source_text
    assert "Row 1: name=Atlas koylak; price=250000; currency=UZS; stock=7" in csv_source.source_units[0].source_text
    assert "Row 1: sku=RING-17; title=Madelyn uzuk; size=17" in xlsx_source.source_units[0].source_text
    assert csv_fact is not None
    assert csv_fact.value["metadata"]["normalized_source"] == "spreadsheet_rows"
    assert csv_fact.value["metadata"]["row_count"] == 2
    assert xlsx_fact is not None
    assert xlsx_fact.value["metadata"]["columns"] == ["sku", "title", "size"]


async def test_onboarding_ingests_shopify_products_json_as_catalog_source(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def fetch_source(url: str) -> SourceFetchResult:
        assert url == "https://macbro.uz/products.json?limit=2"
        return SourceFetchResult(
            content=json.dumps(
                {
                    "products": [
                        {
                            "id": 8792872419626,
                            "title": "Apple Lightning to Digital AV Adapter",
                            "handle": "adapter-apple-lighting-to-digital-av",
                            "body_html": (
                                "<p>Adapter HDMI video uchun. 1080p HD qo'llab-quvvatlaydi.</p>"
                            ),
                            "vendor": "Apple",
                            "product_type": "Зарядные устройства Apple",
                            "variants": [
                                {
                                    "id": 47109396070698,
                                    "title": "White",
                                    "option1": "White",
                                    "sku": "White",
                                    "available": True,
                                    "price": "804000.00",
                                }
                            ],
                            "images": [
                                {
                                    "id": 43346585059626,
                                    "src": "https://cdn.shopify.com/adapter.png",
                                    "width": 1263,
                                    "height": 1417,
                                    "position": 1,
                                }
                            ],
                            "options": [{"name": "Цвет", "values": ["White"]}],
                        }
                    ]
                }
            ).encode(),
            content_type="application/json; charset=utf-8",
            final_url=url,
        )

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(
        repository=repository,
        fetch_source=fetch_source,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:source:macbro-products-json",
            source_kind="website",
            source_payload={"url": "https://macbro.uz/products.json?limit=2"},
            correlation_id="corr-source-macbro-json",
            idempotency_key="source-macbro-json",
        )
    )
    source_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=source.source_fact_id,
    )
    media_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=f"business_source_media:{source.media_assets[0].media_ref}",
    )

    assert source.degraded_reasons == []
    assert len(source.source_units) == 1
    assert source.source_units[0].source_text.startswith("Contextual source unit")
    assert "Source hint: https://macbro.uz/products.json?limit=2" in source.source_units[0].source_text
    assert "Apple Lightning to Digital AV Adapter" in source.source_units[0].source_text
    assert "price=804000.00" in source.source_units[0].source_text
    assert "availability=available" in source.source_units[0].source_text
    assert "Option: Цвет = White" in source.source_units[0].source_text
    assert [asset.url for asset in source.media_assets] == [
        "https://cdn.shopify.com/adapter.png"
    ]
    assert source.media_assets[0].origin == "shopify_product_image"
    assert source.media_assets[0].metadata["product_handle"] == (
        "adapter-apple-lighting-to-digital-av"
    )
    assert source_fact is not None
    assert source_fact.value["metadata"]["structured_source"] == "shopify_products_json"
    assert media_fact is not None
    assert media_fact.value["alt_text"] == "Apple Lightning to Digital AV Adapter"


async def test_onboarding_ingests_image_only_pdf_as_source_media(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    service = OnboardingSourceIngestionService(repository=repository)

    pdf = await service.ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:source:image-only-pdf",
            source_kind="pdf",
            source_payload={"file_name": "image-only.pdf"},
            content_bytes=_image_only_pdf_bytes(),
            correlation_id="corr-source-image-pdf",
            idempotency_key="source-image-pdf",
        )
    )
    source_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=pdf.source_fact_id,
    )
    media_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=f"business_source_media:{pdf.media_assets[0].media_ref}",
    )

    assert pdf.source_units == []
    assert len(pdf.media_assets) == 1
    assert pdf.media_assets[0].origin == "pdf_page_image"
    assert pdf.media_assets[0].content_hash
    assert pdf.degraded_reasons == []
    assert source_fact is not None
    assert source_fact.status == "active"
    assert source_fact.value["processing"] == {
        "state": "indexed",
        "source_unit_count": 0,
        "source_media_count": 1,
        "degraded_reasons": [],
    }
    assert media_fact is not None
    assert media_fact.fact_type == "business_source_media_fact"
    assert media_fact.value["page_number"] == 1


async def test_onboarding_ingests_voice_audio_as_source_text_for_rules(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def transcribe_audio(audio_bytes: bytes, mime_type: str) -> str:
        assert audio_bytes == b"fake-voice-bytes"
        assert mime_type == "audio/ogg"
        return "Yetkazib berish so'ralsa, avval tuman va telefon so'ra."

    repository = CommercialSpineRepository(db_session)
    voice = await OnboardingSourceIngestionService(
        repository=repository,
        transcribe_audio=transcribe_audio,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:source:voice-rule",
            source_kind="voice_note",
            source_payload={
                "file_name": "rule.ogg",
                "content_type": "audio/ogg",
            },
            content_bytes=b"fake-voice-bytes",
            correlation_id="corr-source-voice",
            idempotency_key="source-voice-rule",
        )
    )
    source_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=voice.source_fact_id,
    )

    assert voice.degraded_reasons == []
    assert len(voice.source_units) == 1
    assert "avval tuman va telefon so'ra" in voice.source_units[0].source_text
    assert source_fact is not None
    assert source_fact.value["metadata"]["content_type"] == "audio/ogg"
    assert source_fact.value["processing"]["source_unit_count"] == 1


async def test_onboarding_voice_audio_uses_llm_gateway_when_no_transcriber_injected(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    captured: dict[str, object] = {}

    async def provider(request) -> LLMProviderResponse:
        captured["request"] = request
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "transcript": "Kurs haqida so'ralsa, avval yo'nalishini aniqlang.",
                }
            ),
            model_used="fixture-media",
            token_usage={"input_tokens": 2, "output_tokens": 2},
        )

    repository = CommercialSpineRepository(db_session)
    gateway = LLMGateway(repository=repository, provider=provider)
    voice = await OnboardingSourceIngestionService(
        repository=repository,
        gateway=gateway,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:source:voice-gateway",
            source_kind="voice_note",
            source_payload={
                "file_name": "course-rule.ogg",
                "content_type": "audio/ogg",
            },
            content_bytes=b"fake-voice-bytes",
            correlation_id="corr-source-voice-gateway",
            idempotency_key="source-voice-gateway",
        )
    )

    request = captured["request"]
    assert request.prompt_id == "media.voice_transcription"
    assert request.input_payload["prompt"]["prompt_id"] == "media.voice_transcription"
    assert request.correlation_id == (
        f"source-intake-media:{workspace.id}:onboarding:source:voice-gateway"
    )
    assert request.source_refs == ["onboarding:source:voice-gateway"]
    assert voice.degraded_reasons == []
    assert len(voice.source_units) == 1
    assert "avval yo'nalishini aniqlang" in voice.source_units[0].source_text


async def test_telegram_channel_ingestion_extracts_media_and_pairs_are_retrievable(
    db_session: AsyncSession,
    workspace: Workspace,
    conversation,
) -> None:
    repository = CommercialSpineRepository(db_session)
    service = OnboardingSourceIngestionService(repository=repository)
    channel = await service.ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="telegram_channel:@nafis:may",
            source_kind="telegram_channel",
            source_payload={
                "channel_id": "@nafis",
                "messages": [
                    {
                        "message_id": "700",
                        "text": "Yashil atlas ko'ylak. Narxi 250000 UZS.",
                        "media_type": "photo",
                        "media_metadata": {
                            "mime_type": "image/jpeg",
                            "width": 1080,
                            "height": 1350,
                            "url": "https://cdn.example/atlas-green.jpg",
                        },
                    },
                    {
                        "message_id": "701",
                        "caption": "Binafsha sumka yangi keldi.",
                        "media_type": "photo",
                        "grouped_id": "album-7",
                        "media_metadata": {
                            "mime_type": "image/jpeg",
                            "url": "https://cdn.example/bag-purple.jpg",
                        },
                    },
                ],
            },
            correlation_id="corr-telegram-channel",
            idempotency_key="telegram-channel-nafis-may",
        )
    )

    memory = BusinessBrainMemoryService(repository=repository)
    media_result = await memory.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["business_source_media_fact"],
            query_text="yashil atlas koylak rasmi",
            include_source_units=False,
            limit=5,
        )
    )
    pairs = await memory.mine_conversation_pairs(
        ConversationPairMiningInput(
            workspace_id=workspace.id,
            conversation_id=conversation.id,
            source_refs=["conversation:telegram-channel-test"],
            turns=[
                {
                    "message_ref": "msg:customer:photo-1",
                    "sender_type": "customer",
                    "content": "Shu atlas ko'ylakdan bormi?",
                    "created_at": "2026-05-07T09:00:00+00:00",
                    "media_semantics": {
                        "description": "customer sent a green atlas dress photo",
                        "source_media_refs": [channel.media_assets[0].media_ref],
                    },
                },
                {
                    "message_ref": "msg:seller:reply-2",
                    "sender_type": "seller",
                    "content": "Ha, yashil atlas ko'ylak bor. Narxi 250 000 so'm.",
                    "created_at": "2026-05-07T09:01:00+00:00",
                    "quality_label": "approved",
                    "outcome": "continued",
                },
            ],
            correlation_id="corr-pair-media",
        )
    )
    await memory.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=["conversation_pair_fact"],
        )
    )
    pair_result = await memory.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["conversation_pair_fact"],
            query_text="green atlas dress photo bor",
            include_source_units=True,
            limit=5,
        )
    )

    assert [asset.media_ref for asset in channel.media_assets] == [
        "telegram_channel:@nafis:700:media",
        "telegram_channel:@nafis:701:media",
    ]
    assert channel.media_assets[0].url == "https://cdn.example/atlas-green.jpg"
    assert channel.media_assets[1].grouped_id == "album-7"
    assert media_result.candidates[0].fact_id == (
        "business_source_media:telegram_channel:@nafis:700:media"
    )
    assert pairs.pairs[0].fact.value["media_semantics"]["customer"] == {
        "description": "customer sent a green atlas dress photo",
        "source_media_refs": ["telegram_channel:@nafis:700:media"],
    }
    assert pair_result.candidates[0].fact_id == pairs.pairs[0].fact.fact_id
    assert "green atlas dress photo" in pair_result.candidates[0].contextual_text


async def test_onboarding_ingests_past_conversation_as_contextual_source_units(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    conversation = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:source:past-conversation",
            source_kind="past_conversation",
            source_payload={
                "conversation_id": 44,
                "turns": [
                    {
                        "message_ref": "conv:44:customer:1",
                        "sender_type": "customer",
                        "content": "Kurs narxi va davomiyligi qancha?",
                        "created_at": "2026-05-14T07:00:00+00:00",
                    },
                    {
                        "message_ref": "conv:44:seller:2",
                        "sender_type": "seller",
                        "content": "Boshlang'ich kurs 6 hafta, narxi 700 ming so'm.",
                        "created_at": "2026-05-14T07:01:00+00:00",
                        "quality_label": "approved",
                    },
                ],
            },
            correlation_id="corr-past-conversation-source",
            idempotency_key="past-conversation-source",
            embed_source_units=True,
        )
    )
    retrieval = await RetrievalCoreService(repository=repository).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            requested_fact_types=["business_source_fact"],
            query_text="kurs davomiyligi narxi",
            enable_semantic=True,
            include_source_units=True,
            limit=5,
        )
    )

    assert conversation.degraded_reasons == []
    assert len(conversation.source_units) == 1
    assert conversation.source_units[0].embedding_state == "ready"
    assert "sender=customer" in conversation.source_units[0].source_text
    assert "sender=seller" in conversation.source_units[0].source_text
    assert "Boshlang'ich kurs 6 hafta" in conversation.source_units[0].source_text
    assert retrieval.candidates[0].fact_id == conversation.source_fact_id
    assert retrieval.candidates[0].source_units[0].unit_ref == (
        conversation.source_units[0].unit_ref
    )
