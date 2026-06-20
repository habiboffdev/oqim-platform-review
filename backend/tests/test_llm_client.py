"""Tests for the unified LLMClient class.

Covers:
- strip_thinking() with various inputs
- LLMResponse dataclass
- LLMClient init with/without Cerebras key
- generate_with_fallback skipping unavailable Cerebras
- generate_with_fallback falling through on failure
- generate_with_fallback raising when all fail
- Cooldown behavior after 429
- get_files_client() file-upload routing
- Chain type validation
- Gemini-first FLASH_LITE_CHAIN, FLASH_LITE_GEMINI_CHAIN
- Gemini 3 CONTROL_CHAIN and control-node config helpers
- Phase 2: Structured logging (provider, model, latency_ms, fallback)
"""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest


# ── strip_thinking tests ──


def test_strip_thinking_removes_think_tags():
    """Single think block is removed, leaving only output."""
    from app.brain.llm import strip_thinking

    text = "<think>reasoning here</think>The actual answer"
    assert strip_thinking(text) == "The actual answer"


def test_strip_thinking_handles_multiline():
    """Multiline think blocks are removed."""
    from app.brain.llm import strip_thinking

    text = "<think>\nStep 1: analyze\nStep 2: decide\n</think>\n{\"result\": true}"
    result = strip_thinking(text)
    assert result == '{"result": true}'


def test_strip_thinking_handles_nested():
    """Nested/multiple think blocks are all removed."""
    from app.brain.llm import strip_thinking

    text = "<think>first</think>middle<think>second</think>end"
    assert strip_thinking(text) == "middleend"


def test_strip_thinking_no_tags_unchanged():
    """Text without think tags is returned unchanged."""
    from app.brain.llm import strip_thinking

    text = "Just a normal response"
    assert strip_thinking(text) == "Just a normal response"


def test_strip_thinking_empty_think_block():
    """Empty think block is removed."""
    from app.brain.llm import strip_thinking

    text = "<think></think>output"
    assert strip_thinking(text) == "output"


# ── LLMResponse tests ──


def test_llm_response_dataclass():
    """LLMResponse stores text, model_used, provider, usage, and parsed fields."""
    from app.brain.llm import LLMResponse

    resp = LLMResponse(
        text="hello",
        model_used="gemini-3-flash-preview",
        provider="gemini",
        usage={"input_tokens": 10, "output_tokens": 5},
        parsed={"ok": True},
    )
    assert resp.text == "hello"
    assert resp.model_used == "gemini-3-flash-preview"
    assert resp.provider == "gemini"
    assert resp.usage == {"input_tokens": 10, "output_tokens": 5}
    assert resp.parsed == {"ok": True}


def test_llm_response_usage_optional():
    """LLMResponse usage and parsed fields default to None."""
    from app.brain.llm import LLMResponse

    resp = LLMResponse(text="hi", model_used="test", provider="gemini")
    assert resp.usage is None
    assert resp.parsed is None


# ── Chain type tests ──


def test_chain_type_is_tuple_list():
    """Chain constants are lists of (provider, model) tuples."""
    from app.brain.llm_policy import CONTROL_CHAIN, FLASH_CHAIN, FLASH_LITE_CHAIN

    assert isinstance(FLASH_LITE_CHAIN, list)
    assert isinstance(FLASH_LITE_CHAIN[0], tuple)
    assert FLASH_LITE_CHAIN[0][0] == "gemini"
    assert FLASH_LITE_CHAIN[0][1] == "gemini-3.1-flash-lite-preview"

    assert isinstance(FLASH_CHAIN, list)
    assert isinstance(FLASH_CHAIN[0], tuple)
    assert FLASH_CHAIN[0][0] == "gemini"
    # 2026-06-13: agent_turn_generation primary bumped Flash-Lite -> 3 Flash.
    assert FLASH_CHAIN[0][1] == "gemini-3-flash-preview"

    assert isinstance(CONTROL_CHAIN, list)
    assert isinstance(CONTROL_CHAIN[0], tuple)
    assert CONTROL_CHAIN[0] == ("gemini", "gemini-3.1-flash-lite-preview")


# ── LLMClient init tests ──


def test_client_init_no_cerebras_key():
    """LLMClient with no Cerebras key sets _cerebras_client to None."""
    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-gemini-key"
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        with patch(
            "app.brain.llm.build_genai_client_kwargs",
            return_value=({"api_key": "test-gemini-key"}, MagicMock()),
        ):
            with patch("app.brain.llm.genai.Client"):
                client = LLMClient()
                assert client._cerebras_client is None


def test_client_init_uses_service_account_credentials_in_vertex_mode():
    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = None
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = "oqim-business"
    mock_settings.google_cloud_location = "global"

    fake_credentials = MagicMock()
    fake_status = MagicMock()

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        with patch(
            "app.brain.llm.build_genai_client_kwargs",
            return_value=(
                {
                    "vertexai": True,
                    "project": "oqim-business",
                    "location": "global",
                    "credentials": fake_credentials,
                },
                fake_status,
            ),
        ):
            with patch("app.brain.llm.log_google_auth_status") as mock_log_auth:
                with patch("app.brain.llm.genai.Client") as mock_genai_client:
                    LLMClient()
                    mock_genai_client.assert_called_once_with(
                        vertexai=True,
                        project="oqim-business",
                        location="global",
                        credentials=fake_credentials,
                    )
                    mock_log_auth.assert_called_once_with(
                        ANY,
                        component="brain.llm",
                        status=fake_status,
                    )


def test_client_init_with_cerebras_key():
    """LLMClient with Cerebras key creates the OpenAI client."""
    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-gemini-key"
    mock_settings.cerebras_api_key = "test-cerebras-key"
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        with patch("app.brain.llm.genai.Client"):
            with patch("app.brain.llm.AsyncOpenAI") as mock_openai:
                client = LLMClient()
                assert client._cerebras_client is not None
                mock_openai.assert_called_once_with(
                    base_url="https://api.cerebras.ai/v1",
                    api_key="test-cerebras-key",
                )


# ── generate_with_fallback tests ──


@pytest.mark.asyncio
async def test_fallback_skips_cerebras_when_no_key():
    """When Cerebras is not configured, chain items with provider=cerebras are skipped."""
    from app.brain.llm import LLMClient, LLMResponse

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    # Mock Gemini response
    mock_gemini_response = MagicMock()
    mock_gemini_response.text = "gemini result"
    mock_gemini_response.usage_metadata = MagicMock(
        prompt_token_count=10, candidates_token_count=5
    )

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        mock_genai_client = MagicMock()
        mock_genai_client.aio.models.generate_content = AsyncMock(
            return_value=mock_gemini_response
        )
        with patch("app.brain.llm.genai.Client", return_value=mock_genai_client):
            client = LLMClient()
            chain = [
                ("cerebras", "qwen-3-235b"),
                ("gemini", "gemini-3.1-flash-lite-preview"),
            ]
            result = await client.generate_with_fallback(
                chain=chain, contents="test", config=None, timeout=10.0,
            )

            assert isinstance(result, LLMResponse)
            assert result.provider == "gemini"
            assert result.text == "gemini result"


@pytest.mark.asyncio
async def test_gemini_generate_uses_provider_cached_content_for_stable_prompt():
    from app.brain.llm import LLMClient, build_tool_config

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    mock_gemini_response = MagicMock()
    mock_gemini_response.text = '{"ok": true}'
    mock_gemini_response.usage_metadata = MagicMock(
        prompt_token_count=37,
        candidates_token_count=6,
        cached_content_token_count=29,
    )

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        mock_genai_client = MagicMock()
        mock_genai_client.caches.create = MagicMock(
            return_value=SimpleNamespace(name="cachedContents/agent-static")
        )
        mock_genai_client.aio.models.generate_content = AsyncMock(
            return_value=mock_gemini_response
        )
        with patch("app.brain.llm.genai.Client", return_value=mock_genai_client):
            client = LLMClient()
            result = await client.generate(
                model="gemini-test",
                contents='{"turn": "dynamic"}',
                config=build_tool_config(
                    [
                        {
                            "name": "talk_send_msgs",
                            "description": "Send one or more Telegram bubbles.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "messages": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "text": {"type": "string"},
                                                "reply_message_id": {"type": "integer"},
                                            },
                                        },
                                    }
                                },
                            },
                        }
                    ],
                    system_instruction="SYSTEM",
                ),
                provider="gemini",
                prompt_cache={
                    "provider_strategy": "gemini_cached_content",
                    "cacheable": True,
                    "estimated_tokens": 4096,
                    "min_cache_tokens": 4096,
                    "runtime_context": {
                        "cache_key": "agent-runtime-context:v1:1:2:abc",
                        "material_hash": "abc123",
                        "stable_payload": {
                            "runtime_context": {
                                "documents": {
                                    "business_md": {"markdown": "stable business"}
                                }
                            }
                        },
                    },
                },
            )

    create_kwargs = mock_genai_client.caches.create.call_args.kwargs
    create_config = create_kwargs["config"]
    generate_config = mock_genai_client.aio.models.generate_content.call_args.kwargs[
        "config"
    ]
    assert create_kwargs["model"] == "gemini-test"
    assert getattr(create_config, "system_instruction") == "SYSTEM"
    assert getattr(create_config, "tools", None)
    assert getattr(create_config, "ttl") == "3600s"
    assert "stable business" in create_config.contents[0].parts[0].text
    assert getattr(generate_config, "cached_content") == "cachedContents/agent-static"
    assert getattr(generate_config, "system_instruction", None) is None
    assert getattr(generate_config, "tools", None) in (None, [])
    assert getattr(generate_config, "tool_config", None) is None
    assert getattr(generate_config, "automatic_function_calling", None) is None
    assert result.usage == {
        "input_tokens": 37,
        "output_tokens": 6,
        "cached_content_tokens": 29,
    }


@pytest.mark.asyncio
async def test_gemini_generate_falls_back_when_cached_content_creation_fails():
    from google.genai import types

    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    mock_gemini_response = MagicMock()
    mock_gemini_response.text = '{"ok": true}'
    mock_gemini_response.usage_metadata = MagicMock(
        prompt_token_count=12,
        candidates_token_count=4,
        cached_content_token_count=0,
    )

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        mock_genai_client = MagicMock()
        mock_genai_client.caches.create = MagicMock(side_effect=RuntimeError("cache down"))
        mock_genai_client.aio.models.generate_content = AsyncMock(
            return_value=mock_gemini_response
        )
        with patch("app.brain.llm.genai.Client", return_value=mock_genai_client):
            client = LLMClient()
            result = await client.generate(
                model="gemini-test",
                contents="dynamic",
                config=types.GenerateContentConfig(system_instruction="SYSTEM"),
                provider="gemini",
                prompt_cache={
                    "provider_strategy": "gemini_cached_content",
                    "cacheable": True,
                    "estimated_tokens": 4096,
                    "min_cache_tokens": 4096,
                    "runtime_context": {
                        "cache_key": "agent-runtime-context:v1:1:2:abc",
                        "material_hash": "abc123",
                        "stable_payload": {"documents": {"business": "stable"}},
                    },
                },
            )

    generate_config = mock_genai_client.aio.models.generate_content.call_args.kwargs[
        "config"
    ]
    assert getattr(generate_config, "cached_content", None) is None
    assert getattr(generate_config, "system_instruction") == "SYSTEM"
    assert result.text == '{"ok": true}'


@pytest.mark.asyncio
async def test_gemini_generate_skips_cached_content_below_provider_minimum():
    from google.genai import types

    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    mock_gemini_response = MagicMock()
    mock_gemini_response.text = '{"ok": true}'
    mock_gemini_response.usage_metadata = MagicMock(
        prompt_token_count=12,
        candidates_token_count=4,
        cached_content_token_count=0,
    )

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        mock_genai_client = MagicMock()
        mock_genai_client.caches.create = MagicMock()
        mock_genai_client.aio.models.generate_content = AsyncMock(
            return_value=mock_gemini_response
        )
        with patch("app.brain.llm.genai.Client", return_value=mock_genai_client):
            client = LLMClient()
            result = await client.generate(
                model="gemini-test",
                contents="dynamic",
                config=types.GenerateContentConfig(system_instruction="SYSTEM"),
                provider="gemini",
                prompt_cache={
                    "provider_strategy": "gemini_cached_content",
                    "cacheable": True,
                    "estimated_tokens": 300,
                    "min_cache_tokens": 4096,
                    "runtime_context": {
                        "cache_key": "agent-runtime-context:v1:1:2:small",
                        "material_hash": "small123",
                        "stable_payload": {"documents": {"business": "small"}},
                    },
                },
            )

    mock_genai_client.caches.create.assert_not_called()
    generate_config = mock_genai_client.aio.models.generate_content.call_args.kwargs[
        "config"
    ]
    assert getattr(generate_config, "cached_content", None) is None
    assert getattr(generate_config, "system_instruction") == "SYSTEM"
    assert result.text == '{"ok": true}'


@pytest.mark.asyncio
async def test_gemini_generate_uses_model_specific_cached_content_minimum():
    from google.genai import types

    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    mock_gemini_response = MagicMock()
    mock_gemini_response.text = '{"ok": true}'
    mock_gemini_response.usage_metadata = MagicMock(
        prompt_token_count=12,
        candidates_token_count=4,
        cached_content_token_count=0,
    )

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        mock_genai_client = MagicMock()
        mock_genai_client.caches.create = MagicMock()
        mock_genai_client.aio.models.generate_content = AsyncMock(
            return_value=mock_gemini_response
        )
        with patch("app.brain.llm.genai.Client", return_value=mock_genai_client):
            client = LLMClient()
            result = await client.generate(
                model="gemini-3.5-flash",
                contents="dynamic",
                config=types.GenerateContentConfig(system_instruction="SYSTEM"),
                provider="gemini",
                prompt_cache={
                    "provider_strategy": "gemini_cached_content",
                    "cacheable": True,
                    "estimated_tokens": 1769,
                    "min_cache_tokens": 1024,
                    "runtime_context": {
                        "cache_key": "agent-runtime-context:v1:1:2:borderline",
                        "material_hash": "borderline123",
                        "stable_payload": {"documents": {"business": "borderline"}},
                    },
                },
            )

    mock_genai_client.caches.create.assert_not_called()
    assert result.text == '{"ok": true}'


@pytest.mark.asyncio
async def test_fallback_falls_through_on_exception():
    """When first model raises, falls through to next model in chain."""
    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    # First call fails, second succeeds
    mock_success_response = MagicMock()
    mock_success_response.text = "fallback result"
    mock_success_response.usage_metadata = MagicMock(
        prompt_token_count=10, candidates_token_count=5
    )

    call_count = 0

    async def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Model unavailable")
        return mock_success_response

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        mock_genai_client = MagicMock()
        mock_genai_client.aio.models.generate_content = AsyncMock(side_effect=side_effect)
        with patch("app.brain.llm.genai.Client", return_value=mock_genai_client):
            client = LLMClient()
            chain = [
                ("gemini", "gemini-3-flash-preview"),
                ("gemini", "gemini-2.5-flash"),
            ]
            result = await client.generate_with_fallback(
                chain=chain, contents="test", config=None, timeout=10.0,
            )

            assert result.text == "fallback result"
            assert call_count == 2


@pytest.mark.asyncio
async def test_fallback_raises_when_all_fail():
    """When all models in chain fail, last exception is raised."""
    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        mock_genai_client = MagicMock()
        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("All broken")
        )
        with patch("app.brain.llm.genai.Client", return_value=mock_genai_client):
            client = LLMClient()
            chain = [
                ("gemini", "gemini-3-flash-preview"),
                ("gemini", "gemini-2.5-flash"),
            ]
            with pytest.raises(RuntimeError, match="All broken"):
                await client.generate_with_fallback(
                    chain=chain, contents="test", config=None, timeout=10.0,
                )


# ── Cooldown tests ──


def test_cerebras_cooldown():
    """After cooldown, _is_cerebras_available returns False for the duration."""
    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.cerebras_api_key = "test-cerebras-key"
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        with patch("app.brain.llm.genai.Client"):
            with patch("app.brain.llm.AsyncOpenAI"):
                client = LLMClient()
                assert client._is_cerebras_available() is True

                # Trigger cooldown
                client._cooldown_cerebras(seconds=60.0)
                assert client._is_cerebras_available() is False


# ── File upload client routing ──


def test_get_files_client_without_api_key_reuses_shared_runtime_client():
    """Files API fallback reuses the configured runtime Gemini client."""
    import app.brain.llm as llm

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = None
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        mock_genai = MagicMock()
        with patch("app.brain.llm.genai.Client", return_value=mock_genai):
            llm._instance = None
            try:
                assert llm.get_files_client() is mock_genai
            finally:
                llm._instance = None


# ── Chain constants ──


def test_flash_lite_chain_is_gemini_only():
    """FLASH_LITE_CHAIN stays Gemini-only while Cerebras is disabled."""
    from app.brain.llm_policy import FLASH_LITE_CHAIN

    assert len(FLASH_LITE_CHAIN) == 1
    assert all(provider == "gemini" for provider, _ in FLASH_LITE_CHAIN)
    assert FLASH_LITE_CHAIN[0] == ("gemini", "gemini-3.1-flash-lite-preview")


def test_flash_lite_gemini_chain_exists():
    """FLASH_LITE_GEMINI_CHAIN has the reflex Gemini model."""
    from app.brain.llm_policy import FLASH_LITE_GEMINI_CHAIN

    assert len(FLASH_LITE_GEMINI_CHAIN) == 1
    assert all(provider == "gemini" for provider, _ in FLASH_LITE_GEMINI_CHAIN)
    assert FLASH_LITE_GEMINI_CHAIN[0] == ("gemini", "gemini-3.1-flash-lite-preview")


def test_flash_chain_excludes_pro_preview_from_default_runtime():
    """FLASH_CHAIN is Gemini-only and avoids hidden Pro fallbacks."""
    from app.brain.llm_policy import FLASH_CHAIN

    assert FLASH_CHAIN == [
        ("gemini", "gemini-3-flash-preview"),
        ("gemini", "gemini-3.5-flash"),
    ]
    assert all(provider == "gemini" for provider, _ in FLASH_CHAIN)
    assert all("pro" not in model for _, model in FLASH_CHAIN)


def test_control_chain_uses_flash_lite_only():
    """Control nodes should use bounded Gemini 3.1 Flash-Lite."""
    from app.brain.llm_policy import CONTROL_CHAIN

    assert CONTROL_CHAIN == [("gemini", "gemini-3.1-flash-lite-preview")]
    assert all("pro" not in model for _, model in CONTROL_CHAIN)


def test_hermes_reply_uses_agent_turn_policy_with_medium_thinking():
    """hermes_reply IS the agent turn: same policy owns the thinking level.

    Bumped low -> "medium" (2026-06-13, owner call: latency acceptable): the
    A-class consultative flow benefits from deeper per-turn judgment and more
    reasoning helps the model catch its own holding-reply repetition. The reply
    turn still drives multi-step tool work (lead + react + confirm) at medium.
    """
    from app.brain.llm import _tool_loop_thinking_level
    from app.brain.llm_policy import get_task_policy, normalize_operation

    assert normalize_operation("hermes_reply") == "agent_turn_generation"
    assert get_task_policy("hermes_reply").thinking_level == "medium"
    assert _tool_loop_thinking_level("hermes_reply", include_thoughts=True) == "medium"
    # tasks without a policy thinking level keep the historical defaults
    assert _tool_loop_thinking_level("structured_json", include_thoughts=False) is None
    assert _tool_loop_thinking_level(None, include_thoughts=False) is None


def test_policy_maps_tasks_to_performance_lanes():
    from app.brain.llm_policy import normalize_operation, resolve_chain_for_operation

    # 3 Flash is REGISTERED, so resolve collapses to the single chosen model
    # (runtime degrades gracefully on failure); unregistered primaries keep the
    # full fallback chain (Flash-Lite GA was unregistered, hence 2 before).
    assert resolve_chain_for_operation(
        operation="agent_turn_generation",
        requested_chain=[],
    ) == [("gemini", "gemini-3-flash-preview")]
    assert normalize_operation("draft_generation") == "draft_generation"
    assert resolve_chain_for_operation(
        operation="agent_turn_planner",
        requested_chain=[],
    ) == [("gemini", "gemini-3.1-flash-lite-preview")]
    assert resolve_chain_for_operation(
        operation="agent_turn_review",
        requested_chain=[],
    ) == [("gemini", "gemini-3-flash-preview")]
    assert normalize_operation("agent_turn_review_retry") == "agent_turn_review"
    assert resolve_chain_for_operation(
        operation="unknown_custom_operation",
        requested_chain=[("gemini", "custom-model")],
    ) == [("gemini", "custom-model")]


def test_build_control_json_config_uses_json_schema_and_minimal_thinking():
    from pydantic import BaseModel

    from app.brain.llm import build_control_json_config

    class _Payload(BaseModel):
        ok: bool

    config = build_control_json_config(response_schema=_Payload, max_output_tokens=123)

    assert config.response_mime_type == "application/json"
    assert config.response_json_schema is not None
    assert config.response_schema is None
    assert config.max_output_tokens == 123
    assert config.temperature == 1.0
    assert config.automatic_function_calling is not None
    assert config.automatic_function_calling.disable is True
    assert config.thinking_config is not None
    assert str(config.thinking_config.thinking_level).endswith("MINIMAL")


def test_build_reply_text_config_uses_low_thinking_and_disables_afc():
    from app.brain.llm import build_reply_text_config

    config = build_reply_text_config(
        system_instruction="You are a seller.",
        max_output_tokens=222,
    )

    assert config.system_instruction == "You are a seller."
    assert config.max_output_tokens == 222
    assert config.temperature == 1.0
    assert config.automatic_function_calling is not None
    assert config.automatic_function_calling.disable is True
    assert config.thinking_config is not None
    assert str(config.thinking_config.thinking_level).endswith("LOW")


def test_get_llm_policy_uses_onboarding_specific_settings():
    from app.brain.llm import get_llm_policy
    from app.brain.llm_policy import FLASH_LITE_CHAIN

    policy = get_llm_policy("batch_contact_classification")

    assert policy.chain is FLASH_LITE_CHAIN
    assert policy.temperature == 0.1
    assert policy.max_output_tokens == 4096
    assert policy.thinking_level == "minimal"


def test_build_structured_json_config_uses_response_json_schema_and_minimal_thinking():
    from pydantic import BaseModel

    from app.brain.llm import build_structured_json_config, get_llm_policy

    class _Payload(BaseModel):
        ok: bool

    config = build_structured_json_config(
        policy=get_llm_policy("autocrm_resolution"),
        system_instruction="sys",
        response_schema=_Payload,
    )

    assert config.system_instruction == "sys"
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema is not None
    assert config.response_schema is None
    assert config.temperature == 0.2
    assert config.max_output_tokens == 4096
    assert config.automatic_function_calling is not None
    assert config.automatic_function_calling.disable is True
    assert config.thinking_config is not None
    assert str(config.thinking_config.thinking_level).endswith("MINIMAL")


@pytest.mark.asyncio
async def test_gemini_retries_without_unsupported_thinking_config():
    from google.genai import types

    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    mock_success_response = MagicMock()
    mock_success_response.text = "ok"
    mock_success_response.usage_metadata = MagicMock(
        prompt_token_count=10,
        candidates_token_count=5,
    )

    async def side_effect(**kwargs):
        if kwargs["config"].thinking_config is not None:
            raise RuntimeError("thinking_level MINIMAL is not supported by this model")
        return mock_success_response

    config = types.GenerateContentConfig(
        max_output_tokens=32,
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
    )

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        mock_genai_client = MagicMock()
        mock_genai_client.aio.models.generate_content = AsyncMock(side_effect=side_effect)
        with patch("app.brain.llm.genai.Client", return_value=mock_genai_client):
            client = LLMClient()
            response = await client.generate(
                model="legacy-no-thinking-model",
                contents="test",
                config=config,
                provider="gemini",
                timeout=10.0,
            )

    assert response.text == "ok"
    assert mock_genai_client.aio.models.generate_content.await_count == 2
    retry_config = mock_genai_client.aio.models.generate_content.await_args_list[1].kwargs["config"]
    assert retry_config.thinking_config is None
    assert config.thinking_config is not None


# ── Phase 2: Structured logging ──


@pytest.mark.asyncio
async def test_structured_logging_on_success():
    """generate_with_fallback logs provider, model, latency_ms, fallback on success."""
    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    mock_gemini_response = MagicMock()
    mock_gemini_response.text = "result"
    mock_gemini_response.usage_metadata = MagicMock(
        prompt_token_count=10, candidates_token_count=5
    )

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        mock_genai_client = MagicMock()
        mock_genai_client.aio.models.generate_content = AsyncMock(
            return_value=mock_gemini_response
        )
        with patch("app.brain.llm.genai.Client", return_value=mock_genai_client):
            client = LLMClient()
            chain = [("gemini", "gemini-3.1-flash-lite-preview")]

            with patch("app.brain.llm.logger") as mock_logger:
                await client.generate_with_fallback(
                    chain=chain, contents="test", config=None, timeout=10.0,
                )
                # Find the structured llm_call log
                info_calls = mock_logger.info.call_args_list
                llm_call_found = False
                for call in info_calls:
                    args, kwargs = call
                    if args and "llm_call" in str(args[0]):
                        llm_call_found = True
                        extra = kwargs.get("extra", {})
                        assert "provider" in extra
                        assert "model" in extra
                        assert "latency_ms" in extra
                        assert "fallback" in extra
                        assert extra["provider"] == "gemini"
                        assert extra["fallback"] is False
                assert llm_call_found, "Expected 'llm_call' log not found"


@pytest.mark.asyncio
async def test_structured_logging_on_fallback():
    """When first model fails, second succeeds — fallback=True in log."""
    from app.brain.llm import LLMClient

    mock_settings = MagicMock()
    mock_settings.gemini_api_key = "test-key"
    mock_settings.cerebras_api_key = None
    mock_settings.cerebras_base_url = "https://api.cerebras.ai/v1"
    mock_settings.cerebras_timeout = 5.0
    mock_settings.google_cloud_project = None
    mock_settings.google_cloud_location = "global"

    mock_success_response = MagicMock()
    mock_success_response.text = "fallback result"
    mock_success_response.usage_metadata = MagicMock(
        prompt_token_count=10, candidates_token_count=5
    )

    call_count = 0

    async def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Model unavailable")
        return mock_success_response

    with patch("app.brain.llm.get_settings", return_value=mock_settings):
        mock_genai_client = MagicMock()
        mock_genai_client.aio.models.generate_content = AsyncMock(side_effect=side_effect)
        with patch("app.brain.llm.genai.Client", return_value=mock_genai_client):
            client = LLMClient()
            chain = [
                ("gemini", "gemini-3-flash-preview"),
                ("gemini", "gemini-2.5-flash"),
            ]

            with patch("app.brain.llm.logger") as mock_logger:
                await client.generate_with_fallback(
                    chain=chain, contents="test", config=None, timeout=10.0,
                )
                # Find the structured llm_call log for the successful model
                info_calls = mock_logger.info.call_args_list
                llm_call_found = False
                for call in info_calls:
                    args, kwargs = call
                    if args and "llm_call" in str(args[0]):
                        llm_call_found = True
                        extra = kwargs.get("extra", {})
                        assert extra["fallback"] is True
                assert llm_call_found


# ── generate_structured_json tests ──


@pytest.mark.asyncio
async def test_generate_structured_json_parses_response():
    """generate_structured_json returns parsed JSON from LLM response."""
    from app.brain.llm import generate_structured_json, LLMResponse
    from app.brain.llm_policy import FLASH_LITE_CHAIN

    mock_response = LLMResponse(text='{"should_reply": true, "intent": "faq"}', model_used="test", provider="gemini")

    with patch("app.brain.llm.generate_with_fallback", new_callable=AsyncMock, return_value=mock_response):
        result = await generate_structured_json(
            chain=FLASH_LITE_CHAIN,
            system="You are a classifier.",
            prompt="Classify this message.",
            operation="test_op",
        )
    assert result == {"should_reply": True, "intent": "faq"}


@pytest.mark.asyncio
async def test_generate_structured_json_uses_operation_policy():
    """Structured helper should apply operation-level model/config policy."""
    from app.brain.llm import LLMResponse, generate_structured_json
    from app.brain.llm_policy import FLASH_CHAIN

    mock_response = LLMResponse(text='{"ok": true}', model_used="test", provider="gemini")
    llm = AsyncMock(return_value=mock_response)

    with patch("app.brain.llm.generate_with_fallback", llm):
        result = await generate_structured_json(
            chain=FLASH_CHAIN,
            system="sys",
            prompt="prompt",
            operation="batch_contact_classification",
        )

    assert result == {"ok": True}
    assert llm.await_args.kwargs["chain"][0] == ("gemini", "gemini-3.1-flash-lite-preview")
    config = llm.await_args.kwargs["config"]
    assert config.temperature == 0.1
    assert config.max_output_tokens == 4096
    assert config.automatic_function_calling.disable is True
    assert str(config.thinking_config.thinking_level).endswith("MINIMAL")


@pytest.mark.asyncio
async def test_generate_structured_json_handles_markdown_wrapped():
    """Handles JSON wrapped in markdown code blocks."""
    from app.brain.llm import generate_structured_json, LLMResponse
    from app.brain.llm_policy import FLASH_LITE_CHAIN

    mock_response = LLMResponse(text='```json\n{"status": "ok"}\n```', model_used="test", provider="gemini")

    with patch("app.brain.llm.generate_with_fallback", new_callable=AsyncMock, return_value=mock_response):
        result = await generate_structured_json(
            chain=FLASH_LITE_CHAIN,
            system="sys",
            prompt="prompt",
            operation="test",
        )
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_generate_structured_json_returns_empty_on_blank():
    """Returns {} when LLM returns empty string."""
    from app.brain.llm import generate_structured_json, LLMResponse
    from app.brain.llm_policy import FLASH_LITE_CHAIN

    mock_response = LLMResponse(text="", model_used="test", provider="gemini")

    with patch("app.brain.llm.generate_with_fallback", new_callable=AsyncMock, return_value=mock_response):
        result = await generate_structured_json(
            chain=FLASH_LITE_CHAIN,
            system="sys",
            prompt="prompt",
            operation="test",
        )
    assert result == {}
