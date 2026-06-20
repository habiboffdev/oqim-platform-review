from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.llm import LLMResponse
from app.brain.model_policy import (
    MODEL_GEMINI_3_FLASH,
    MODEL_GEMINI_31_FLASH_LITE,
    MODEL_GEMINI_31_PRO_PREVIEW,
    MODEL_GEMINI_EMBEDDING_2,
    get_model_route,
    list_model_routes,
)
from app.core.config import get_settings
from app.core.google_auth import build_genai_client_kwargs
from app.models.workspace import Workspace
from app.modules.commercial_spine.contracts import LLMGatewayRequest
from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.commercial_spine.repository import CommercialSpineRepository


class GatewayStructuredOutput(BaseModel):
    answer: str


class _Settings:
    gemini_api_key = None
    google_genai_use_vertexai = True
    google_cloud_project = "oqim-test-project"
    google_cloud_location = "global"
    google_auth_source = "service_account"
    google_impersonate_service_account = None


async def test_vertex_client_kwargs_use_vertex_v1_endpoint() -> None:
    kwargs, status = build_genai_client_kwargs(_Settings())

    assert kwargs["vertexai"] is True
    assert kwargs["project"] == "oqim-test-project"
    assert kwargs["location"] == "global"
    assert kwargs["http_options"].api_version == "v1"
    assert status.vertex_forced is True


def test_model_policy_has_single_current_vertex_gemini_matrix() -> None:
    routes = {route.key: route for route in list_model_routes()}

    assert routes["structured_fast"].model_id == MODEL_GEMINI_31_FLASH_LITE
    assert routes["structured_fast"].chain == [
        ("gemini", MODEL_GEMINI_31_FLASH_LITE),
        ("gemini", MODEL_GEMINI_3_FLASH),
    ]
    assert routes["structured_judge"].model_id == MODEL_GEMINI_31_FLASH_LITE
    assert routes["composition_rich"].model_id == MODEL_GEMINI_3_FLASH
    assert routes["composition_complex"].model_id == MODEL_GEMINI_3_FLASH
    assert routes["deep_reasoning"].model_id == MODEL_GEMINI_31_PRO_PREVIEW
    assert routes["embedding_multimodal_primary"].model_id == MODEL_GEMINI_EMBEDDING_2
    assert all(route.provider == "gemini" for route in routes.values())
    assert all(
        "gemini-1.5" not in route.model_id and "gemini-2.0" not in route.model_id
        for route in routes.values()
    )
    assert all(
        "pro" not in model_id
        for route in routes.values()
        if route.response_mode != "embedding"
        for _, model_id in route.chain
        if route.key != "deep_reasoning"
    )


async def test_llm_gateway_default_provider_uses_route_structured_output_config(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_generate_with_fallback(**kwargs):
        captured.update(kwargs)
        return LLMResponse(
            text=json.dumps({"answer": "ok"}),
            model_used="gemini-3.1-flash-lite-preview",
            provider="gemini",
            usage={"input_tokens": 7, "output_tokens": 3},
        )

    monkeypatch.setattr(
        "app.modules.commercial_spine.llm_gateway.generate_with_fallback",
        fake_generate_with_fallback,
    )
    request = LLMGatewayRequest(
        route_key="structured_fast",
        workflow_name="phase75_gateway",
        prompt_id="business_brain.source_learning",
        prompt_version="1.0.0",
        input_payload={"customer_text": "Salom"},
        output_schema_name="GatewayStructuredOutput",
        workspace_id=workspace.id,
        correlation_id="corr:phase75:gateway",
        source_refs=["message:phase75"],
        timeout_ms=10_000,
    )

    result = await LLMGateway(
        repository=CommercialSpineRepository(db_session),
    ).generate(request, output_model=GatewayStructuredOutput)

    config = captured["config"]
    assert result.status == "ok"
    assert captured["chain"] == get_model_route("structured_fast").chain
    assert captured["operation"] == "phase75_gateway"
    assert captured["workspace_id"] == workspace.id
    payload = json.loads(captured["contents"])
    assert payload["customer_text"] == "Salom"
    assert payload["prompt"]["prompt_id"] == "business_brain.source_learning"
    assert payload["prompt"]["registry_state"] == "loaded"
    assert "body" not in payload["prompt"]
    assert "OQIM Business Brain source learner" in config.system_instruction
    assert config.response_mime_type == "application/json"
    assert config.response_schema is GatewayStructuredOutput
    assert config.automatic_function_calling.disable is True
    assert str(config.thinking_config.thinking_level).endswith("MINIMAL")


async def test_llm_gateway_rejects_unregistered_prompt_before_provider(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(_request):
        raise AssertionError("provider must not run without a managed prompt")

    request = LLMGatewayRequest(
        route_key="structured_fast",
        workflow_name="unmanaged_prompt",
        prompt_id="unmanaged.prompt",
        prompt_version="1.0.0",
        input_payload={"customer_text": "Salom"},
        output_schema_name="GatewayStructuredOutput",
        workspace_id=workspace.id,
        correlation_id="corr:unmanaged:prompt",
        source_refs=["message:unmanaged"],
        timeout_ms=10_000,
    )

    result = await LLMGateway(
        repository=CommercialSpineRepository(db_session),
        provider=provider,
    ).generate(request, output_model=GatewayStructuredOutput)

    assert result.status == "schema_error"
    assert result.validation_errors == ["prompt_registry:PromptNotFoundError"]


async def test_llm_gateway_blocks_provider_when_daily_cost_budget_is_exceeded(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    class _Tracker:
        async def get_daily_usage(self, workspace_id: int):
            assert workspace_id == workspace.id
            return {
                "seller_agent:gemini:input": 1000,
                "seller_agent:gemini:output": 1000,
            }

    async def provider(_request):
        raise AssertionError("provider must not run after daily cost budget is exceeded")

    monkeypatch.setenv("LLM_DAILY_COST_BUDGET_MICROS_PER_WORKSPACE", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.modules.commercial_spine.llm_gateway.get_token_tracker",
        lambda: _Tracker(),
    )
    request = LLMGatewayRequest(
        route_key="structured_fast",
        workflow_name="cost_guard",
        prompt_id="business_brain.source_learning",
        prompt_version="1.0.0",
        input_payload={"customer_text": "Salom"},
        output_schema_name="GatewayStructuredOutput",
        workspace_id=workspace.id,
        correlation_id="corr:cost:guard",
        source_refs=["message:cost"],
        timeout_ms=10_000,
    )

    try:
        result = await LLMGateway(
            repository=CommercialSpineRepository(db_session),
            provider=provider,
        ).generate(request, output_model=GatewayStructuredOutput)
    finally:
        get_settings.cache_clear()

    assert result.status == "blocked"
    assert result.validation_errors == ["daily_cost_budget_exceeded"]


async def test_llm_gateway_sends_registered_prompt_asset_to_provider(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_generate_with_fallback(**kwargs):
        captured.update(kwargs)
        return LLMResponse(
            text=json.dumps({"answer": "ok"}),
            model_used="gemini-3.1-flash-lite-preview",
            provider="gemini",
            usage={"input_tokens": 7, "output_tokens": 3},
        )

    monkeypatch.setattr(
        "app.modules.commercial_spine.llm_gateway.generate_with_fallback",
        fake_generate_with_fallback,
    )
    request = LLMGatewayRequest(
        route_key="structured_fast",
        workflow_name="extraction.seller_voice",
        prompt_id="extraction.seller_voice",
        prompt_version="1.0.0",
        input_payload={"customer_text": "Salom"},
        output_schema_name="GatewayStructuredOutput",
        workspace_id=workspace.id,
        correlation_id="corr:prompt:asset",
        source_refs=["message:prompt"],
        timeout_ms=10_000,
    )

    result = await LLMGateway(
        repository=CommercialSpineRepository(db_session),
    ).generate(request, output_model=GatewayStructuredOutput)

    payload = json.loads(captured["contents"])
    assert result.status == "ok"
    assert payload["customer_text"] == "Salom"
    assert payload["prompt"]["prompt_id"] == "extraction.seller_voice"
    assert payload["prompt"]["registry_state"] == "loaded"
    assert "body" not in payload["prompt"]
    assert "Return only JSON matching `SellerVoiceExtractionOutput`" in captured["config"].system_instruction


async def test_llm_gateway_preserves_prompt_cache_metadata_in_payload_and_trace(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_generate_with_fallback(**kwargs):
        captured.update(kwargs)
        return LLMResponse(
            text=json.dumps({"answer": "ok"}),
            model_used="gemini-3.1-flash-lite-preview",
            provider="gemini",
            usage={"input_tokens": 11, "output_tokens": 4},
        )

    monkeypatch.setattr(
        "app.modules.commercial_spine.llm_gateway.generate_with_fallback",
        fake_generate_with_fallback,
    )
    request = LLMGatewayRequest(
        route_key="structured_fast",
        workflow_name="business_brain.source_learning",
        prompt_id="business_brain.source_learning",
        prompt_version="1.0.0",
        input_payload={
            "runtime_context": {
                "documents": {"business_md": {"markdown": "stable business"}},
                "permissions": {"active_external_scopes": ["telegram.send_message"]},
                "recent_messages": [{"content": "dynamic"}],
            },
            "turn": {"customer": "Salom"},
        },
        output_schema_name="GatewayStructuredOutput",
        workspace_id=workspace.id,
        correlation_id="corr:prompt:cache",
        source_refs=["message:cache"],
        prompt_cache={
            "provider_strategy": "metadata_only",
            "cache_scope": "agent",
            "cache_key": "agent-runtime-context:v1:1:2:abc",
            "material_hash": "abc123",
            "cacheable": True,
            "stable_payload_keys": [
                "runtime_context.documents",
                "runtime_context.permissions",
            ],
            "dynamic_payload_keys": ["turn", "grounding"],
            "invalidation_refs": ["agent:2:AGENT.md"],
        },
        timeout_ms=10_000,
    )

    result = await LLMGateway(
        repository=CommercialSpineRepository(db_session),
    ).generate(request, output_model=GatewayStructuredOutput)
    snapshot = await CommercialSpineRepository(db_session).get_debug_snapshot(
        workspace_id=workspace.id,
        correlation_id="corr:prompt:cache",
    )

    payload = json.loads(captured["contents"])
    trace = snapshot.llm_gateway_traces[0]
    assert result.status == "ok"
    assert payload["prompt_cache"]["schema_version"] == "llm_prompt_cache.v1"
    assert payload["prompt_cache"]["provider_strategy"] == "gemini_cached_content"
    assert payload["prompt_cache"]["runtime_context"]["cache_key"] == (
        "agent-runtime-context:v1:1:2:abc"
    )
    assert payload["prompt_cache"]["runtime_context"]["dynamic_payload_keys"] == [
        "turn",
        "grounding",
    ]
    assert trace.raw_request["prompt_cache"]["runtime_context"]["material_hash"] == (
        "abc123"
    )
    assert captured["prompt_cache"]["runtime_context"]["stable_payload"][
        "runtime_context"
    ]["documents"] == {"business_md": {"markdown": "stable business"}}
    assert trace.raw_request["prompt_cache"]["prompt_asset"]["cache_key"].startswith(
        "prompt:business_brain.source_learning:1.0.0:"
    )
    assert "documents" not in payload["runtime_context"]
    assert "permissions" not in payload["runtime_context"]
    assert trace.raw_request["input_payload"]["runtime_context"]["recent_messages"] == [
        {"content": "dynamic"}
    ]


async def test_llm_gateway_sends_registered_prompt_with_multimodal_parts(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_generate_with_fallback(**kwargs):
        captured.update(kwargs)
        return LLMResponse(
            text=json.dumps({"answer": "ok"}),
            model_used="gemini-3.1-flash-lite-preview",
            provider="gemini",
            usage={"input_tokens": 7, "output_tokens": 3},
        )

    monkeypatch.setattr(
        "app.modules.commercial_spine.llm_gateway.generate_with_fallback",
        fake_generate_with_fallback,
    )
    request = LLMGatewayRequest(
        route_key="media_rich",
        workflow_name="media_image_description",
        prompt_id="media.image_description",
        prompt_version="1.0.0",
        input_payload={"mime_type": "image/jpeg"},
        content_parts=[
            {
                "kind": "inline_data",
                "mime_type": "image/jpeg",
                "data_base64": "ZmFrZS1qcGVn",
            },
        ],
        output_schema_name="GatewayStructuredOutput",
        workspace_id=workspace.id,
        correlation_id="corr:prompt:asset:media",
        source_refs=["message:media"],
        timeout_ms=10_000,
    )

    result = await LLMGateway(
        repository=CommercialSpineRepository(db_session),
    ).generate(request, output_model=GatewayStructuredOutput)

    contents = captured["contents"]
    payload = json.loads(contents[0].parts[0].text)
    assert result.status == "ok"
    assert payload["prompt"]["prompt_id"] == "media.image_description"
    assert payload["prompt"]["registry_state"] == "loaded"
    assert "body" not in payload["prompt"]
    assert "Describe what is visibly in this image" in captured["config"].system_instruction
    assert payload["mime_type"] == "image/jpeg"
    assert len(contents[0].parts) == 2


async def test_llm_gateway_uploads_file_api_content_parts(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    captured: dict[str, Any] = {}
    uploaded_files: list[dict[str, Any]] = []

    class _Uploaded:
        uri = "gs://gemini-file/catalog.pdf"
        mime_type = "application/pdf"
        name = "files/catalog"
        state = None

    class _Files:
        def upload(self, *, file, config=None):
            uploaded_files.append(
                {
                    "bytes": file.read(),
                    "name": getattr(file, "name", None),
                    "config": dict(config or {}),
                }
            )
            return _Uploaded()

        def get(self, *, name, config=None):  # pragma: no cover - ready file
            return _Uploaded()

    class _Client:
        files = _Files()

    async def fake_generate_with_fallback(**kwargs):
        captured.update(kwargs)
        return LLMResponse(
            text=json.dumps({"answer": "ok"}),
            model_used="gemini-3.1-flash-lite-preview",
            provider="gemini",
            usage={"input_tokens": 9, "output_tokens": 3},
        )

    monkeypatch.setattr(
        "app.modules.commercial_spine.llm_gateway.get_files_client",
        lambda: _Client(),
    )
    monkeypatch.setattr(
        "app.modules.commercial_spine.llm_gateway.generate_with_fallback",
        fake_generate_with_fallback,
    )
    request = LLMGatewayRequest(
        route_key="structured_fast",
        workflow_name="business_source_learning",
        prompt_id="business_brain.source_learning",
        prompt_version="1.0.0",
        input_payload={"source_kind": "pdf"},
        content_parts=[
            {
                "kind": "inline_data",
                "mime_type": "application/pdf",
                "data_base64": "JVBERi0xLjQ=",
                "file_name": "catalog.pdf",
                "upload_strategy": "file_api",
            },
        ],
        output_schema_name="GatewayStructuredOutput",
        workspace_id=workspace.id,
        correlation_id="corr:file-api:pdf",
        source_refs=["onboarding:source:pdf"],
        timeout_ms=10_000,
    )

    result = await LLMGateway(
        repository=CommercialSpineRepository(db_session),
    ).generate(request, output_model=GatewayStructuredOutput)

    contents = captured["contents"]
    file_data = contents[0].parts[1].file_data
    assert result.status == "ok"
    assert uploaded_files == [
        {
            "bytes": b"%PDF-1.4",
            "name": "catalog.pdf",
            "config": {
                "mime_type": "application/pdf",
                "display_name": "catalog.pdf",
            },
        }
    ]
    assert file_data.file_uri == "gs://gemini-file/catalog.pdf"
    assert file_data.mime_type == "application/pdf"


async def test_llm_gateway_overrides_caller_supplied_prompt_payload(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    captured: dict[str, Any] = {}

    async def provider(request):
        captured["request"] = request
        return LLMProviderResponse(text=json.dumps({"answer": "ok"}))

    request = LLMGatewayRequest(
        route_key="structured_fast",
        workflow_name="prompt_override_guard",
        prompt_id="extraction.seller_voice",
        prompt_version="1.0.0",
        input_payload={
            "prompt": {
                "prompt_id": "caller.injected",
                "body": "wrong prompt",
                "registry_state": "caller",
            },
            "customer_text": "Salom",
        },
        output_schema_name="GatewayStructuredOutput",
        workspace_id=workspace.id,
        correlation_id="corr:prompt:override",
        source_refs=["message:prompt-override"],
    )

    result = await LLMGateway(
        repository=CommercialSpineRepository(db_session),
        provider=provider,
    ).generate(request, output_model=GatewayStructuredOutput)

    prompt = captured["request"].input_payload["prompt"]
    assert result.status == "ok"
    assert prompt["prompt_id"] == "extraction.seller_voice"
    assert prompt["registry_state"] == "loaded"
    assert "wrong prompt" not in prompt["body"]


async def test_embedding_service_uses_gemini_embedding_route_without_task_type(
    monkeypatch,
) -> None:
    from app.brain import embedding_service

    captured: list[dict[str, Any]] = []

    class _Embedding:
        values = [0.1] * embedding_service.DIMENSIONS

    class _Result:
        embeddings: ClassVar[list[_Embedding]] = [_Embedding(), _Embedding()]

    class _Models:
        async def embed_content(self, *, model, contents, config):
            captured.append(
                {"model": model, "contents": contents, "config": config}
            )
            return _Result()

    class _Aio:
        models = _Models()

    class _Client:
        aio = _Aio()

    monkeypatch.setattr(embedding_service, "_get_client", lambda: _Client())

    service = embedding_service.EmbeddingService()
    query = await service.embed_query("qizil ko'ylak bormi?")
    batch = await service.embed_texts_batch(["catalog one", "catalog two"])

    assert len(query) == embedding_service.DIMENSIONS
    assert len(batch) == 2
    assert all(item["model"] == MODEL_GEMINI_EMBEDDING_2 for item in captured)
    assert all(item["config"].output_dimensionality == embedding_service.DIMENSIONS for item in captured)
    assert all(getattr(item["config"], "task_type", None) is None for item in captured)
    assert "search query" in captured[0]["contents"]


def test_phase75_docs_mark_legacy_ai_paths_as_compatibility_only() -> None:
    docs_root = Path(__file__).resolve().parents[2] / "docs"
    architecture = (
        docs_root / "architecture/2026-05-04-business-brain-autocrm-architecture.md"
    ).read_text(encoding="utf-8")
    inventory = (
        docs_root / "architecture/2026-05-04-legacy-deletion-inventory.md"
    ).read_text(encoding="utf-8")

    assert "Phase 7.5 landed" in architecture
    assert "old CRM extraction and draft LLM paths remain legacy-only" in inventory
