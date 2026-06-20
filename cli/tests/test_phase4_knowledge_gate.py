from __future__ import annotations

from types import SimpleNamespace

from cli.commands import test_cmd


def test_phase4_knowledge_gate_runs_focused_backend_proofs(monkeypatch) -> None:
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="49 passed in 1.23s", stderr="")

    monkeypatch.setattr(test_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(test_cmd, "_venv_python", lambda directory: "/tmp/python")

    result = test_cmd._run_phase4_knowledge_local_proofs()

    assert result["passed"] is True
    assert result["summary"] == "1/1 proof groups passed"
    assert captured["args"] == [
        "/tmp/python",
        "-m",
        "pytest",
        *test_cmd.PHASE4_KNOWLEDGE_BACKEND_PROOF_PATHS,
        "-q",
        "--no-cov",
    ]
    assert result["checks"][0]["proof_paths"] == [
        "tests/test_phase4_knowledge_mcp_agent_control.py",
        "tests/test_hermes_oqim_tools.py",
    ]


def test_phase4_knowledge_gate_fails_when_backend_proofs_fail(monkeypatch) -> None:
    def fake_run(_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="1 failed")

    monkeypatch.setattr(test_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(test_cmd, "_venv_python", lambda directory: "/tmp/python")

    result = test_cmd._run_phase4_knowledge_local_proofs()

    assert result["passed"] is False
    assert result["checks"][0]["status"] == "fail"
    assert "1 failed" in result["output"]
