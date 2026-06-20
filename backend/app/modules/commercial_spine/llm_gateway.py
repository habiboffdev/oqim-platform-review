from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from google.genai import types
from pydantic import BaseModel, ValidationError

from app.brain.llm import generate_with_fallback, get_files_client
from app.brain.model_policy import ModelRoute, get_model_route
from app.brain.prompt_payload import prompt_asset_payload
from app.brain.prompt_registry import PromptRegistryError
from app.brain.token_tracker import get_token_tracker
from app.brain.usage_costs import (
    estimate_daily_usage_cost_micros,
    parse_usage_cost_policy,
)
from app.core.config import get_settings
from app.modules.commercial_spine.contracts import (
    LLMGatewayRequest,
    LLMGatewayResult,
    LLMGatewayTrace,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository

Provider = Callable[[LLMGatewayRequest], Awaitable["LLMProviderResponse"]]
_FILE_API_INLINE_THRESHOLD_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class LLMProviderResponse:
    text: str
    model_used: str | None = None
    token_usage: dict[str, Any] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False


class LLMGateway:
    def __init__(
        self,
        *,
        repository: CommercialSpineRepository,
        provider: Provider | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._persist_lock = asyncio.Lock()

    async def generate(
        self,
        request: LLMGatewayRequest,
        *,
        output_model: type[BaseModel],
    ) -> LLMGatewayResult:
        result, trace = await self.generate_detached(
            request,
            output_model=output_model,
        )
        async with self._persist_lock:
            await self._repository.persist_llm_trace(trace)
        return result

    async def generate_detached(
        self,
        request: LLMGatewayRequest,
        *,
        output_model: type[BaseModel],
    ) -> tuple[LLMGatewayResult, LLMGatewayTrace]:
        trace_id = f"llm-trace-{uuid.uuid4().hex}"
        started = time.monotonic()
        status = "ok"
        parsed_output: dict[str, Any] | None = None
        validation_errors: list[str] = []
        provider_response: LLMProviderResponse | None = None
        raw_output_ref: str | None = None

        try:
            request = _with_managed_prompt_payload(request)
            get_model_route(request.route_key)
        except PromptRegistryError as exc:
            status = "schema_error"
            validation_errors = [f"prompt_registry:{exc.__class__.__name__}"]
        if status == "ok":
            try:
                budget_error = await _daily_cost_budget_error(request)
                if budget_error is not None:
                    status = "blocked"
                    validation_errors = [budget_error]
                    raise _GatewayBlocked()
                if self._provider is not None:
                    provider_response = await self._provider(request)
                else:
                    provider_response = await _default_provider(request, output_model)
                raw_output_ref = provider_response.text
                parsed_json = json.loads(provider_response.text or "{}")
                parsed = output_model.model_validate(parsed_json)
                parsed_output = parsed.model_dump(mode="json")
            except TimeoutError:
                status = "timeout"
                validation_errors = ["provider_timeout"]
            except json.JSONDecodeError as exc:
                status = "schema_error"
                validation_errors = [f"invalid_json:{exc.msg}"]
            except ValidationError as exc:
                status = "schema_error"
                validation_errors = [error["msg"] for error in exc.errors()]
            except _GatewayBlocked:
                pass
            except Exception as exc:
                status = "provider_error"
                validation_errors = [exc.__class__.__name__]

        latency_ms = int((time.monotonic() - started) * 1000)
        result = LLMGatewayResult(
            status=status,  # type: ignore[arg-type]
            parsed_output=parsed_output,
            raw_output_ref=raw_output_ref,
            model_used=provider_response.model_used if provider_response else None,
            token_usage=provider_response.token_usage if provider_response else {},
            latency_ms=latency_ms,
            trace_id=trace_id,
            validation_errors=validation_errors,
            fallback_used=provider_response.fallback_used if provider_response else False,
        )
        trace = LLMGatewayTrace(
            trace_id=trace_id,
            workspace_id=request.workspace_id,
            correlation_id=request.correlation_id,
            route_key=request.route_key,
            workflow_name=request.workflow_name,
            prompt_id=request.prompt_id,
            prompt_version=request.prompt_version,
            source_refs=list(request.source_refs),
            status=result.status,
            model_used=result.model_used,
            token_usage=dict(result.token_usage),
            latency_ms=result.latency_ms,
            cost_estimate=result.cost_estimate,
            fallback_used=result.fallback_used,
            validation_errors=list(result.validation_errors),
            raw_output_ref=result.raw_output_ref,
            raw_request=request.model_dump(mode="json"),
            raw_response=(
                dict(provider_response.raw_response)
                if provider_response
                else {"status": status, "validation_errors": validation_errors}
            ),
        )
        return result, trace


class _GatewayBlocked(Exception):
    pass


async def _daily_cost_budget_error(request: LLMGatewayRequest) -> str | None:
    settings = get_settings()
    limit = int(
        request.budget.get("daily_cost_limit_micros")
        or settings.llm_daily_cost_budget_micros_per_workspace
        or 0
    )
    if limit <= 0:
        return None
    tracker = get_token_tracker()
    if tracker is None:
        return None
    usage = await tracker.get_daily_usage(request.workspace_id)
    estimated = estimate_daily_usage_cost_micros(
        usage,
        cost_policy=parse_usage_cost_policy(
            settings.llm_usage_cost_micros_per_1k_tokens
        ),
    )
    if estimated >= limit:
        return "daily_cost_budget_exceeded"
    return None


async def _default_provider(
    request: LLMGatewayRequest,
    output_model: type[BaseModel],
) -> LLMProviderResponse:
    route = get_model_route(request.route_key)
    response = await generate_with_fallback(
        chain=route.chain,
        contents=await _gateway_contents(request),
        config=_route_structured_config(
            route=route,
            output_model=output_model,
            system_instruction=_prompt_system_instruction(request),
        ),
        timeout=request.timeout_ms / 1000,
        workspace_id=request.workspace_id,
        operation=request.workflow_name,
        prompt_cache=request.prompt_cache,
    )
    return LLMProviderResponse(
        text=response.text,
        model_used=response.model_used,
        token_usage=response.usage or {},
        raw_response={"provider": response.provider},
    )


def _route_structured_config(
    *,
    route: ModelRoute,
    output_model: type[BaseModel],
    system_instruction: str | None = None,
) -> types.GenerateContentConfig:
    config_kwargs: dict[str, Any] = {
        "response_mime_type": "application/json",
        "response_schema": output_model,
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(
            disable=True,
        ),
    }
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    if route.default_thinking_level is not None:
        config_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_level=route.default_thinking_level,
        )
    return types.GenerateContentConfig(**config_kwargs)


def _with_managed_prompt_payload(request: LLMGatewayRequest) -> LLMGatewayRequest:
    prompt = prompt_asset_payload(
        request.prompt_id,
        version=request.prompt_version,
    )
    prompt_cache = _gateway_prompt_cache_payload(request, prompt)
    return request.model_copy(
        update={
            "input_payload": {
                **request.input_payload,
                "prompt": prompt,
            },
            "prompt_cache": prompt_cache,
        }
    )


def _gateway_prompt_cache_payload(
    request: LLMGatewayRequest,
    prompt: dict[str, Any],
) -> dict[str, Any]:
    runtime_cache = dict(request.prompt_cache or {})
    prompt_cache_key = prompt.get("cache_key")
    prompt_digest = prompt.get("digest")
    stable_payload = _gateway_runtime_stable_payload(request.input_payload)
    provider_strategy = runtime_cache.get("provider_strategy", "metadata_only")
    cacheable = bool(prompt_cache_key or runtime_cache.get("cacheable"))
    if stable_payload and cacheable:
        provider_strategy = "gemini_cached_content"
    payload: dict[str, Any] = {
        "schema_version": "llm_prompt_cache.v1",
        "provider_strategy": provider_strategy,
        "cacheable": cacheable,
        "ttl_seconds": runtime_cache.get("ttl_seconds", 3600),
        "prompt_asset": {
            "cache_key": prompt_cache_key,
            "digest": prompt_digest,
            "cache_policy": prompt.get("cache_policy"),
        },
    }
    if runtime_cache:
        payload["runtime_context"] = dict(runtime_cache)
        if stable_payload:
            payload["runtime_context"]["stable_payload"] = stable_payload
    return payload


async def _gateway_contents(request: LLMGatewayRequest) -> str | list[types.Content]:
    input_payload = _gateway_model_input_payload(request)
    if not request.content_parts:
        return json.dumps(input_payload, ensure_ascii=False)

    parts: list[types.Part] = [
        types.Part.from_text(
            text=json.dumps(input_payload, ensure_ascii=False)
        )
    ]
    for raw_part in request.content_parts:
        kind = str(raw_part.get("kind") or raw_part.get("type") or "").strip()
        if kind == "text":
            parts.append(types.Part.from_text(text=str(raw_part.get("text") or "")))
            continue
        if kind == "inline_data":
            data_base64 = str(raw_part.get("data_base64") or "")
            mime_type = str(raw_part.get("mime_type") or "application/octet-stream")
            data = base64.b64decode(data_base64)
            upload_strategy = str(raw_part.get("upload_strategy") or "").strip()
            if upload_strategy == "file_api" or len(data) > _FILE_API_INLINE_THRESHOLD_BYTES:
                file_part = await _upload_gateway_file_part(
                    data=data,
                    mime_type=mime_type,
                    display_name=str(raw_part.get("file_name") or "").strip() or None,
                )
                parts.append(file_part)
                continue
            parts.append(
                types.Part.from_bytes(
                    data=data,
                    mime_type=mime_type,
                )
            )
            continue
        if kind == "file_uri":
            file_uri = str(raw_part.get("file_uri") or "")
            mime_type = str(raw_part.get("mime_type") or "application/octet-stream")
            if not file_uri:
                raise ValueError("file_uri content part requires file_uri")
            parts.append(types.Part.from_uri(file_uri=file_uri, mime_type=mime_type))
            continue
        raise ValueError(f"unsupported_gateway_content_part:{kind or 'missing'}")
    return [types.Content(role="user", parts=parts)]


async def _upload_gateway_file_part(
    *,
    data: bytes,
    mime_type: str,
    display_name: str | None = None,
) -> types.Part:
    uploaded = await asyncio.to_thread(
        _upload_gateway_file_sync,
        data,
        mime_type,
        display_name,
    )
    file_uri = str(getattr(uploaded, "uri", "") or "")
    uploaded_mime_type = str(getattr(uploaded, "mime_type", "") or mime_type)
    if not file_uri:
        raise ValueError("gemini_file_upload_missing_uri")
    return types.Part.from_uri(file_uri=file_uri, mime_type=uploaded_mime_type)


def _upload_gateway_file_sync(
    data: bytes,
    mime_type: str,
    display_name: str | None,
) -> Any:
    client = get_files_client()
    file_obj = io.BytesIO(data)
    if display_name:
        file_obj.name = display_name
    config: dict[str, Any] = {"mime_type": mime_type}
    if display_name:
        config["display_name"] = display_name
    uploaded = client.files.upload(
        file=file_obj,
        config=config,
    )
    attempts = 0
    while _file_state_name(uploaded) == "PROCESSING" and attempts < 24:
        time.sleep(2.5)
        uploaded = client.files.get(name=str(getattr(uploaded, "name")))
        attempts += 1
    state = _file_state_name(uploaded)
    if state and state not in {"ACTIVE", "SUCCEEDED"}:
        raise ValueError(f"gemini_file_upload_not_ready:{state}")
    return uploaded


def _file_state_name(uploaded: Any) -> str:
    state = getattr(uploaded, "state", None)
    return str(getattr(state, "name", "") or state or "").strip().upper()


def _prompt_system_instruction(request: LLMGatewayRequest) -> str | None:
    prompt = request.input_payload.get("prompt")
    if not isinstance(prompt, dict):
        return None
    body = prompt.get("body")
    if not isinstance(body, str) or not body.strip():
        return None
    return body.strip()


def _gateway_model_input_payload(request: LLMGatewayRequest) -> dict[str, Any]:
    payload = dict(request.input_payload)
    prompt = payload.get("prompt")
    if isinstance(prompt, dict):
        prompt_metadata = {
            key: value
            for key, value in prompt.items()
            if key != "body" and value not in (None, "", [], {})
        }
        payload["prompt"] = prompt_metadata
    if request.prompt_cache:
        payload = _without_cached_stable_runtime_payload(payload, request.prompt_cache)
        payload["prompt_cache"] = _compact_prompt_cache_for_model(request.prompt_cache)
    return payload


def _compact_prompt_cache_for_model(prompt_cache: dict[str, Any]) -> dict[str, Any]:
    runtime_context = prompt_cache.get("runtime_context")
    compact: dict[str, Any] = {
        key: value
        for key, value in prompt_cache.items()
        if key != "runtime_context" and value not in (None, "", [], {})
    }
    if isinstance(runtime_context, dict):
        compact["runtime_context"] = {
            key: runtime_context.get(key)
            for key in (
                "cache_scope",
                "cache_key",
                "material_hash",
                "cacheable",
                "dynamic_payload_keys",
                "stable_payload_keys",
                "invalidation_refs",
            )
            if runtime_context.get(key) not in (None, "", [], {})
        }
    return compact


def _gateway_runtime_stable_payload(input_payload: dict[str, Any]) -> dict[str, Any] | None:
    runtime_context = input_payload.get("runtime_context")
    if not isinstance(runtime_context, dict):
        return None
    stable_context: dict[str, Any] = {
        key: runtime_context.get(key)
        for key in (
            "schema_version",
            "workspace_id",
            "agent_id",
            "agent_name",
            "agent_kind",
            "documents",
            "permissions",
        )
        if runtime_context.get(key) not in (None, "", [], {})
    }
    if "documents" not in stable_context and "permissions" not in stable_context:
        return None
    return {
        "schema_version": "agent_runtime_stable_context.v1",
        "runtime_context": stable_context,
    }


def _without_cached_stable_runtime_payload(
    payload: dict[str, Any],
    prompt_cache: dict[str, Any],
) -> dict[str, Any]:
    runtime_cache = prompt_cache.get("runtime_context")
    stable_payload = (
        runtime_cache.get("stable_payload") if isinstance(runtime_cache, dict) else None
    )
    if not isinstance(stable_payload, dict):
        return payload
    runtime_context = payload.get("runtime_context")
    if not isinstance(runtime_context, dict):
        return payload
    dynamic_context = {
        key: value
        for key, value in runtime_context.items()
        if key not in {"documents", "permissions"}
    }
    return {**payload, "runtime_context": dynamic_context}
