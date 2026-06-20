from cli.commands.test_cmd import (
    _local_reality_steps,
    _pilot_gate_steps,
    _run_local_reality,
    _run_pilot_gate,
)


def test_pilot_gate_dry_run_lists_required_harnesses() -> None:
    report = _run_pilot_gate(workspaces=123, dry_run=True, reply_workspace=7)

    assert report["passed"] is False
    assert report["dry_run"] is True
    assert report["summary"] == "21 planned checks"
    commands = [" ".join(step["command"]) for step in report["steps"]]
    assert "test runtime-zero --reset --yes --cleanup-sidecar --json" in commands
    assert "test harness-parallel --json" in commands
    assert "test app-smoke --json" in commands
    assert "test telegram-intake --browser --json" in commands
    assert "test tenants --workspaces 123 --json" in commands
    assert "test adapter-contract --json" in commands
    assert "eval replies --workspace 7 --concurrency 2 --max-p95-ms 45000 --json" in commands
    assert "eval sales --json" in commands
    assert "eval retrieval-core --max-p95-ms 5000 --json" in commands
    assert "eval retrieval-core --live-rerank-provider --max-p95-ms 5000 --json" in commands
    assert "eval company-brain --max-p95-ms 10000 --json" in commands
    assert (
        "eval company-brain --live --semantic --contextual-source-units --max-p95-ms 60000 --json"
        in commands
    )
    assert "eval buyer-intent --repetitions 3 --concurrency 5 --max-p95-ms 5000 --json" in commands
    retired_command = "eval " + "auto" + "crm-extraction --max-p95-ms 5000 --json"
    assert retired_command not in commands
    assert "audit runtime --json" in commands


def test_pilot_gate_steps_end_with_runtime_audit() -> None:
    steps = _pilot_gate_steps(workspaces=1000)
    commands = [" ".join(step["command"]) for step in steps]

    assert steps[0]["name"] == "runtime-zero"
    assert steps[1]["name"] == "harness-parallel"
    assert "eval replies --seed-workspace --concurrency 2 --max-p95-ms 45000 --json" in commands
    assert "eval retrieval-core --max-p95-ms 5000 --json" in commands
    assert "eval retrieval-core --live-rerank-provider --max-p95-ms 5000 --json" in commands
    assert "eval company-brain --max-p95-ms 10000 --json" in commands
    assert (
        "eval company-brain --live --semantic --contextual-source-units --max-p95-ms 60000 --json"
        in commands
    )
    assert "eval buyer-intent --repetitions 3 --concurrency 5 --max-p95-ms 5000 --json" in commands
    retired_command = "eval " + "auto" + "crm-extraction --max-p95-ms 5000 --json"
    assert retired_command not in commands
    assert steps[-1]["name"] == "runtime-audit"


def test_local_reality_dry_run_lists_required_checks() -> None:
    report = _run_local_reality(dry_run=True)

    assert report["passed"] is False
    assert report["dry_run"] is True
    assert report["summary"] == "6 planned checks"
    names = [step["name"] for step in report["steps"]]
    assert names == [
        "dependency-truth",
        "api-capability",
        "reconnect",
        "browser-cache-reset",
        "app-smoke",
        "telegram-intake",
    ]


def test_cli_harness_purposes_use_reply_language() -> None:
    pilot_steps = _pilot_gate_steps(workspaces=1000)
    local_steps = _local_reality_steps(skip_browser=False)
    combined = " ".join(
        str(step.get("purpose", ""))
        for step in [*pilot_steps, *local_steps]
    ).lower()

    assert "draft" not in combined
    assert "reply" in combined


def test_local_reality_skip_browser_omits_playwright_checks() -> None:
    steps = _local_reality_steps(skip_browser=True)

    assert [step["name"] for step in steps] == [
        "dependency-truth",
        "api-capability",
        "reconnect",
    ]
