"""Pure Markdown renderers for BUSINESS.md / AGENT.md / SKILL.md.

These functions take structured records and produce the rendered document.
They are pure (no IO, no LLM calls) and deterministic. They are the only
correct way to derive the .md surface — never store rendered text as truth.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from app.modules.agent_documents.contracts import RenderedDocument


class _SectionLike(Protocol):
    section_key: str
    title: str
    body: str
    order_index: int


class _AgentLike(Protocol):
    id: int
    name: str
    agent_type: str
    trust_mode: str


class _SkillLike(Protocol):
    id: int
    slug: str
    name: str
    description: str
    instructions: str
    when_to_use: str
    when_not_to_use: str
    tools: list[str]
    examples: list[dict[str, object]]


@dataclass(frozen=True)
class _Header:
    title: str
    subtitle: str | None = None


def _order_sections(sections: Iterable[_SectionLike]) -> list[_SectionLike]:
    return sorted(sections, key=lambda s: (s.order_index, s.section_key))


def _render_header(header: _Header) -> str:
    lines = [f"# {header.title}"]
    if header.subtitle:
        lines.append("")
        lines.append(f"_{header.subtitle}_")
    return "\n".join(lines)


def _render_section(section: _SectionLike) -> str:
    body = section.body.strip()
    if not body:
        body = "_Yetishmayapti._"
    return f"## {section.title}\n\n{body}"


def render_business_md(
    workspace_name: str,
    sections: Iterable[_SectionLike],
) -> RenderedDocument:
    ordered = _order_sections(sections)
    parts: list[str] = [
        _render_header(
            _Header(
                title=f"BUSINESS.md: {workspace_name}",
                subtitle="Workspace-level business truth.",
            )
        )
    ]
    parts.extend(_render_section(section) for section in ordered)
    markdown = "\n\n".join(parts).rstrip() + "\n"
    return RenderedDocument(
        kind="business",
        subject_id=None,
        title=f"BUSINESS.md: {workspace_name}",
        markdown=markdown,
        sections_used=len(ordered),
    )


def render_agent_md(
    agent: _AgentLike,
    sections: Iterable[_SectionLike],
    skills: Iterable[_SkillLike] = (),
) -> RenderedDocument:
    ordered_sections = _order_sections(sections)
    skill_list = list(skills)
    title = f"AGENT.md: {agent.name}"
    parts: list[str] = [
        _render_header(
            _Header(
                title=title,
                subtitle=f"Type: {agent.agent_type} · Trust mode: {agent.trust_mode}",
            )
        )
    ]
    parts.extend(_render_section(section) for section in ordered_sections)

    if skill_list:
        skill_block_lines = ["## Skills"]
        for skill in skill_list:
            line = f"- **{skill.name}** (`{skill.slug}`)"
            if skill.description:
                line = f"{line}: {skill.description.strip()}"
            skill_block_lines.append(line)
        parts.append("\n".join(skill_block_lines))

    markdown = "\n\n".join(parts).rstrip() + "\n"
    return RenderedDocument(
        kind="agent",
        subject_id=agent.id,
        title=title,
        markdown=markdown,
        sections_used=len(ordered_sections),
    )


def render_skill_md(
    skill: _SkillLike,
    sections: Iterable[_SectionLike] = (),
) -> RenderedDocument:
    ordered_sections = _order_sections(sections)
    title = f"SKILL.md: {skill.name}"
    header = _render_header(
        _Header(title=title, subtitle=f"Slug: `{skill.slug}`")
    )

    base_blocks: list[str] = [header]
    if skill.description.strip():
        base_blocks.append(f"## Description\n\n{skill.description.strip()}")
    if skill.when_to_use.strip():
        base_blocks.append(f"## When to use\n\n{skill.when_to_use.strip()}")
    if skill.when_not_to_use.strip():
        base_blocks.append(f"## When not to use\n\n{skill.when_not_to_use.strip()}")
    if skill.instructions.strip():
        base_blocks.append(f"## Instructions\n\n{skill.instructions.strip()}")
    if skill.tools:
        tool_lines = "\n".join(f"- `{tool}`" for tool in skill.tools)
        base_blocks.append(f"## Tools\n\n{tool_lines}")
    if skill.examples:
        example_lines: list[str] = ["## Examples", ""]
        for index, example in enumerate(skill.examples, start=1):
            example_lines.append(f"### Example {index}")
            for key, value in example.items():
                example_lines.append(f"- **{key}**: {value}")
            example_lines.append("")
        base_blocks.append("\n".join(example_lines).rstrip())

    base_blocks.extend(_render_section(section) for section in ordered_sections)

    markdown = "\n\n".join(base_blocks).rstrip() + "\n"
    return RenderedDocument(
        kind="skill",
        subject_id=skill.id,
        title=title,
        markdown=markdown,
        sections_used=len(ordered_sections),
    )
