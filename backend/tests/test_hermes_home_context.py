"""Run-model X core: per-workspace HERMES_HOME via contextvar (spike #439).

The genuine spike risk: does a contextvar-scoped HERMES_HOME survive Hermes's
asyncio.to_thread worker hop and stay isolated under concurrent multi-tenant
runs? If it leaks, run-model X is no-go. The seller path (no contextvar) must be
byte-identical to the global default.
"""

import asyncio

from app.modules.agent_runtime_v2.hermes.hermes_home_context import use_hermes_home
from app.modules.agent_runtime_v2.hermes.vendor_patches import apply_vendor_patches


def test_seller_path_unchanged_without_contextvar():
    """No contextvar set (the seller plane) -> Hermes's global default, untouched."""
    apply_vendor_patches()
    import hermes_constants

    original = hermes_constants.get_hermes_home.__wrapped_original__()
    assert hermes_constants.get_hermes_home() == original


async def test_contextvar_redirects_get_hermes_home_through_to_thread(tmp_path):
    apply_vendor_patches()
    import hermes_constants

    home = tmp_path / "ws-1"
    with use_hermes_home(home):
        # synchronous (the async caller's thread)
        assert hermes_constants.get_hermes_home() == home
        # through asyncio.to_thread — the exact hop the engine uses for the run
        got = await asyncio.to_thread(hermes_constants.get_hermes_home)
        assert got == home
        # the skill-discovery path resolves per-workspace
        assert await asyncio.to_thread(hermes_constants.get_skills_dir) == home / "skills"

    # contextvar reset on exit -> back to the global default
    assert hermes_constants.get_hermes_home() != home


async def test_concurrent_workspaces_are_isolated(tmp_path):
    """Two workspaces under concurrent runs must never see each other's HERMES_HOME."""
    apply_vendor_patches()
    import hermes_constants

    async def run(ws: str) -> object:
        with use_hermes_home(tmp_path / ws):
            await asyncio.sleep(0.02)  # force interleaving
            return await asyncio.to_thread(hermes_constants.get_hermes_home)

    a, b, c = await asyncio.gather(run("ws-1"), run("ws-2"), run("ws-3"))
    assert a == tmp_path / "ws-1"
    assert b == tmp_path / "ws-2"
    assert c == tmp_path / "ws-3"


async def test_seeded_skill_md_is_discovered_under_workspace_home(tmp_path):
    """End-to-end: a file-drop SKILL.md under the workspace HERMES_HOME is found by
    Hermes's own skill walker, through the contextvar + to_thread hop."""
    apply_vendor_patches()
    import hermes_constants
    from agent.skill_utils import iter_skill_index_files

    home = tmp_path / "ws-7"
    skill_dir = home / "skills" / "intro-videos"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: intro-videos\n"
        "description: Send the intro videos, offer first, when a lead waits on an operator.\n"
        "---\n\n"
        "# Intro videos\n\nOffer first; put the video links underneath.\n",
        encoding="utf-8",
    )

    def _discover() -> list[str]:
        skills_dir = hermes_constants.get_skills_dir()
        return [str(p) for p in iter_skill_index_files(skills_dir, "SKILL.md")]

    with use_hermes_home(home):
        found = await asyncio.to_thread(_discover)

    assert len(found) == 1
    found_path = found[0]
    assert found_path.startswith(str(home))  # resolved to THIS workspace's home
    assert found_path.endswith("intro-videos/SKILL.md")
