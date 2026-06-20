"""Inside an OQIM agent run, Hermes's AUXILIARY LLM work (compression,
summarization, the boot-time feasibility probe, the main-loop fallback) must
route through OQIM's Gemini — the same shim the main loop uses — not the
upstream Nous/OpenRouter/direct-api-key chain.

The Hermes auxiliary client funnels every sync-text provider lookup through
``agent.auxiliary_client.resolve_provider_client`` (call_llm -> _get_cached_client,
the trajectory compressor, get_text_auxiliary_client, and the fallback chain).
We short-circuit it to OQIM's ShimClient when an OQIM ToolContext is active and
the call is sync + non-vision (the contract the sync shim can serve). Async and
vision lookups fall through to the upstream router untouched.
"""
from app.modules.agent_runtime_v2.hermes.openai_shim import (
    ShimClient,
    oqim_aux_text_client_or_none,
)
from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext, use_tool_context
from app.modules.agent_runtime_v2.hermes.vendor_patches import apply_vendor_patches


def _ctx() -> ToolContext:
    return ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None,
        grounding=[], history=[], chain_name="FLASH_CHAIN", loop=None,
    )


def test_aux_text_client_is_none_outside_a_run():
    # No ToolContext active -> we are not inside an OQIM run; do not hijack the
    # upstream router (returns None so the wrapper falls through to the original).
    assert oqim_aux_text_client_or_none(async_mode=False, is_vision=False) is None


def test_aux_text_client_is_the_oqim_shim_inside_a_run():
    with use_tool_context(_ctx()):
        client = oqim_aux_text_client_or_none(async_mode=False, is_vision=False)
        assert isinstance(client, ShimClient)


def test_aux_text_client_falls_through_for_async_and_vision():
    # The sync ShimClient cannot serve an async client or vision (image) calls,
    # so those must fall through to the upstream router even inside a run.
    with use_tool_context(_ctx()):
        assert oqim_aux_text_client_or_none(async_mode=True, is_vision=False) is None
        assert oqim_aux_text_client_or_none(async_mode=False, is_vision=True) is None


def test_apply_vendor_patches_routes_resolve_provider_client_through_oqim():
    apply_vendor_patches()
    import agent.auxiliary_client as aux

    assert getattr(aux.resolve_provider_client, "_oqim_patched", False) is True

    # Inside a run, a sync-text provider lookup returns the OQIM shim + the
    # requested model label, short-circuiting the Nous/OpenRouter/api-key chain
    # (no network, no provider health checks).
    with use_tool_context(_ctx()):
        client, model = aux.resolve_provider_client("auto", "gemini-3-flash-preview")
        assert isinstance(client, ShimClient)
        assert model == "gemini-3-flash-preview"

        # No explicit model -> a sane default label.
        client2, model2 = aux.resolve_provider_client("auto")
        assert isinstance(client2, ShimClient)
        assert model2 == "gemini"


def test_apply_vendor_patches_is_idempotent_for_aux_routing():
    apply_vendor_patches()
    apply_vendor_patches()
    import agent.auxiliary_client as aux

    # Double-applied: still a single wrapper (guarded by _oqim_patched), not
    # wrapped twice.
    inner = getattr(aux.resolve_provider_client, "__wrapped_original__", None)
    assert inner is not None
    assert getattr(inner, "_oqim_patched", False) is False
