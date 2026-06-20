"""Per-workspace seller playbook override.

Owner request 2026-06-11: the managed `seller_playbook.md` is the DEFAULT
selling method for every seller agent, but a workspace can OVERRIDE it with a
business-specific playbook (the pilot encodes its real HR-course sales method).
Other workspaces keep the default and can override the same way.

Storage reuses `agent_document_sections` with document_kind="playbook",
subject_type="workspace" — no new table, no migration.
"""

from __future__ import annotations

import pytest

from app.modules.agent_documents.contracts import AgentDocumentSectionInput
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.agent_runtime_v2.config_loader import AgentConfigLoader
from app.modules.agent_runtime_v2.reply_runtime import (
    SELLER_PLAYBOOK_DOCUMENT_KIND,
    SELLER_PLAYBOOK_SECTION_KEY,
    compose_hermes_system_prompt,
    load_workspace_seller_playbook,
)

# asyncio_mode=auto (pytest.ini) runs the async tests; the sync compose tests
# stay sync, so no module-level asyncio marker (it would warn on them).


# ---- contract: 'playbook' is a workspace-scoped document kind --------------


def test_playbook_section_input_requires_workspace_subject():
    ok = AgentDocumentSectionInput(
        document_kind="playbook",
        subject_type="workspace",
        section_key="seller_playbook",
        title="Seller playbook",
        body="<seller_playbook>...</seller_playbook>",
    )
    assert ok.document_kind == "playbook"
    assert ok.subject_id is None

    with pytest.raises(ValueError):
        AgentDocumentSectionInput(
            document_kind="playbook",
            subject_type="agent",
            subject_id=1,
            section_key="seller_playbook",
            title="x",
            body="y",
        )


# ---- composition: override wins for seller kinds, default otherwise --------


def test_compose_uses_workspace_override_for_seller_kind():
    override = "<seller_playbook>HR kursi maxsus uslubi.</seller_playbook>"
    prompt = compose_hermes_system_prompt(
        "# A\nSotuvchi.",
        "seller_agent",
        seller_playbook_override=override,
    )
    assert "HR kursi maxsus uslubi." in prompt
    # the generic default must NOT also be present
    assert "Discovery first" not in prompt
    # still wrapped/ordered as a playbook block between contract and AGENT.md
    assert prompt.index("<seller_playbook>") < prompt.index("Rendered AGENT.md:")


def test_compose_falls_back_to_default_when_no_override():
    prompt = compose_hermes_system_prompt("# A\nSotuvchi.", "seller_agent")
    assert "Discovery first" in prompt  # the managed default


def test_compose_ignores_override_for_non_seller_kind():
    override = "<seller_playbook>HR kursi maxsus uslubi.</seller_playbook>"
    prompt = compose_hermes_system_prompt(
        "# A\nSupport.",
        "support_agent",
        seller_playbook_override=override,
    )
    assert "<seller_playbook>" not in prompt
    assert "HR kursi maxsus uslubi." not in prompt


def test_blank_override_falls_back_to_default():
    prompt = compose_hermes_system_prompt(
        "# A\nSotuvchi.", "seller_agent", seller_playbook_override="   "
    )
    assert "Discovery first" in prompt


# ---- loader: reads the workspace playbook section --------------------------


async def _upsert_playbook(db_session, workspace, body: str) -> None:
    await AgentDocumentService(db_session).upsert_section(
        workspace_id=workspace.id,
        payload=AgentDocumentSectionInput(
            document_kind=SELLER_PLAYBOOK_DOCUMENT_KIND,
            subject_type="workspace",
            section_key=SELLER_PLAYBOOK_SECTION_KEY,
            title="Seller playbook",
            body=body,
            generated_by="owner",
        ),
    )
    await db_session.flush()


async def test_load_workspace_seller_playbook_returns_body(db_session, workspace):
    await _upsert_playbook(db_session, workspace, "<seller_playbook>HR.</seller_playbook>")
    body = await load_workspace_seller_playbook(db_session, workspace_id=workspace.id)
    assert body == "<seller_playbook>HR.</seller_playbook>"


async def test_load_workspace_seller_playbook_none_when_absent(db_session, workspace):
    body = await load_workspace_seller_playbook(db_session, workspace_id=workspace.id)
    assert body is None


async def test_agent_config_carries_workspace_playbook_override(
    db_session, workspace, agent
):
    await _upsert_playbook(
        db_session, workspace, "<seller_playbook>HR kursi maxsus.</seller_playbook>"
    )
    config = await AgentConfigLoader(db_session).load(
        workspace_id=workspace.id, agent_id=agent.id
    )
    assert config.seller_playbook_override == "<seller_playbook>HR kursi maxsus.</seller_playbook>"


async def test_agent_config_override_none_when_no_playbook(db_session, workspace, agent):
    config = await AgentConfigLoader(db_session).load(
        workspace_id=workspace.id, agent_id=agent.id
    )
    assert config.seller_playbook_override is None


# ---- pilot seed: the HR playbook content is sane ---------------------------


def test_pilot_seed_carries_hr_playbook_within_budget():
    """The pilot company's seed ships an HR-course playbook (not the generic
    default) that names the HR course, never the dropped 'A-class' label, and
    stays under a sane budget so it caches cheaply alongside AGENT.md."""
    import json
    from pathlib import Path

    seed_path = (
        Path(__file__).resolve().parents[1]
        / "seed_data"
        / "companies"
        / "biznesni_tizimlashtirish.json"
    )
    package = json.loads(seed_path.read_text(encoding="utf-8"))
    body = package.get("seller_playbook") or ""

    assert body.strip().startswith("<seller_playbook>")
    assert body.strip().endswith("</seller_playbook>")
    assert "HR" in body
    assert "A-class" not in body and "A-Class" not in body
    # the prompt must never DEMONSTRATE an em-dash — flash-lite imitates the
    # form, so a playbook full of "—" teaches the model to use them (the live
    # leak found 2026-06-11). The rule says no em-dashes; the content must obey.
    assert "—" not in body, "pilot playbook demonstrates an em-dash"
    # budget: richer than the generic default but still small enough to cache
    assert len(body) <= 4000, f"pilot playbook grew: {len(body)} chars"


def test_managed_prompts_do_not_demonstrate_em_dashes():
    """hermes_reply.md and seller_playbook.md ban em-dashes; they must not use
    one themselves (the model imitates the prompt's form, not just its rules)."""
    from app.modules.agent_runtime_v2.reply_runtime import (
        load_hermes_reply_prompt,
        load_seller_playbook_prompt,
    )

    assert "—" not in load_hermes_reply_prompt().body
    assert "—" not in load_seller_playbook_prompt().body


def test_composed_system_prompt_has_no_em_dash_in_any_layer():
    """Every layer the model sees must be em-dash-free, not just the .md files:
    the live leak 2026-06-11 came from the emoji-guidance line and the AGENT.md
    title, which compose() injects around the assets."""
    from app.modules.agent_runtime_v2.reply_runtime import _EMOJI_USAGE_GUIDANCE

    for line in _EMOJI_USAGE_GUIDANCE.values():
        assert "—" not in line
    for usage in ("low", "medium", "high"):
        prompt = compose_hermes_system_prompt(
            "# AGENT.md: Test\nSotuvchi.", "seller_agent", emoji_usage=usage
        )
        assert "—" not in prompt


def test_rendered_agent_md_title_has_no_em_dash():
    from types import SimpleNamespace

    from app.modules.agent_documents.renderer import render_agent_md

    agent = SimpleNamespace(
        id=1, name="Test Agent", agent_type="seller", trust_mode="autopilot"
    )
    out = render_agent_md(agent, [], [])
    assert "—" not in out.markdown
