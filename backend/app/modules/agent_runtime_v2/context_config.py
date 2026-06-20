"""Per-agent Hermes context-window resolution.

OQIM serves gemini through an OpenAI-compatible shim at a sentinel base_url, so
Hermes's native context-length detector cannot probe the endpoint and falls back
to a 256K guess (logging a warning every turn). We resolve a per-agent window
here (default = gemini's true 1M window), clamp it to Hermes's safe range, and
inject it through a vendor patch (see hermes/vendor_patches.py). The window also
sets where Hermes's compactor fires: threshold_tokens = max(window * 0.50, 64K).
"""
from __future__ import annotations

# Hermes rejects models below MINIMUM_CONTEXT_LENGTH (64K) at AIAgent init
# (run_agent.py:2375 raises), so this is a HARD floor, not a preference.
CONTEXT_WINDOW_MIN = 64_000
# Gemini's true window; matches Hermes's own DEFAULT_CONTEXT_LENGTHS["gemini"].
CONTEXT_WINDOW_MAX = 1_048_576
CONTEXT_WINDOW_DEFAULT = 1_048_576


def resolve_context_window(channel_config: dict | None) -> int:
    """Read ``channel_config['context']['window_tokens']`` and clamp to
    [CONTEXT_WINDOW_MIN, CONTEXT_WINDOW_MAX]; return the default on absent or
    invalid input. Shaped so a future ``threshold`` knob can live under the same
    ``context`` object without a migration."""
    if not isinstance(channel_config, dict):
        return CONTEXT_WINDOW_DEFAULT
    context_cfg = channel_config.get("context")
    if not isinstance(context_cfg, dict):
        return CONTEXT_WINDOW_DEFAULT
    raw = context_cfg.get("window_tokens")
    # bool is a subclass of int — reject it explicitly so True/False is not 1/0.
    if isinstance(raw, bool) or not isinstance(raw, int):
        return CONTEXT_WINDOW_DEFAULT
    return max(CONTEXT_WINDOW_MIN, min(raw, CONTEXT_WINDOW_MAX))
