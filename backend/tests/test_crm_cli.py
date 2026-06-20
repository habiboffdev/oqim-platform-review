"""oqim crm CLI: the root --json flag must reach the crm subcommands."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# typer ships with the oqim CLI (root pyproject `.[cli]` extra), which is NOT
# installed in the minimal backend proof-gate env — skip there, run where the CLI
# deps exist (local dev + the prod backend venv).
pytest.importorskip("typer")

# The CLI package lives at the REPO ROOT (not under backend/). Put it FIRST on the
# path so our `cli/` package wins over hermes-agent's shadowing site-packages
# `cli.py` (the same shadowing the prod `oqim` wrapper guards against).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cli.agentio as agentio
from cli.app import app
from typer.testing import CliRunner

runner = CliRunner()


class _FakeSession:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        return False


def _no_db(monkeypatch):
    """Avoid a real DB: yield a dummy session, resolve no active connection."""
    monkeypatch.setattr("app.db.session.async_session", lambda: _FakeSession())
    monkeypatch.setattr(
        "cli.commands.crm_cmd._active_connection", AsyncMock(return_value=None)
    )


def test_root_json_flag_reaches_crm_schema(monkeypatch):
    # the documented agent-surface form: `oqim --json crm schema` (root flag first).
    monkeypatch.setattr(agentio, "OUTPUT_JSON", False)  # auto-restored at teardown
    _no_db(monkeypatch)
    result = runner.invoke(app, ["--json", "crm", "schema", "-w", "999"])
    assert result.exit_code == 1                       # no active connection
    payload = json.loads(result.stdout)                # MUST be JSON, not terse text
    assert payload == {"workspace": 999, "error": "no_active_connection"}


def test_root_json_flag_reaches_crm_rediscover(monkeypatch):
    monkeypatch.setattr(agentio, "OUTPUT_JSON", False)
    _no_db(monkeypatch)
    result = runner.invoke(app, ["--json", "crm", "rediscover", "-w", "999"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload == {"workspace": 999, "error": "no_active_connection"}


def test_subcommand_json_flag_still_works(monkeypatch):
    # the per-command `--json` (after the subcommand) must keep working too.
    monkeypatch.setattr(agentio, "OUTPUT_JSON", False)
    _no_db(monkeypatch)
    result = runner.invoke(app, ["crm", "schema", "-w", "999", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"workspace": 999, "error": "no_active_connection"}


def test_crm_route_subcommands_registered():
    from cli.commands import crm_cmd

    route = next((g for g in crm_cmd.app.registered_groups if g.name == "route"), None)
    assert route is not None
    names = {c.name for c in route.typer_instance.registered_commands}
    assert {"show", "set"} <= names


def test_crm_route_set_rejects_empty_map(monkeypatch):
    monkeypatch.setattr(agentio, "OUTPUT_JSON", False)
    result = runner.invoke(app, ["--json", "crm", "route", "set", "-w", "1", "-a", "3",
                                 "--map", "bad-no-equals"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "empty_or_malformed_map"


def test_crm_route_set_rejects_default_not_in_map(monkeypatch):
    monkeypatch.setattr(agentio, "OUTPUT_JSON", False)
    result = runner.invoke(app, ["--json", "crm", "route", "set", "-w", "1", "-a", "3",
                                 "--map", "sales=111", "--default", "consulting"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "default_not_in_map"


# --------------------------------------------------------------------------- #
# S4: oqim crm fields show/set + tags set
# --------------------------------------------------------------------------- #
class _FakeAgent:
    def __init__(self, workspace_id=1, channel_config=None):
        self.workspace_id = workspace_id
        self.channel_config = channel_config


class _FakeConn:
    def __init__(self, pipeline_config):
        self.pipeline_config = pipeline_config


class _DBWithAgent:
    """A fake session whose ``get`` returns a fixed agent (for _agent_in_workspace)
    and that records a commit (so the CLI's channel_config write is observable)."""
    def __init__(self, agent):
        self._agent = agent
        self.committed = False

    async def get(self, _model, _pk):
        return self._agent

    async def commit(self):
        self.committed = True


class _SessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


def _snapshot_conn(field_ids, entity="leads"):
    return _FakeConn({
        "snapshot": {"custom_fields": {
            entity: [{"id": fid, "name": f"F{fid}", "type": "numeric"} for fid in field_ids]
        }}
    })


def test_crm_fields_subcommands_registered():
    from cli.commands import crm_cmd

    fields = next((g for g in crm_cmd.app.registered_groups if g.name == "fields"), None)
    assert fields is not None
    names = {c.name for c in fields.typer_instance.registered_commands}
    assert {"show", "set"} <= names
    tags = next((g for g in crm_cmd.app.registered_groups if g.name == "tags"), None)
    assert tags is not None
    assert "set" in {c.name for c in tags.typer_instance.registered_commands}
    dnc = next((g for g in crm_cmd.app.registered_groups if g.name == "dnc"), None)
    assert dnc is not None
    assert "set" in {c.name for c in dnc.typer_instance.registered_commands}


def test_crm_fields_set_writes_channel_config(monkeypatch):
    monkeypatch.setattr(agentio, "OUTPUT_JSON", False)
    agent = _FakeAgent(workspace_id=1, channel_config={})
    db = _DBWithAgent(agent)
    monkeypatch.setattr("app.db.session.async_session", lambda: _SessionCtx(db))
    monkeypatch.setattr(
        "cli.commands.crm_cmd._active_connection",
        AsyncMock(return_value=_snapshot_conn(["600123"])),
    )
    result = runner.invoke(app, ["--json", "crm", "fields", "set", "-w", "1", "-a", "3",
                                 "--key", "budget", "--field-id", "600123",
                                 "--label", "Budjet", "--type", "numeric",
                                 "--inject", "--write"])
    assert result.exit_code == 0
    entry = agent.channel_config["crm"]["fields"]["budget"]
    assert entry == {"field_id": "600123", "label": "Budjet", "type": "numeric",
                     "entity": "lead", "inject": True, "write": True}
    assert db.committed is True


def test_crm_fields_set_rejects_unknown_field_id(monkeypatch):
    monkeypatch.setattr(agentio, "OUTPUT_JSON", False)
    agent = _FakeAgent(workspace_id=1, channel_config={})
    db = _DBWithAgent(agent)
    monkeypatch.setattr("app.db.session.async_session", lambda: _SessionCtx(db))
    monkeypatch.setattr(
        "cli.commands.crm_cmd._active_connection",
        AsyncMock(return_value=_snapshot_conn(["600123"])),  # 600999 NOT present
    )
    result = runner.invoke(app, ["--json", "crm", "fields", "set", "-w", "1", "-a", "3",
                                 "--key", "x", "--field-id", "600999"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "unknown_field_id"
    assert agent.channel_config == {}  # never written


def test_crm_tags_set_writes_channel_config(monkeypatch):
    monkeypatch.setattr(agentio, "OUTPUT_JSON", False)
    agent = _FakeAgent(workspace_id=1, channel_config={})
    db = _DBWithAgent(agent)
    monkeypatch.setattr("app.db.session.async_session", lambda: _SessionCtx(db))
    result = runner.invoke(app, ["--json", "crm", "tags", "set", "-w", "1", "-a", "3",
                                 "--vocabulary", "vip,hot", "--namespace", "oqim:"])
    assert result.exit_code == 0
    assert agent.channel_config["crm"]["tags"] == {"vocabulary": ["vip", "hot"],
                                                   "namespace": "oqim:"}
    assert db.committed is True


def test_crm_dnc_set_writes_channel_config(monkeypatch):
    monkeypatch.setattr(agentio, "OUTPUT_JSON", False)
    agent = _FakeAgent(workspace_id=1, channel_config={})
    db = _DBWithAgent(agent)
    monkeypatch.setattr("app.db.session.async_session", lambda: _SessionCtx(db))
    monkeypatch.setattr(
        "cli.commands.crm_cmd._active_connection",
        AsyncMock(return_value=_snapshot_conn(["600126"], entity="contacts")),
    )
    result = runner.invoke(app, ["--json", "crm", "dnc", "set", "-w", "1", "-a", "3",
                                 "--field-id", "600126", "--on-value", "true"])
    assert result.exit_code == 0
    assert agent.channel_config["crm"]["do_not_contact"] == {
        "field_id": "600126", "on_value": True, "entity": "contact", "label": ""}
    assert db.committed is True


def test_crm_dnc_set_rejects_unknown_field_id(monkeypatch):
    monkeypatch.setattr(agentio, "OUTPUT_JSON", False)
    agent = _FakeAgent(workspace_id=1, channel_config={})
    db = _DBWithAgent(agent)
    monkeypatch.setattr("app.db.session.async_session", lambda: _SessionCtx(db))
    monkeypatch.setattr(
        "cli.commands.crm_cmd._active_connection",
        AsyncMock(return_value=_snapshot_conn(["600126"])),  # 600999 NOT present
    )
    result = runner.invoke(app, ["--json", "crm", "dnc", "set", "-w", "1", "-a", "3",
                                 "--field-id", "600999"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "unknown_field_id"
    assert agent.channel_config == {}  # never written
