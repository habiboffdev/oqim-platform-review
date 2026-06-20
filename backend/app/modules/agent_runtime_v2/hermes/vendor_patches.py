"""Idempotent patches to the packaged Hermes engine so it presents OQIM's
identity, not Nous Research's. run_agent.py imports these constants BY VALUE
(run_agent.py:145-148), so we must rebind them on the `run_agent` module, not
on `prompt_builder`. Empty strings are dropped by the stable-tier join guard
(run_agent.py:6259), leaving our ephemeral_system_prompt (AGENT.md) as the
sole identity. The identity constants carry no tool-use mechanics (those live
in TOOL_USE_ENFORCEMENT_GUIDANCE / GOOGLE_MODEL_OPERATIONAL_GUIDANCE), so this
does not affect tool-calling.

We also bridge contextvar propagation into Hermes's inner request thread. The
shim reads per-run state (workspace/chain/loop) from the ``current_tool_context``
contextvar. ``asyncio.to_thread`` copies the context into the loop's worker
thread, BUT ``_interruptible_api_call`` (run_agent.py:7742) dispatches the actual
``client.chat.completions.create`` onto a *fresh* ``threading.Thread(target=_call)``
(run_agent.py:7822) — and a raw ``threading.Thread`` does NOT inherit contextvars.
So the shim would see ``None`` there and raise. We rebind ``run_agent.threading``
to a namespace whose ``Thread`` captures ``contextvars.copy_context()`` at
construction (on the worker thread, where the context IS valid) and runs its
target inside it. Scoped to ``run_agent``'s module-global ``threading`` only —
the process-wide stdlib ``threading.Thread`` is untouched. Stateless and
per-construction, so it is correct under concurrent runs (no shared module-global
binds a single run's state)."""
from __future__ import annotations

import contextvars
import threading
import types

from app.modules.agent_runtime_v2.hermes._bootstrap import ensure_hermes_runtime

_applied = False


class _ContextCopyingThread(threading.Thread):
    """A ``threading.Thread`` that propagates the constructing thread's
    contextvars into the spawned thread (mirrors ``asyncio.to_thread`` /
    ``loop.run_in_executor`` context-copy semantics). Capturing the *current*
    thread's context at construction is always safe — it never binds another
    run's state — so this can live as a permanent, install-once rebind."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._oqim_captured_ctx = contextvars.copy_context()

    def run(self):
        self._oqim_captured_ctx.run(super().run)


def _build_threading_shim() -> types.ModuleType:
    """A stand-in for the ``threading`` module: delegates every attribute to the
    real module except ``Thread``, which becomes the context-copying subclass."""
    shim = types.ModuleType("oqim_hermes_threading_shim")
    shim.__dict__.update(threading.__dict__)
    shim.Thread = _ContextCopyingThread
    return shim


def _install_auxiliary_oqim_routing() -> None:
    """Route Hermes's AUXILIARY text LLM work through OQIM's Gemini when inside an
    OQIM run, instead of the upstream Nous/OpenRouter/direct-api-key chain.

    Every sync-text provider lookup funnels through
    ``agent.auxiliary_client.resolve_provider_client`` (compression via the
    trajectory compressor, ``call_llm`` -> ``_get_cached_client``, the boot-time
    ``get_text_auxiliary_client`` feasibility probe, and the main-loop fallback).
    We wrap it so that — when an OQIM ToolContext is active and the call is sync +
    non-vision — it returns OQIM's ShimClient (same bridge the main loop uses:
    budget + fallback, no provider health checks). Async/vision lookups and
    out-of-run calls fall through to the original router unchanged. Idempotent:
    re-wrapping is guarded by the ``_oqim_patched`` marker."""
    import agent.auxiliary_client as aux

    original = aux.resolve_provider_client
    if getattr(original, "_oqim_patched", False):
        return

    def _patched(
        provider,
        model=None,
        async_mode=False,
        raw_codex=False,
        explicit_base_url=None,
        explicit_api_key=None,
        api_mode=None,
        main_runtime=None,
        is_vision=False,
    ):
        from app.modules.agent_runtime_v2.hermes.openai_shim import (
            oqim_aux_text_client_or_none,
        )

        client = oqim_aux_text_client_or_none(async_mode=async_mode, is_vision=is_vision)
        if client is not None:
            return client, (model or "gemini")
        return original(
            provider,
            model=model,
            async_mode=async_mode,
            raw_codex=raw_codex,
            explicit_base_url=explicit_base_url,
            explicit_api_key=explicit_api_key,
            api_mode=api_mode,
            main_runtime=main_runtime,
            is_vision=is_vision,
        )

    _patched._oqim_patched = True
    _patched.__wrapped_original__ = original
    aux.resolve_provider_client = _patched


def _install_context_length_resolver() -> None:
    """Resolve OQIM's shimmed gemini to a real context window instead of the 256K
    probe-down fallback (which also logs a warning every turn).

    The shim's base_url (OQIM_SHIM_BASE_URL) is a sentinel that is never hit over
    HTTP, so Hermes's native detector returns DEFAULT_FALLBACK_CONTEXT (256K) at
    model_metadata.py:1588 — short-circuiting BEFORE its own hardcoded
    "gemini": 1_048_576 entry. We wrap the resolver so that, for the sentinel
    base_url, it returns the per-run window from the ToolContext (default 1M),
    clamped to Hermes's safe range. Every other endpoint delegates untouched.

    context_compressor.py imports get_model_context_length BY VALUE at module load
    (context_compressor.py:28), and ContextCompressor.__init__ is what emits the
    warning, so we MUST rebind on agent.context_compressor too — not just
    agent.model_metadata. run_agent's call sites re-import from agent.model_metadata
    at call time, so the model_metadata rebind covers them. Idempotent via the
    _oqim_patched marker."""
    import agent.context_compressor as cc
    import agent.model_metadata as mm

    from app.modules.agent_runtime_v2.context_config import (
        CONTEXT_WINDOW_DEFAULT,
        CONTEXT_WINDOW_MAX,
        CONTEXT_WINDOW_MIN,
    )
    from app.modules.agent_runtime_v2.hermes.openai_shim import OQIM_SHIM_BASE_URL
    from app.modules.agent_runtime_v2.hermes.tool_context import current_tool_context

    original = mm.get_model_context_length
    if getattr(original, "_oqim_patched", False):
        return

    def _patched(
        model,
        base_url="",
        api_key="",
        config_context_length=None,
        provider="",
        custom_providers=None,
    ):
        # An explicit override always wins — delegate so upstream step 0 honors it.
        if config_context_length:
            return original(
                model, base_url, api_key, config_context_length, provider, custom_providers
            )
        if base_url == OQIM_SHIM_BASE_URL:
            ctx = current_tool_context.get()
            window = ctx.context_window if ctx is not None else CONTEXT_WINDOW_DEFAULT
            return max(CONTEXT_WINDOW_MIN, min(int(window), CONTEXT_WINDOW_MAX))
        return original(
            model, base_url, api_key, config_context_length, provider, custom_providers
        )

    _patched._oqim_patched = True
    _patched.__wrapped_original__ = original
    mm.get_model_context_length = _patched
    cc.get_model_context_length = _patched


def _install_hermes_home_resolver() -> None:
    """Make Hermes's ``get_hermes_home`` per-workspace for OWNER runs (run-model X,
    spike #439), via the ``current_hermes_home`` contextvar.

    When the contextvar is set (owner/setup runs), every runtime
    ``get_hermes_home()`` call — skill discovery (``get_skills_dir``), SOUL.md,
    ``config.yaml`` (MCP), optional-skills — resolves to that workspace's home.
    When unset (the seller plane), it delegates to the original, so the seller
    path is byte-identical. ``engine.py`` dispatches the run via
    ``asyncio.to_thread`` which copies the contextvar into the worker thread, so
    the value reaches all of Hermes's runtime loading.

    Rebound on BOTH ``hermes_constants`` (covers ``get_skills_dir`` /
    ``get_config_path`` / ``get_optional_skills_dir``, which call the module-local
    name) AND the by-value importers ``run_agent`` / ``agent.prompt_builder``
    (which did ``from hermes_constants import get_hermes_home``). Idempotent via
    the ``_oqim_patched`` marker."""
    import hermes_constants
    import run_agent

    from app.modules.agent_runtime_v2.hermes.hermes_home_context import (
        current_hermes_home,
    )

    original = hermes_constants.get_hermes_home
    if getattr(original, "_oqim_patched", False):
        return

    def _patched():
        home = current_hermes_home.get()
        if home is not None:
            return home
        return original()

    _patched._oqim_patched = True
    _patched.__wrapped_original__ = original
    hermes_constants.get_hermes_home = _patched
    run_agent.get_hermes_home = _patched
    try:
        import agent.prompt_builder as pb

        pb.get_hermes_home = _patched
    except Exception:  # prompt_builder layout may differ; the hermes_constants
        pass  # rebind already covers get_skills_dir/get_config_path.


def _install_compaction_compressor() -> None:
    """Rebind ``run_agent.ContextCompressor`` to the OQIM kind-aware subclass.

    run_agent imports ``ContextCompressor`` BY VALUE (run_agent.py:160) and
    constructs it at run_agent.py:2355 for every agent's context compressor, so
    rebinding the module global swaps the class used for both live preflight
    compaction and the manual ``oqim ai compact`` path. The subclass picks a
    per-agent-kind compaction summary template (seller / personal) and delegates
    to the Hermes default for unregistered kinds and any LLM error. Idempotent:
    the subclass is memoized and we skip if the binding already points at it."""
    import run_agent

    from app.modules.agent_runtime_v2.hermes.compaction_compressor import (
        get_oqim_context_compressor_class,
    )

    cls = get_oqim_context_compressor_class()
    if run_agent.ContextCompressor is cls:
        return
    run_agent.ContextCompressor = cls


def apply_vendor_patches() -> None:
    global _applied
    if _applied:
        return
    ensure_hermes_runtime()
    import run_agent
    run_agent.DEFAULT_AGENT_IDENTITY = ""
    run_agent.HERMES_AGENT_HELP_GUIDANCE = ""
    # Strip the upstream CODING-agent system scaffolding. run_agent imports these
    # BY VALUE (run_agent.py:163) and appends them to the stable system-prompt tier
    # (run_agent.py:6142/6147/6151) whenever the model name matches gpt/gemini/etc.,
    # priming a software-engineering agent ("run the tests", "check package.json",
    # "use sha256sum/read_file", absolute-path directives) — wrong for a customer
    # seller and ~6KB of prompt bloat per call. OQIM uses a managed prompt asset
    # plus rendered AGENT.md as the whole system prompt. Empty strings are
    # dropped by the join guard.
    run_agent.TOOL_USE_ENFORCEMENT_GUIDANCE = ""
    run_agent.GOOGLE_MODEL_OPERATIONAL_GUIDANCE = ""
    run_agent.OPENAI_MODEL_EXECUTION_GUIDANCE = ""
    run_agent.threading = _build_threading_shim()
    _install_auxiliary_oqim_routing()
    _install_context_length_resolver()
    _install_hermes_home_resolver()
    _install_compaction_compressor()
    _applied = True
