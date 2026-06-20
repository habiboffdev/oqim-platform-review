from __future__ import annotations

import re
from pathlib import Path


TEST_COMMAND = Path(__file__).resolve().parents[1] / "commands" / "test_cmd.py"
BACKEND_TESTS = Path(__file__).resolve().parents[2] / "backend"


def test_api_capability_smoke_uses_active_seller_dashboard() -> None:
    source = TEST_COMMAND.read_text(encoding="utf-8")

    assert '"/api/bi-promoter/analytics/dashboard"' in source
    assert '"bi_analytics_dashboard.v1"' in source
    assert '"/api/' + 'dashboard"' not in source
    assert '"dashboard_' + 'pipeline"' not in source


def test_phase3_gateway_gate_tracks_required_live_evidence() -> None:
    source = TEST_COMMAND.read_text(encoding="utf-8")

    assert '@app.command(name="phase3-gateway")' in source
    assert '"phase3_complete"' in source
    assert '"local_only"' in source
    assert "workspace_id: Optional[int] = typer.Option(" in source
    assert "Resolve the Phase 3 live workspace from connected Telegram state." in source
    assert "workspace_id=resolved_workspace_id" in source
    assert "--live-wait-seconds" in source
    assert "_run_phase3_gateway_gate_until" in source
    assert '"live_capture_plan"' in source
    assert "_phase3_gateway_live_capture_plan" in source
    assert "--approve-live-reply" in source
    assert "--approve-live-marker" in source
    assert "position(lower(:marker) in lower(tm.content)) > 0" in source
    assert "hr.details ? 'trigger_telemetry'" in source
    assert 'client_idempotency_key=f"phase3-live-approve:{{reply.id}}"' in source
    assert "tests/test_runtime_profile_compiler.py" in source
    assert "tests/test_runtime_profile_eval.py" in source
    assert "tests/test_replay_harness.py::test_replay_rebuilds_projection_idempotently_and_workspace_scoped" in source
    assert "tests/test_message_runtime_reliability.py" not in source
    assert "tests/test_runtime_profile_snapshot.py" not in source
    assert "live_operator_talk_bundle_delivery" in source
    assert "live_10_message_trigger_start_p50" in source
    assert "live_restart_long_idle" in source
    assert "live_profile_trigger_modes" in source
    assert "live_multi_workspace_flood_wait_isolation" in source
    assert "telegram_trigger_start_under_1s_status" in source


def test_test_command_backend_pytest_paths_exist() -> None:
    source = TEST_COMMAND.read_text(encoding="utf-8")
    paths = sorted(
        set(re.findall(r"tests/[A-Za-z0-9_./:-]+\.py(?:::[A-Za-z0-9_]+)?", source))
    )
    missing = [
        path
        for path in paths
        if not (BACKEND_TESTS / path.split("::", 1)[0]).exists()
    ]

    assert missing == []
