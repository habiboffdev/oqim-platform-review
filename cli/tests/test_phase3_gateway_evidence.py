from __future__ import annotations

from cli.commands.test_cmd import (
    _phase3_gateway_evidence_check,
    _phase3_gateway_live_capture_plan,
    _phase3_gateway_live_approval_option_error,
    _phase3_gateway_live_next_actions,
    _phase3_gateway_multi_workspace_check_from_payload,
    _phase3_gateway_operator_check_from_payload,
    _phase3_gateway_result_from_parts,
    _phase3_gateway_profile_trigger_check_from_payload,
    _phase3_gateway_restart_long_idle_check_from_payload,
    _phase3_gateway_trigger_start_check_from_payload,
)


def test_phase3_live_evidence_requires_artifacts_before_passing() -> None:
    gate = {
        "name": "live_operator_talk_bundle_delivery",
        "purpose": "prove delivery",
    }

    result = _phase3_gateway_evidence_check(
        gate,
        {"live_operator_talk_bundle_delivery": {"passed": True}},
    )

    assert result["passed"] is False
    assert result["status"] == "missing_artifact"


def test_phase3_live_evidence_passes_with_recorded_artifacts() -> None:
    gate = {
        "name": "live_operator_talk_bundle_delivery",
        "purpose": "prove delivery",
    }
    entry = {
        "passed": True,
        "recorded_at": "2026-05-30T02:30:00+05:00",
        "evidence": {
            "trace": "HermesRun 123",
            "telegram_delivery": "confirmed bubble",
        },
    }

    result = _phase3_gateway_evidence_check(
        gate,
        {"live_operator_talk_bundle_delivery": entry},
    )

    assert result["passed"] is True
    assert result["status"] == "pass"
    assert result["evidence"] == entry["evidence"]


def test_phase3_trigger_start_live_check_reports_runtime_diagnostics() -> None:
    payload = {
        "workspace_id": 3,
        "slo": {
            "telegram_trigger_start_under_1s_status": "unmeasured",
            "telegram_trigger_start_p50_ms": None,
            "telegram_trigger_start_sample_count": 0,
            "telegram_trigger_start_required_sample_count": 10,
        },
        "phase3_gateway_diagnostics": {
            "telegram_runs": 45,
            "telegram_runs_with_trigger_telemetry": 0,
            "latest_telegram_run_created_at": "2026-05-29T20:55:00+00:00",
            "latest_trigger_telemetry_run_created_at": None,
            "sidecar": {
                "state": "connected",
                "lastInboundHotPathSource": "history_sync",
                "lastLiveInboundHotPathAt": None,
            },
        },
    }

    result = _phase3_gateway_trigger_start_check_from_payload(
        payload,
        workspace_id=None,
        period_days=7,
    )

    assert result["passed"] is False
    assert result["status"] == "unmeasured"
    assert "telegram_runs=45" in result["detail"]
    assert "trigger_telemetry_runs=0" in result["detail"]
    assert "sidecar_live_at=None" in result["detail"]
    assert result["data"]["diagnostics"] == payload["phase3_gateway_diagnostics"]


def test_phase3_operator_live_check_passes_with_presence_bundle_and_delivery() -> None:
    evidence = {
        "workspace_id": 3,
        "operator_run": {
            "run_id": "hermes_run:live",
            "run_created_at": "2026-05-30T03:10:00+05:00",
            "has_trigger_telemetry": True,
            "presence_state": "ok",
            "presence_payload": {"online": True, "read": True, "typing": True},
            "talk_bundle_state": "queued",
            "talk_bundle_action_count": 2,
            "output_ref": "seller_agent_reply:777",
            "reply_status": "sent",
            "reply_sent_at": "2026-05-30T03:10:04+05:00",
            "delivery_state": "confirmed",
            "external_message_id": "tg:777",
        },
    }

    result = _phase3_gateway_operator_check_from_payload(evidence, period_days=7)

    assert result["passed"] is True
    assert result["status"] == "pass"
    assert "run=hermes_run:live" in result["detail"]
    assert result["data"]["operator_run"] == evidence["operator_run"]


def test_phase3_operator_live_check_accepts_split_reply_delivery_runtime() -> None:
    evidence = {
        "workspace_id": 3,
        "operator_run": {
            "run_id": "hermes_run:split",
            "run_created_at": "2026-05-30T03:10:00+05:00",
            "has_trigger_telemetry": True,
            "presence_state": "ok",
            "presence_payload": {"online": True, "read": True, "typing": True},
            "talk_bundle_state": "queued",
            "talk_bundle_action_count": 2,
            "output_ref": "seller_agent_reply:777",
            "reply_status": "sent",
            "reply_sent_at": "2026-05-30T03:10:04+05:00",
            "reply_message_id": None,
            "delivery_state": "confirmed",
            "external_message_id": None,
            "telegram_message_id": None,
            "delivery_external_message_id": "1401",
            "delivery_message_id": 8758,
        },
    }

    result = _phase3_gateway_operator_check_from_payload(evidence, period_days=7)

    assert result["passed"] is True
    assert result["status"] == "pass"


def test_phase3_operator_live_check_fails_without_live_trigger_telemetry() -> None:
    evidence = {
        "workspace_id": 3,
        "operator_run": {
            "run_id": "hermes_run:history",
            "has_trigger_telemetry": False,
            "presence_state": "ok",
            "presence_payload": {"online": True, "read": True, "typing": True},
            "talk_bundle_state": "queued",
            "talk_bundle_action_count": 1,
            "reply_status": "sent",
            "delivery_state": "confirmed",
            "external_message_id": "tg:history",
        },
    }

    result = _phase3_gateway_operator_check_from_payload(evidence, period_days=7)

    assert result["passed"] is False
    assert result["status"] == "missing_live_telemetry"
    assert "live trigger telemetry missing" in result["detail"]


def test_phase3_restart_long_idle_passes_when_live_precedes_catchup_completion() -> None:
    payload = {
        "workspace_id": 3,
        "sidecar": {
            "state": "connected",
            "handlersRegisteredAt": "2026-05-30T03:00:00.000Z",
            "catchUpScheduledAt": "2026-05-30T03:00:01.000Z",
            "catchUpStartedAt": "2026-05-30T03:00:02.000Z",
            "lastLiveInboundHotPathAt": "2026-05-30T03:00:03.000Z",
            "lastLiveInboundHotPathLatencyMs": 55,
            "lastCatchUpAt": "2026-05-30T03:00:05.000Z",
            "lastInboundHotPathSource": "live",
        },
    }

    result = _phase3_gateway_restart_long_idle_check_from_payload(payload)

    assert result["passed"] is True
    assert result["status"] == "pass"
    assert "live_at=2026-05-30T03:00:03.000Z" in result["detail"]
    assert result["data"] == payload


def test_phase3_restart_long_idle_accepts_fast_live_handler_after_catchup() -> None:
    payload = {
        "workspace_id": 3,
        "sidecar": {
            "state": "connected",
            "handlersRegisteredAt": "2026-05-30T03:00:00.000Z",
            "catchUpScheduledAt": "2026-05-30T03:00:01.000Z",
            "catchUpStartedAt": "2026-05-30T03:00:02.000Z",
            "lastCatchUpAt": "2026-05-30T03:00:05.000Z",
            "lastLiveInboundHotPathAt": "2026-05-30T03:00:08.000Z",
            "lastLiveInboundHotPathLatencyMs": 55,
            "lastInboundHotPathSource": "live",
        },
    }

    result = _phase3_gateway_restart_long_idle_check_from_payload(payload)

    assert result["passed"] is True
    assert result["status"] == "pass"


def test_phase3_restart_long_idle_requires_true_live_update() -> None:
    payload = {
        "workspace_id": 3,
        "sidecar": {
            "state": "connected",
            "handlersRegisteredAt": "2026-05-30T03:00:00.000Z",
            "catchUpScheduledAt": "2026-05-30T03:00:01.000Z",
            "lastInboundHotPathSource": "history_sync",
            "lastLiveInboundHotPathAt": None,
        },
    }

    result = _phase3_gateway_restart_long_idle_check_from_payload(payload)

    assert result["passed"] is False
    assert result["status"] == "missing_live_update"


def test_phase3_profile_trigger_check_passes_with_all_profiles() -> None:
    payload = {
        "workspace_id": 3,
        "profiles": {
            "reply": {
                "run_id": "hermes_run:reply",
                "run_mode": "reply",
                "agent_kind": "seller",
                "trigger_type": "telegram_message",
                "has_trigger_telemetry": True,
            },
            "personal": {
                "run_id": "hermes_run:personal",
                "run_mode": "personal",
                "agent_kind": "personal",
                "trigger_type": "generic_trigger",
            },
            "broadcast": {
                "run_id": "hermes_run:broadcast",
                "run_mode": "broadcast",
                "agent_kind": "broadcast",
                "trigger_type": "generic_trigger",
            },
            "scanner": {
                "run_id": "hermes_run:scan",
                "run_mode": "scan",
                "agent_kind": "scanner",
                "trigger_type": "generic_trigger",
            },
        },
    }

    result = _phase3_gateway_profile_trigger_check_from_payload(payload, period_days=7)

    assert result["passed"] is True
    assert result["status"] == "pass"
    assert "profiles=reply,personal,broadcast,scanner" in result["detail"]


def test_phase3_profile_trigger_check_requires_reply_live_telemetry() -> None:
    payload = {
        "workspace_id": 3,
        "profiles": {
            "reply": {
                "run_id": "hermes_run:reply",
                "run_mode": "reply",
                "agent_kind": "seller",
                "trigger_type": "telegram_message",
                "has_trigger_telemetry": False,
            },
            "personal": {"run_id": "hermes_run:personal"},
            "broadcast": {"run_id": "hermes_run:broadcast"},
            "scanner": {"run_id": "hermes_run:scan"},
        },
    }

    result = _phase3_gateway_profile_trigger_check_from_payload(payload, period_days=7)

    assert result["passed"] is False
    assert result["status"] == "missing_reply_live_telemetry"


def test_phase3_multi_workspace_check_passes_with_flooded_and_live_workspace() -> None:
    payload = {
        "queriedAt": "2026-05-30T03:20:10.000Z",
        "sessions": [
            {
                "workspaceId": 3,
                "state": "connected",
                "telegramFloodWaits": [
                    {
                        "methodClass": "dialog_sync",
                        "retryAfter": 31,
                        "pausedForMs": 21_000,
                    },
                ],
            },
            {
                "workspaceId": 4,
                "state": "connected",
                "lastLiveInboundHotPathAt": "2026-05-30T03:20:01.000Z",
                "lastLiveInboundHotPathLatencyMs": 80,
            },
        ],
    }

    result = _phase3_gateway_multi_workspace_check_from_payload(payload)

    assert result["passed"] is True
    assert result["status"] == "pass"
    assert "flooded_workspace=3" in result["detail"]
    assert "live_workspace=4" in result["detail"]


def test_phase3_multi_workspace_check_requires_two_connected_workspaces() -> None:
    payload = {
        "queriedAt": "2026-05-30T03:20:10.000Z",
        "sessions": [
            {
                "workspaceId": 3,
                "state": "connected",
                "telegramFloodWaits": [],
                "lastLiveInboundHotPathAt": None,
            },
        ],
    }

    result = _phase3_gateway_multi_workspace_check_from_payload(payload)

    assert result["passed"] is False
    assert result["status"] == "missing_two_connected_workspaces"


def test_phase3_multi_workspace_check_accepts_controlled_flood_wait_proof() -> None:
    payload = {
        "queriedAt": "2026-05-30T03:20:10.000Z",
        "localFloodWaitIsolationProof": {"passed": True, "summary": "pass"},
        "sessions": [
            {
                "workspaceId": 3,
                "state": "connected",
                "telegramFloodWaits": [],
            },
            {
                "workspaceId": 4,
                "state": "connected",
                "telegramFloodWaits": [],
                "lastLiveInboundHotPathAt": "2026-05-30T03:20:01.000Z",
                "lastLiveInboundHotPathLatencyMs": 80,
            },
        ],
    }

    result = _phase3_gateway_multi_workspace_check_from_payload(payload)

    assert result["passed"] is True
    assert result["status"] == "pass"
    assert "controlled flood-wait isolation proof" in result["detail"]


def test_phase3_multi_workspace_check_requires_flood_wait_timing() -> None:
    payload = {
        "queriedAt": "2026-05-30T03:20:10.000Z",
        "sessions": [
            {
                "workspaceId": 3,
                "state": "connected",
                "telegramFloodWaits": [
                    {"methodClass": "dialog_sync", "pausedForMs": 21_000},
                ],
            },
            {
                "workspaceId": 4,
                "state": "connected",
                "lastLiveInboundHotPathAt": "2026-05-30T03:20:01.000Z",
            },
        ],
    }

    result = _phase3_gateway_multi_workspace_check_from_payload(payload)

    assert result["passed"] is False
    assert result["status"] == "missing_flood_wait_timing"


def test_phase3_multi_workspace_check_requires_live_after_flood_wait_started() -> None:
    payload = {
        "queriedAt": "2026-05-30T03:20:10.000Z",
        "sessions": [
            {
                "workspaceId": 3,
                "state": "connected",
                "telegramFloodWaits": [
                    {
                        "methodClass": "dialog_sync",
                        "retryAfter": 31,
                        "pausedForMs": 21_000,
                    },
                ],
            },
            {
                "workspaceId": 4,
                "state": "connected",
                "lastLiveInboundHotPathAt": "2026-05-30T03:19:59.000Z",
            },
        ],
    }

    result = _phase3_gateway_multi_workspace_check_from_payload(payload)

    assert result["passed"] is False
    assert result["status"] == "missing_isolated_live_workspace"


def test_phase3_gateway_result_includes_next_actions_for_live_gaps() -> None:
    local_result = {
        "passed": True,
        "checks": [{"name": "local", "passed": True}],
    }
    live_checks = [
        {
            "name": "live_10_message_trigger_start_p50",
            "passed": False,
            "status": "unmeasured",
        },
        {
            "name": "live_multi_workspace_flood_wait_isolation",
            "passed": False,
            "status": "missing_two_connected_workspaces",
        },
    ]

    result = _phase3_gateway_result_from_parts(
        local_result=local_result,
        live_checks=live_checks,
        local_only=False,
    )

    assert result["passed"] is False
    assert result["phase3_complete"] is False
    assert "Send 10 real inbound Telegram messages" in result["next_actions"][0]
    assert any("Connect a second Telegram workspace" in item for item in result["next_actions"])
    assert result["live_capture_plan"]["required_live_messages"] == 10
    assert result["live_capture_plan"]["needs_second_connected_workspace"] is True
    assert "phase3-gateway" in result["live_capture_plan"]["wait_command"]


def test_phase3_gateway_live_next_actions_dedupes_shared_inbound_gaps() -> None:
    actions = _phase3_gateway_live_next_actions([
        {
            "name": "live_10_message_trigger_start_p50",
            "passed": False,
            "status": "unmeasured",
        },
        {
            "name": "live_restart_long_idle",
            "passed": False,
            "status": "missing_live_update",
        },
        {
            "name": "live_operator_talk_bundle_delivery",
            "passed": False,
            "status": "missing_live_telemetry",
        },
    ])

    assert len(actions) == 1
    assert actions[0].startswith("Send 10 real inbound Telegram messages")


def test_phase3_gateway_live_next_actions_treats_reply_telemetry_as_inbound_gap() -> None:
    actions = _phase3_gateway_live_next_actions([
        {
            "name": "live_profile_trigger_modes",
            "passed": False,
            "status": "missing_reply_live_telemetry",
        },
    ])

    assert len(actions) == 1
    assert actions[0].startswith("Send 10 real inbound Telegram messages")


def test_phase3_gateway_live_capture_plan_builds_marker_guarded_wait_command() -> None:
    plan = _phase3_gateway_live_capture_plan(
        [
            {
                "name": "live_operator_talk_bundle_delivery",
                "passed": False,
                "status": "missing_live_telemetry",
                "data": {"workspace_id": 3},
            },
            {
                "name": "live_multi_workspace_flood_wait_isolation",
                "passed": False,
                "status": "missing_two_connected_workspaces",
                "data": {"sessions": [{"workspaceId": 3, "state": "connected"}]},
            },
        ],
        approve_live_marker="phase3-live-proof",
    )

    assert plan["workspace_id"] == 3
    assert plan["required_live_messages"] == 10
    assert plan["message_marker"] == "phase3-live-proof"
    assert plan["approval_helper_enabled"] is True
    assert plan["connected_workspace_ids"] == [3]
    assert plan["connected_workspace_count"] == 1
    assert plan["needs_second_connected_workspace"] is True
    assert "--approve-live-marker phase3-live-proof" in plan["wait_command"]
    assert "--workspace-id 3" in plan["wait_command"]


def test_phase3_gateway_live_approval_requires_explicit_marker_and_workspace() -> None:
    assert _phase3_gateway_live_approval_option_error(
        approve_live_reply=False,
        approve_live_marker=None,
        workspace_id=None,
        local_only=False,
    ) is None
    assert _phase3_gateway_live_approval_option_error(
        approve_live_reply=True,
        approve_live_marker="phase3-ok",
        workspace_id=None,
        local_only=False,
    ) == "--approve-live-reply requires --workspace-id"
    assert _phase3_gateway_live_approval_option_error(
        approve_live_reply=True,
        approve_live_marker=None,
        workspace_id=3,
        local_only=False,
    ) == "--approve-live-reply requires --approve-live-marker"
    assert _phase3_gateway_live_approval_option_error(
        approve_live_reply=True,
        approve_live_marker="ok",
        workspace_id=3,
        local_only=False,
    ) == "--approve-live-marker must be at least 6 characters"
    assert _phase3_gateway_live_approval_option_error(
        approve_live_reply=True,
        approve_live_marker="phase3-ok",
        workspace_id=3,
        local_only=True,
    ) == "--approve-live-reply cannot be used with --local-only"
    assert _phase3_gateway_live_approval_option_error(
        approve_live_reply=True,
        approve_live_marker="phase3-ok",
        workspace_id=3,
        local_only=False,
    ) is None
