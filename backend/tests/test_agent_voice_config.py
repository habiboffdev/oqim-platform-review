"""Agent Voice config: presets, knob resolution, voice block, composition, loader."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.brain.llm import LLMToolResponse
from app.brain.prompt_registry import get_prompt_registry
from app.modules.agent_runtime_v2.config_loader import AgentConfig, AgentConfigLoader
from app.modules.agent_runtime_v2.hermes.engine import HermesEngineAdapter
from app.modules.agent_runtime_v2.reply_runtime import (
    compose_hermes_system_prompt,
    load_voice_preset_asset,
    render_voice_block,
)
from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfileCompiler
from app.modules.agent_runtime_v2.voice_presets import (
    VOICE_PRESETS,
    ResolvedVoice,
    resolve_voice,
)
from app.modules.agent_talking.contracts import TalkingPolicy


class TestPresetAssets:
    def test_every_preset_registers_a_loadable_em_dash_free_asset(self):
        registry = get_prompt_registry()
        for preset_id, preset in VOICE_PRESETS.items():
            assert preset.preset_id == preset_id
            assert preset.asset_id == f"agent_runtime.voice.{preset_id}"
            asset = registry.load(preset.asset_id, version="1.0.0")
            body = asset.body.strip()
            assert body, f"{preset.asset_id} has empty body"
            assert "—" not in body, f"{preset.asset_id} demonstrates an em-dash"
            # voice prose describes HOW to sound; it is wrapped by render_voice_block,
            # so the asset body itself must NOT carry the <voice> tag.
            assert "<voice>" not in body
            assert len(body) <= 1200, f"{preset.asset_id} grew to {len(body)} chars"

    def test_preset_default_knobs_are_in_range(self):
        for preset in VOICE_PRESETS.values():
            assert preset.verbosity in {"terse", "balanced", "rich"}
            assert preset.emoji in {"low", "medium", "high"}
            assert preset.bubble_length in {"short", "medium", "long"}
            assert 1 <= preset.max_bubbles <= 3
            assert preset.pacing in {"none", "fast", "human", "slow"}

    def test_expected_presets_exist_with_seller_default_warm(self):
        assert set(VOICE_PRESETS) == {"warm_seller", "friendly", "professional", "gen_z"}
        warm = VOICE_PRESETS["warm_seller"]
        assert warm.verbosity == "rich"
        assert warm.bubble_length == "long"  # 700 chars; restores fuller seller bubbles


class TestResolveVoice:
    def test_absent_voice_preserves_legacy_talking_and_emits_no_block(self):
        resolved = resolve_voice({"talking": {"max_chars": 500}}, "seller_agent")
        assert resolved == ResolvedVoice(
            talking_overrides={"max_chars": 500},
            preset_asset_id=None,
            verbosity=None,
            additional_instructions="",
        )

    def test_no_channel_config_is_fully_inert(self):
        resolved = resolve_voice(None, "custom_agent")
        assert resolved.talking_overrides is None
        assert resolved.preset_asset_id is None

    def test_preset_supplies_defaults_for_unset_knobs(self):
        resolved = resolve_voice({"voice": {"preset": "warm_seller"}}, "seller_agent")
        assert resolved.preset_asset_id == "agent_runtime.voice.warm_seller"
        assert resolved.verbosity == "rich"
        # warm_seller defaults: emoji medium, bubble_length long (700), max_bubbles 3, human
        assert resolved.talking_overrides == {
            "emoji_usage": "medium",
            "max_chars": 700,
            "max_bubbles": 3,
            "pacing": "human",
        }

    def test_explicit_knobs_override_preset_defaults(self):
        resolved = resolve_voice(
            {
                "voice": {
                    "preset": "professional",
                    "verbosity": "rich",
                    "emoji": "high",
                    "bubble_length": "short",
                    "max_bubbles": 1,
                    "pacing": "slow",
                }
            },
            "seller_agent",
        )
        assert resolved.verbosity == "rich"
        assert resolved.talking_overrides == {
            "emoji_usage": "high",
            "max_chars": 220,
            "max_bubbles": 1,
            "pacing": "slow",
        }

    def test_resolved_overrides_are_accepted_by_talking_policy(self):
        resolved = resolve_voice({"voice": {"preset": "friendly"}}, "seller_agent")
        policy = TalkingPolicy.for_agent(**resolved.talking_overrides)
        assert policy.emoji_usage == "high"
        assert policy.max_chars_per_bubble == 400  # friendly bubble_length=medium
        assert policy.max_bubbles_per_turn == 3
        assert policy.pacing_profile == "human"

    def test_unknown_preset_falls_back_to_kind_default(self):
        seller = resolve_voice({"voice": {"preset": "nope"}}, "seller_agent")
        assert seller.preset_asset_id == "agent_runtime.voice.warm_seller"
        non_seller = resolve_voice({"voice": {"preset": "nope"}}, "support_agent")
        assert non_seller.preset_asset_id == "agent_runtime.voice.professional"

    def test_garbage_knobs_never_crash_and_fall_back_to_preset_defaults(self):
        resolved = resolve_voice(
            {
                "voice": {
                    "preset": "warm_seller",
                    "emoji": "ultra",
                    "bubble_length": "huge",
                    "max_bubbles": 99,
                    "pacing": "zoom",
                    "verbosity": "loud",
                }
            },
            "seller_agent",
        )
        # all bad values fall back to warm_seller's defaults; nothing raised
        assert resolved.verbosity == "rich"
        assert resolved.talking_overrides == {
            "emoji_usage": "medium",
            "max_chars": 700,
            "max_bubbles": 3,
            "pacing": "human",
        }
        # and the result is still a valid TalkingPolicy
        TalkingPolicy.for_agent(**resolved.talking_overrides)

    def test_additional_instructions_are_capped_and_em_dash_stripped(self):
        dirty = "Be bold — always — confident. " + "x" * 600
        resolved = resolve_voice(
            {"voice": {"preset": "gen_z", "additional_instructions": dirty}},
            "seller_agent",
        )
        assert "—" not in resolved.additional_instructions
        assert len(resolved.additional_instructions) <= 500
        assert resolved.additional_instructions.startswith("Be bold, always, confident.")


class TestRenderVoiceBlock:
    def test_block_has_preset_text_then_verbosity_line_then_additions_in_order(self):
        block = render_voice_block(
            "Speak warmly.", verbosity="rich", additional_instructions="Mention free trial."
        )
        assert block.startswith("<voice>\n")
        assert block.endswith("\n</voice>")
        i_preset = block.index("Speak warmly.")
        i_verbosity = block.index("Be warm and full when it helps the sale")
        i_extra = block.index("Mention free trial.")
        assert i_preset < i_verbosity < i_extra

    def test_verbosity_lines_map_per_level(self):
        assert "short and to the point" in render_voice_block("x", verbosity="terse")
        assert "concise but complete" in render_voice_block("x", verbosity="balanced")
        assert "Never clip the pitch" in render_voice_block("x", verbosity="rich")

    def test_unknown_verbosity_falls_back_to_balanced_line(self):
        assert "concise but complete" in render_voice_block("x", verbosity="bogus")
        assert "concise but complete" in render_voice_block("x", verbosity=None)

    def test_empty_preset_body_yields_empty_block(self):
        assert render_voice_block("", verbosity="rich") == ""
        assert render_voice_block("   ", verbosity="rich") == ""

    def test_no_additions_omits_the_freeform_segment(self):
        block = render_voice_block("Speak warmly.", verbosity="balanced")
        assert block.count("\n\n") == 1  # preset + verbosity only

    def test_load_voice_preset_asset_returns_stripped_body(self):
        body = load_voice_preset_asset("agent_runtime.voice.gen_z")
        assert "playful" in body
        assert body == body.strip()
        assert "<voice>" not in body  # wrapper is added by render, not the asset


class TestComposeVoiceLayer:
    def test_voice_block_sits_between_playbook_and_runtime_kind(self):
        voice = "<voice>\nSpeak with playful Gen Z energy.\n</voice>"
        prompt = compose_hermes_system_prompt(
            "# A\nSotuvchi.", "seller_agent", voice_block=voice
        )
        assert "Speak with playful Gen Z energy." in prompt
        # order: seller_playbook (Discovery first) -> <voice> -> Runtime agent kind -> AGENT.md
        assert prompt.index("Discovery first") < prompt.index("<voice>")
        assert prompt.index("<voice>") < prompt.index("Runtime agent kind:")
        assert prompt.index("Runtime agent kind:") < prompt.index("Rendered AGENT.md:")

    def test_absent_voice_block_leaves_prompt_unchanged(self):
        # backward compat: no voice_block == byte-identical to the pre-feature prompt
        with_default = compose_hermes_system_prompt("# A\nSotuvchi.", "seller_agent")
        with_none = compose_hermes_system_prompt(
            "# A\nSotuvchi.", "seller_agent", voice_block=None
        )
        assert with_default == with_none
        assert "<voice>" not in with_default

    def test_empty_voice_block_string_adds_no_layer(self):
        prompt = compose_hermes_system_prompt(
            "# A\nSotuvchi.", "seller_agent", voice_block="   "
        )
        assert "<voice>" not in prompt

    def test_voice_block_composes_for_non_seller_kind_too(self):
        # voice is independent of the seller playbook (which is seller-only)
        voice = "<voice>\nCalm and precise.\n</voice>"
        prompt = compose_hermes_system_prompt(
            "# A\nSupport.", "support_agent", voice_block=voice
        )
        assert "Calm and precise." in prompt
        assert "<seller_playbook>" not in prompt  # still no playbook for support
        assert prompt.index("<voice>") < prompt.index("Runtime agent kind:")


class TestAgentConfigLoaderVoice:
    pytestmark = pytest.mark.asyncio

    async def test_voice_config_populates_block_and_overrides(self, db_session, workspace, agent):
        agent.channel_config = {
            "voice": {"preset": "gen_z", "emoji": "high", "additional_instructions": "Keep it fun."}
        }
        await db_session.flush()
        config = await AgentConfigLoader(db_session).load(
            workspace_id=workspace.id, agent_id=agent.id
        )
        assert config.voice_block is not None
        assert "<voice>" in config.voice_block
        assert "playful" in config.voice_block  # gen_z preset prose
        assert "Keep it fun." in config.voice_block
        # gen_z knobs -> emoji explicit high, bubble_length medium (400), max_bubbles 3, human
        assert config.talking_overrides == {
            "emoji_usage": "high",
            "max_chars": 400,
            "max_bubbles": 3,
            "pacing": "human",
        }

    async def test_no_voice_config_yields_no_block_and_no_overrides(self, db_session, workspace, agent):
        # the default `agent` fixture has channel_config without "voice" or "talking"
        config = await AgentConfigLoader(db_session).load(
            workspace_id=workspace.id, agent_id=agent.id
        )
        assert config.voice_block is None
        assert config.talking_overrides is None

    async def test_legacy_talking_overrides_preserved_when_no_voice(self, db_session, workspace, agent):
        agent.channel_config = {"talking": {"max_chars": 500, "pacing": "slow"}}
        await db_session.flush()
        config = await AgentConfigLoader(db_session).load(
            workspace_id=workspace.id, agent_id=agent.id
        )
        assert config.voice_block is None
        assert config.talking_overrides == {"max_chars": 500, "pacing": "slow"}


class TestEngineThreadsVoiceBlock:
    pytestmark = pytest.mark.asyncio

    async def test_voice_block_reaches_the_model_system_instruction(self):
        seen: dict = {}

        async def _fake_gwt(**kw):
            seen.update(kw)
            return LLMToolResponse(
                text="Salom!", tool_calls=[], model_used="m", provider="gemini"
            )

        config = AgentConfig(
            agent_id=2,
            workspace_id=1,
            name="Sotuvchi",
            trust_mode="disabled",
            auto_send_threshold=0.85,
            agent_md="# Sotuvchi\nSen sotuvchisan.",
            voice_block="<voice>\nSpeak with playful Gen Z energy.\n</voice>",
        )
        profile = RuntimeProfileCompiler().compile_agent(config=config, agent_kind="seller")
        with patch(
            "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt
        ):
            await HermesEngineAdapter().run(
                config=config,
                profile=profile,
                customer_message="Salom",
                grounding=[],
                history=[],
                agent_kind="seller",
            )
        # the shim pulls the system message out as `system_instruction`
        assert "Speak with playful Gen Z energy." in (seen.get("system_instruction") or "")
