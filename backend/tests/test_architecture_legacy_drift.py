from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = PROJECT_ROOT / "backend/app"


def _python_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def _imports_matching(root: Path, *, banned_prefixes: list[str]) -> list[str]:
    offenders: list[str] = []
    for path in _python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(prefix) for prefix in banned_prefixes):
                    offenders.append(str(path.relative_to(PROJECT_ROOT)))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(prefix) for prefix in banned_prefixes):
                        offenders.append(str(path.relative_to(PROJECT_ROOT)))
    return sorted(set(offenders))


def test_deleted_reply_runtime_modules_stay_deleted() -> None:
    old_pkg = "seller_" + "agent_runtime"
    deleted_paths = [
        BACKEND_APP / "modules/action_runtime" / old_pkg,
        BACKEND_APP / "modules/action_runtime" / ("seller_" + "reply_outcomes.py"),
        BACKEND_APP / "api/routes" / ("ai_" + "replies.py"),
        BACKEND_APP / "api/routes/admin.py",
        BACKEND_APP / "schemas" / ("ai_" + "reply.py"),
        BACKEND_APP / "services" / ("scheduled_" + "reply_sender.py"),
        BACKEND_APP / "services" / ("runtime_" + "operations.py"),
        BACKEND_APP / "services" / ("runtime_" + "signals.py"),
        BACKEND_APP / "services" / ("learning_" + "signal_service.py"),
        BACKEND_APP / "modules" / "sandbox",
    ]
    assert [str(path.relative_to(PROJECT_ROOT)) for path in deleted_paths if path.exists()] == []


def test_active_app_does_not_import_retired_reply_runtime() -> None:
    prefixes = [
        "app.modules.action_runtime." + ("seller_" + "agent_runtime"),
        "app.modules.action_runtime." + ("seller_" + "reply_outcomes"),
        "app.api.routes." + ("ai_" + "replies"),
        "app.schemas." + ("ai_" + "reply"),
        "app.services." + ("scheduled_" + "reply_sender"),
        "app.services." + "runtime_" + "operations",
        "app.services." + "runtime_" + "signals",
        "app.services." + "learning_" + "signal_service",
        "app.modules." + "sandbox",
        "app.models." + ("seller_" + "agent_reply"),
        "app.models." + ("seller_" + "agent_reply_action"),
        "app.models." + ("seller_" + "agent_workspace_runtime"),
    ]
    assert _imports_matching(BACKEND_APP, banned_prefixes=prefixes) == []


def test_active_app_uses_generic_turn_dispatcher() -> None:
    runner = (BACKEND_APP / "modules/conversation_turns/runner.py").read_text()
    main = (BACKEND_APP / "main.py").read_text()
    assert "dispatch_agent_turn" in runner
    assert "ConversationTurnRunner" in main
    assert "AgentRuntimeService" not in main
    assert "ScheduledReplySender" not in main


def test_action_runtime_has_no_retired_reply_transition_helper() -> None:
    source = (BACKEND_APP / "services/action_runtime.py").read_text()
    assert "record_action_state" in source
    assert "record_" + "seller_" + "agent_reply_action_state" not in source
    assert "Seller" + "AgentReply" not in source


def test_delivery_runtime_has_no_deleted_table_foreign_key() -> None:
    model = (BACKEND_APP / "models/delivery_runtime.py").read_text()
    assert "action_record_id" in model
    assert "ForeignKey(\"action_records.id\")" not in model
    assert "ForeignKey(\"ai_" + "replies.id\")" not in model
