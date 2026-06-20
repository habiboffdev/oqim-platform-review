"""Prompt visibility: one place that answers "what does the model receive?".

Founder pain (2026-06-11): prompt material is layered across the managed
hermes_reply.md asset, runtime lines, and the rendered AGENT.md — there was
no way to SEE the composed result. The report makes every layer measurable.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.prompt_report import build_prompt_report
from app.modules.agent_runtime_v2.reply_runtime import compose_hermes_system_prompt

_AGENT_MD = (
    "# AGENT.md\n\n"
    "## Role\nSeller agent for the pilot.\n\n"
    "## Behavior rules\nBe warm. One question per message.\n"
)


def _config() -> AgentConfig:
    return AgentConfig(
        agent_id=13,
        workspace_id=1,
        name="Pilot Agent",
        trust_mode="autopilot",
        auto_send_threshold=0.0,
        agent_md=_AGENT_MD,
        agent_kind="seller_agent",
    )


def test_report_layers_account_for_the_whole_composed_prompt():
    report = build_prompt_report(_config())

    layer_names = [layer.name for layer in report.layers]
    assert layer_names == ["hermes_reply.md", "runtime_lines", "agent_md"]
    composed = compose_hermes_system_prompt(_AGENT_MD, "seller_agent")
    assert report.total_chars == len(composed)
    # every layer is measured and hashed
    assert all(layer.chars > 0 for layer in report.layers)
    assert all(len(layer.sha256_12) == 12 for layer in report.layers)


def test_report_breaks_agent_md_down_by_section():
    report = build_prompt_report(_config())

    headings = dict(report.agent_md_sections)
    assert "## Role" in headings
    assert "## Behavior rules" in headings
    assert all(chars > 0 for chars in headings.values())


def test_seed_agent_sections_stay_inside_prompt_budget():
    """AGENT.md is prompt material — guard it like hermes_reply.md (<=10500)."""
    seed_path = (
        Path(__file__).resolve().parents[1]
        / "seed_data"
        / "companies"
        / "biznesni_tizimlashtirish.json"
    )
    package = json.loads(seed_path.read_text())
    total = sum(len(section["body"]) for section in package["agent_sections"])
    assert total <= 10500, f"agent_sections grew to {total} chars — trim before adding"


def test_seed_prose_never_names_tools():
    """One concern, one home: per-call tool steering lives in tool schemas
    (and hermes_reply.md), company flow lives in the seed — tool names in
    seed prose are the drift machine that Task 8 (2026-06-10) had to fix
    in three places at once."""
    import re

    seed_path = (
        Path(__file__).resolve().parents[1]
        / "seed_data"
        / "companies"
        / "biznesni_tizimlashtirish.json"
    )
    package = json.loads(seed_path.read_text())
    tool_re = re.compile(
        r"\b(work\.\w+|owner\.\w+|commerce\.\w+|conversation\.\w+|talk\.\w+|knowledge_\w+)\b"
    )
    offenders = []
    for group in ("agent_sections", "business_sections"):
        for section in package.get(group, []):
            hits = tool_re.findall(section["body"])
            if hits:
                offenders.append((group, section["section_key"], sorted(set(hits))))
    assert not offenders, f"tool names leaked into seed prose: {offenders}"
