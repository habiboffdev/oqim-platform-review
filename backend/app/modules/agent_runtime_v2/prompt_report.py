"""Prompt visibility report: what the Hermes model actually receives.

Pure — no DB, no I/O. The caller loads ``AgentConfig`` (config_loader) and
this module measures the composed system prompt layer by layer, so "what is
in the prompt?" is answerable with one command instead of archaeology.

Layers (matching ``compose_hermes_system_prompt``):
  1. hermes_reply.md  — the managed runtime prompt asset (registry-governed)
  2. runtime_lines    — agent kind + emoji guidance lines
  3. agent_md         — the rendered AGENT.md document (DB sections + skills)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.reply_runtime import (
    ManagedRuntimePrompt,
    compose_hermes_system_prompt,
    load_hermes_reply_prompt,
)


@dataclass(frozen=True)
class PromptLayer:
    name: str
    chars: int
    sha256_12: str
    source: str


@dataclass(frozen=True)
class PromptReport:
    workspace_id: int
    agent_id: int
    agent_kind: str
    layers: list[PromptLayer]
    total_chars: int
    composed_sha256_12: str
    agent_md_sections: list[tuple[str, int]] = field(default_factory=list)


def build_prompt_report(
    config: AgentConfig,
    *,
    prompt: ManagedRuntimePrompt | None = None,
    emoji_usage: str = "medium",
) -> PromptReport:
    managed = prompt or load_hermes_reply_prompt()
    composed = compose_hermes_system_prompt(
        config.agent_md,
        config.agent_kind,
        prompt=managed,
        emoji_usage=emoji_usage,
    )
    agent_md_block = f"Rendered AGENT.md:\n{config.agent_md}".strip()
    # runtime lines = everything the composer adds between the managed prompt
    # and the AGENT.md block (agent kind + emoji guidance + joins)
    runtime_chars = len(composed) - len(managed.body.strip()) - len(agent_md_block)
    runtime_lines = composed[
        len(managed.body.strip()) : len(composed) - len(agent_md_block)
    ].strip()
    layers = [
        PromptLayer(
            name="hermes_reply.md",
            chars=len(managed.body.strip()),
            sha256_12=_sha12(managed.body.strip()),
            source=f"{managed.prompt_id}@{managed.version} (cache: {managed.cache_policy})",
        ),
        PromptLayer(
            name="runtime_lines",
            chars=max(runtime_chars, len(runtime_lines)),
            sha256_12=_sha12(runtime_lines),
            source="compose_hermes_system_prompt (agent kind + emoji guidance)",
        ),
        PromptLayer(
            name="agent_md",
            chars=len(agent_md_block),
            sha256_12=_sha12(agent_md_block),
            source="agent_documents DB sections + skills (render_agent_md)",
        ),
    ]
    return PromptReport(
        workspace_id=config.workspace_id,
        agent_id=config.agent_id,
        agent_kind=config.agent_kind,
        layers=layers,
        total_chars=len(composed),
        composed_sha256_12=_sha12(composed),
        agent_md_sections=_section_breakdown(config.agent_md),
    )


def _section_breakdown(agent_md: str) -> list[tuple[str, int]]:
    """Char count per ``## `` section of the rendered AGENT.md."""
    sections: list[tuple[str, int]] = []
    current_heading: str | None = None
    current_chars = 0
    for line in (agent_md or "").splitlines():
        if line.startswith("## "):
            if current_heading is not None:
                sections.append((current_heading, current_chars))
            current_heading = line.strip()
            current_chars = 0
        elif current_heading is not None:
            current_chars += len(line) + 1
    if current_heading is not None:
        sections.append((current_heading, current_chars))
    return sections


def _sha12(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]
