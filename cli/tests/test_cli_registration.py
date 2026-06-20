from __future__ import annotations

import os
from pathlib import Path

import cli.app as appmod
from cli.commands import deploy


def test_top_level_commands_registered():
    names = {c.name for c in appmod.app.registered_commands}
    assert "metrics" in names
    assert "push" in names
    assert "check" in names  # pre-existing, must not regress


def test_push_aliases_deploy_push():
    push_cmd = next(c for c in appmod.app.registered_commands if c.name == "push")
    assert push_cmd.callback is deploy.push


def test_agent_subapp_registered():
    group_names = {g.name for g in appmod.app.registered_groups}
    assert "agent" in group_names


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_pyproject_exposes_cli_extra_and_script():
    text = (_repo_root() / "pyproject.toml").read_text(encoding="utf-8")
    assert 'oqim = "cli.__main__:main"' in text
    assert "[project.optional-dependencies]" in text
    assert "cli = [" in text


def test_vm_wrapper_sources_env_and_execs_venv():
    wrapper = _repo_root() / "scripts" / "oqim"
    assert wrapper.exists(), "scripts/oqim wrapper missing"
    assert os.access(wrapper, os.X_OK), "scripts/oqim must be executable"
    body = wrapper.read_text(encoding="utf-8")
    assert "/etc/oqim/oqim.env" in body
    assert "backend/.venv/bin/oqim" in body
    assert "exec " in body
    assert "set -euo pipefail" in body
    # Must prepend the repo root so the editable `cli` package wins over the
    # stray hermes-agent `cli.py` in site-packages (the console script does not
    # put cwd on sys.path).
    assert "PYTHONPATH" in body


def test_deploy_installs_cli_and_symlinks_wrapper():
    deploy_yml = (_repo_root() / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "-e '.[cli]'" in deploy_yml or '-e ".[cli]"' in deploy_yml
    assert "/usr/local/bin/oqim" in deploy_yml
