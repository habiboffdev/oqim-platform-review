from __future__ import annotations

import cli.remote as remote


def test_remote_argv_strips_prod_flag():
    argv = ["metrics", "--conv", "3", "--prod"]
    assert remote.remote_argv(argv) == ["metrics", "--conv", "3"]
    argv2 = ["--prod", "agent", "tail", "--conv", "3"]
    assert remote.remote_argv(argv2) == ["agent", "tail", "--conv", "3"]


def test_bridge_to_prod_builds_ssh_command(monkeypatch):
    captured = {}

    class _Done:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Done()

    monkeypatch.setattr(remote.subprocess, "run", _fake_run)
    monkeypatch.setenv("OQIM_PROD_SSH", "oqim@example.test")

    code = remote.bridge_to_prod(["metrics", "--conv", "3"])

    assert code == 0
    assert captured["cmd"][0] == "ssh"
    assert "oqim@example.test" in captured["cmd"]
    assert captured["cmd"][-1] == "oqim metrics --conv 3"


def test_bridge_to_prod_propagates_nonzero_exit(monkeypatch):
    class _Fail:
        returncode = 255

    monkeypatch.setattr(remote.subprocess, "run", lambda cmd, **kw: _Fail())
    monkeypatch.setenv("OQIM_PROD_SSH", "oqim@example.test")
    assert remote.bridge_to_prod(["ai", "compact"]) == 255


def test_bridge_to_prod_quotes_metacharacters(monkeypatch):
    captured = {}

    class _Done:
        returncode = 0

    monkeypatch.setattr(remote.subprocess, "run",
                        lambda cmd, **kw: captured.__setitem__("cmd", cmd) or _Done())
    monkeypatch.setenv("OQIM_PROD_SSH", "oqim@example.test")

    remote.bridge_to_prod(["agent", "sim", "--conv", "3", "narx; rm -rf /"])

    remote_cmd = captured["cmd"][-1]
    # the dangerous token is quoted as a single literal, not left bare
    assert "'narx; rm -rf /'" in remote_cmd
    assert remote_cmd.startswith("oqim agent sim --conv 3 ")
