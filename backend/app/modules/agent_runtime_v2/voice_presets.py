"""Agent Voice presets: curated personality + default mechanical knobs.

A preset maps a personality id to (a) a managed prompt asset that holds the
`<voice>` prose and (b) typed default knob values. The asset is version-
controlled and em-dash-guarded like every other prompt; the defaults are typed
here so they are cheap to test without loading markdown.

Personality is SEPARATE from method: a preset is tone only. Selling moves live
in `seller_playbook`, business facts in AGENT.md. A "gen_z" voice still runs the
same close.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoicePreset:
    preset_id: str
    asset_id: str  # agent_runtime.voice.<preset_id>
    verbosity: str  # terse | balanced | rich
    emoji: str  # low | medium | high
    bubble_length: str  # short | medium | long
    max_bubbles: int  # 1..3
    pacing: str  # none | fast | human | slow


VOICE_PRESETS: dict[str, VoicePreset] = {
    "warm_seller": VoicePreset(
        "warm_seller", "agent_runtime.voice.warm_seller", "rich", "medium", "long", 3, "human"
    ),
    "friendly": VoicePreset(
        "friendly", "agent_runtime.voice.friendly", "balanced", "high", "medium", 3, "human"
    ),
    "professional": VoicePreset(
        "professional", "agent_runtime.voice.professional", "balanced", "low", "medium", 2, "human"
    ),
    "gen_z": VoicePreset(
        "gen_z", "agent_runtime.voice.gen_z", "balanced", "high", "medium", 3, "human"
    ),
}


_BUBBLE_LENGTH_CHARS = {"short": 220, "medium": 400, "long": 700}
_EMOJI_LEVELS = {"low", "medium", "high"}
_BUBBLE_LENGTHS = {"short", "medium", "long"}
_PACING_PROFILES = {"none", "fast", "human", "slow"}
_VERBOSITY_LEVELS = {"terse", "balanced", "rich"}
_ADDITIONAL_INSTRUCTIONS_MAX = 500


@dataclass(frozen=True)
class ResolvedVoice:
    talking_overrides: dict | None
    preset_asset_id: str | None  # None => no <voice> block (legacy / absent)
    verbosity: str | None
    additional_instructions: str


def _kind_default_preset_id(agent_kind: str) -> str:
    return "warm_seller" if (agent_kind or "").startswith("seller") else "professional"


def _enum_or_default(value: object, allowed: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _bubbles_or_default(value: object, default: int) -> int:
    # bool is an int subclass; reject it explicitly so True is never a knob.
    if isinstance(value, bool):
        return default
    return value if isinstance(value, int) and 1 <= value <= 3 else default


def _strip_em_dashes(text: str) -> str:
    # The model imitates the prompt's form; an em-dash anywhere the model sees
    # teaches it to use them (live leak 2026-06-11). Collapse "a — b" to "a, b".
    return re.sub(r"\s*—\s*", ", ", text)


def _sanitize_additional_instructions(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _strip_em_dashes(value).strip()[:_ADDITIONAL_INSTRUCTIONS_MAX]


def resolve_voice(channel_config: dict | None, agent_kind: str) -> ResolvedVoice:
    """Resolve a per-agent voice config into mechanical + prompt material.

    Resolution order per field: explicit value in ``voice`` -> the chosen
    preset's default. When ``voice`` is absent entirely, preserve the legacy
    ``channel_config.talking`` overrides and emit no voice block, so existing
    agents are byte-for-byte unchanged.
    """
    config = channel_config or {}
    voice = config.get("voice")
    if not isinstance(voice, dict):
        legacy = config.get("talking")
        return ResolvedVoice(
            talking_overrides=legacy if isinstance(legacy, dict) and legacy else None,
            preset_asset_id=None,
            verbosity=None,
            additional_instructions="",
        )

    preset_id = voice.get("preset")
    preset = VOICE_PRESETS.get(preset_id) if isinstance(preset_id, str) else None
    if preset is None:
        fallback_id = _kind_default_preset_id(agent_kind)
        if preset_id is not None:
            logger.warning("unknown voice preset %r; using kind default %r", preset_id, fallback_id)
        preset = VOICE_PRESETS[fallback_id]

    bubble_length = _enum_or_default(voice.get("bubble_length"), _BUBBLE_LENGTHS, preset.bubble_length)
    return ResolvedVoice(
        talking_overrides={
            "emoji_usage": _enum_or_default(voice.get("emoji"), _EMOJI_LEVELS, preset.emoji),
            "max_chars": _BUBBLE_LENGTH_CHARS[bubble_length],
            "max_bubbles": _bubbles_or_default(voice.get("max_bubbles"), preset.max_bubbles),
            "pacing": _enum_or_default(voice.get("pacing"), _PACING_PROFILES, preset.pacing),
        },
        preset_asset_id=preset.asset_id,
        verbosity=_enum_or_default(voice.get("verbosity"), _VERBOSITY_LEVELS, preset.verbosity),
        additional_instructions=_sanitize_additional_instructions(voice.get("additional_instructions")),
    )
