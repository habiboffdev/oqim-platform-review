"""Resolve a per-agent-kind compaction summary template.

Maps an OQIM ``agent_kind`` (the `_agent_kind` taxonomy: ``seller_agent``,
``custom_agent``, ``setup_agent``, ...) to a managed markdown template asset.
Unregistered kinds return ``None`` so the OqimContextCompressor falls back to
Hermes's built-in coding template. See
docs/superpowers/specs/2026-06-12-per-agent-kind-compaction-templates-design.md.
"""
from __future__ import annotations

_SELLER_ASSET_ID = "agent_runtime.compaction.seller"
_PERSONAL_ASSET_ID = "agent_runtime.compaction.personal"
_ASSET_VERSION = "1.0.0"


def resolve_compaction_template_id(agent_kind: str | None) -> str | None:
    """Pure mapping from agent_kind to a compaction template asset id (or None)."""
    kind = (agent_kind or "").strip()
    if kind.startswith("seller"):
        return _SELLER_ASSET_ID
    if kind == "custom_agent":
        return _PERSONAL_ASSET_ID
    return None


def resolve_compaction_template(agent_kind: str | None) -> str | None:
    """Return the loaded template body for ``agent_kind`` (or None for default)."""
    asset_id = resolve_compaction_template_id(agent_kind)
    if asset_id is None:
        return None
    from app.brain.prompt_registry import get_prompt_registry

    return get_prompt_registry().load(asset_id, version=_ASSET_VERSION).body
