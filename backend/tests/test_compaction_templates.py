from __future__ import annotations

import types

from app.brain.prompt_registry import get_prompt_registry, load_prompt_manifest
from app.modules.agent_runtime_v2.hermes.compaction_templates import (
    resolve_compaction_template,
    resolve_compaction_template_id,
)
from app.modules.agent_runtime_v2.hermes.tool_context import (
    ToolContext,
    use_tool_context,
)
from app.modules.agent_runtime_v2.hermes.vendor_patches import apply_vendor_patches


def test_resolver_maps_kinds_to_asset_ids():
    assert resolve_compaction_template_id("seller_agent") == "agent_runtime.compaction.seller"
    assert resolve_compaction_template_id("seller") == "agent_runtime.compaction.seller"
    assert resolve_compaction_template_id("custom_agent") == "agent_runtime.compaction.personal"
    assert resolve_compaction_template_id("setup_agent") is None
    assert resolve_compaction_template_id("support_agent") is None
    assert resolve_compaction_template_id("") is None
    assert resolve_compaction_template_id(None) is None


def test_resolver_loads_template_bodies():
    seller = resolve_compaction_template("seller_agent")
    personal = resolve_compaction_template("custom_agent")
    assert seller is not None and "## Sales Objective" in seller
    assert "## Relevant Files" not in seller
    assert personal is not None and "## Current Focus" in personal
    assert resolve_compaction_template("setup_agent") is None


def test_compaction_assets_load_and_are_em_dash_free():
    registry = get_prompt_registry()
    for asset_id in (
        "agent_runtime.compaction.seller",
        "agent_runtime.compaction.personal",
    ):
        asset = registry.load(asset_id, version="1.0.0")
        assert asset.body.strip()
        assert "—" not in asset.body  # em-dash banned by convention
        assert "draft" not in asset.body.lower()


def test_compaction_assets_have_manifest_rows():
    manifest = load_prompt_manifest()
    ids = {entry.id for entry in manifest.entries}
    assert "agent_runtime.compaction.seller" in ids
    assert "agent_runtime.compaction.personal" in ids


def test_tool_context_carries_agent_kind():
    from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext

    default_ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
    )
    assert default_ctx.agent_kind == "custom_agent"

    seller_ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
        agent_kind="seller_agent",
    )
    assert seller_ctx.agent_kind == "seller_agent"


def _make_compressor():
    """A fully-constructed OqimContextCompressor (built via AIAgent so we get
    real parent state without the 20-arg ContextCompressor constructor)."""
    apply_vendor_patches()
    from run_agent import AIAgent

    from app.modules.agent_runtime_v2.hermes.openai_shim import OQIM_SHIM_BASE_URL

    boot_ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
    )
    with use_tool_context(boot_ctx):
        agent = AIAgent(
            base_url=OQIM_SHIM_BASE_URL, api_key="x", provider="openai",
            api_mode="chat_completions", model="gemini", enabled_toolsets=["oqim"],
            ephemeral_system_prompt="# Sotuvchi", skip_context_files=True,
            skip_memory=True, save_trajectories=False, quiet_mode=True,
            max_iterations=4, session_db=None,
        )
    return agent.context_compressor


def _fake_response(text: str):
    msg = types.SimpleNamespace(content=text)
    choice = types.SimpleNamespace(message=msg)
    return types.SimpleNamespace(choices=[choice])


_TURNS = [
    {"role": "user", "content": "salom, kurs narxi qancha " * 20},
    {"role": "assistant", "content": "narx 4 900 000 so'm " * 20},
    {"role": "user", "content": "telefon raqamim 998901234567 " * 20},
]


def test_vendor_patch_rebinds_compressor_and_is_idempotent():
    apply_vendor_patches()
    apply_vendor_patches()  # second call must not double-rebind
    import agent.context_compressor as cc
    import run_agent

    from app.modules.agent_runtime_v2.hermes.compaction_compressor import (
        get_oqim_context_compressor_class,
    )

    cls = get_oqim_context_compressor_class()
    assert run_agent.ContextCompressor is cls
    assert issubclass(cls, cc.ContextCompressor)


def test_seller_ctx_builds_prompt_with_seller_sections(monkeypatch):
    import agent.context_compressor as cc

    captured = {}

    def _fake_call_llm(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return _fake_response("## Sales Objective\nCustomer wants the course.")

    monkeypatch.setattr(cc, "call_llm", _fake_call_llm)

    compressor = _make_compressor()
    compressor._previous_summary = None  # force first-compaction path
    seller_ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
        agent_kind="seller_agent",
    )
    with use_tool_context(seller_ctx):
        out = compressor._generate_summary(_TURNS)

    assert out is not None
    assert "## Sales Objective" in captured["prompt"]
    assert "## Handoff and Next Step" in captured["prompt"]
    assert "## Relevant Files" not in captured["prompt"]
    assert "## Active Task" not in captured["prompt"]  # coding-template marker


def test_custom_ctx_builds_prompt_with_personal_sections(monkeypatch):
    import agent.context_compressor as cc

    captured = {}

    def _fake_call_llm(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return _fake_response("## Current Focus\nHelping the user.")

    monkeypatch.setattr(cc, "call_llm", _fake_call_llm)

    compressor = _make_compressor()
    compressor._previous_summary = None
    custom_ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
        agent_kind="custom_agent",
    )
    with use_tool_context(custom_ctx):
        out = compressor._generate_summary(_TURNS)

    assert out is not None
    assert "## Current Focus" in captured["prompt"]
    assert "## Relevant Files" not in captured["prompt"]


def test_unknown_kind_delegates_to_super(monkeypatch):
    import agent.context_compressor as cc

    # Spy on the base coding-template summarizer. The subclass must delegate
    # here (Hermes default) for any unregistered kind.
    monkeypatch.setattr(
        cc.ContextCompressor,
        "_generate_summary",
        lambda self, turns, focus_topic=None: "BASE_DEFAULT",
    )

    compressor = _make_compressor()
    setup_ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
        agent_kind="setup_agent",
    )
    with use_tool_context(setup_ctx):
        out = compressor._generate_summary(_TURNS)
    assert out == "BASE_DEFAULT"


def test_no_context_delegates_to_super(monkeypatch):
    import agent.context_compressor as cc

    monkeypatch.setattr(
        cc.ContextCompressor,
        "_generate_summary",
        lambda self, turns, focus_topic=None: "BASE_DEFAULT",
    )
    compressor = _make_compressor()
    # No active ToolContext -> agent_kind unknown -> Hermes default.
    out = compressor._generate_summary(_TURNS)
    assert out == "BASE_DEFAULT"
