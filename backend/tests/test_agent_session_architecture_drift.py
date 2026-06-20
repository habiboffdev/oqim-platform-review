from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def _retired_paths() -> list[Path]:
    old_pkg = "seller_" + "agent_runtime"
    return [
        APP / "modules/action_runtime" / old_pkg,
        APP / "modules/action_runtime" / ("seller_" + "reply_outcomes.py"),
        APP / "modules/agent_runtime_v2" / ("reply_" + "service.py"),
        APP / "api/routes" / ("ai_" + "replies.py"),
        APP / "schemas" / ("ai_" + "reply.py"),
        APP / "services" / ("scheduled_" + "reply_sender.py"),
        APP / "services" / ("runtime_" + "operations.py"),
        APP / "services" / ("runtime_" + "signals.py"),
        APP / "services" / ("learning_" + "signal_service.py"),
        APP / "modules" / "sandbox",
        APP / "models" / ("seller_" + "agent_reply.py"),
        APP / "models" / ("seller_" + "agent_reply_action.py"),
        APP / "models" / ("seller_" + "agent_workspace_runtime.py"),
    ]


def test_retired_reply_brain_is_absent_from_active_runtime() -> None:
    assert [str(path.relative_to(ROOT)) for path in _retired_paths() if path.exists()] == []


def test_generic_agent_runtime_dispatcher_owns_turn_execution() -> None:
    source = (APP / "modules/agent_runtime_v2/dispatcher.py").read_text()
    assert "dispatch_agent_turn" in source
    assert "AgentRuntimeService" in source
    assert "HermesRunService" in source
    assert "OqimHermesSessionDB" in source
    assert "TalkBundleService" in source


def test_runtime_service_replaces_retired_service_names() -> None:
    assert not (APP / "modules/agent_runtime_v2" / ("reply_" + "service.py")).exists()
    source = (APP / "modules/agent_runtime_v2/runtime_service.py").read_text()
    assert "class AgentRuntimeService" in source
    assert "run_turn" in source
    assert "gather_turn_context" in source
    assert "run_from_context" in source
    assert ("Reply" + "AgentService") not in source


def test_hermes_engine_does_not_force_cold_session_db_none() -> None:
    source = (APP / "modules/agent_runtime_v2/hermes/engine.py").read_text()
    assert "session_db=None" not in source


def test_no_keyword_or_regex_safety_filters_in_agent_runtime() -> None:
    checked = [
        APP / "modules/agent_runtime_v2/runtime_service.py",
        APP / "modules/agent_runtime_v2/dispatcher.py",
        APP / "modules/agent_runtime_v2/hermes/engine.py",
    ]
    banned = [
        "re.search(",
        "re.match(",
        ".lower().find(",
        'if "ignore previous"',
        "if 'ignore previous'",
    ]
    for path in checked:
        source = path.read_text()
        assert [token for token in banned if token in source] == []


def test_delivery_runtime_uses_generic_action_record_correlation() -> None:
    combined = "\n".join(
        path.read_text()
        for path in [
            APP / "models/delivery_runtime.py",
            APP / "services/delivery_runtime.py",
            APP / "core/event_spine.py",
        ]
    )
    assert "action_record_id" in combined
    assert "ai_" + "reply_id" not in combined
    assert "ai_" + "replies" not in combined
