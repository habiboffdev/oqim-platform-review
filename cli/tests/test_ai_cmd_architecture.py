from __future__ import annotations

import ast
from pathlib import Path


AI_COMMAND = Path(__file__).resolve().parents[1] / "commands" / "ai.py"
BANNED_AI_CLI_TERMS = {
    ".".join(("app", "brain", "agent")),
    ".".join(("app", "brain", "knowledge_extractor")),
    ".".join(("app", "brain", "voice_profile")),
    ".".join(("app", "brain", "style_search")),
    ".".join(("app", "modules", "draft_engine")),
    ".".join(("app", "models", "catalog_item")),
    "generate" + "_draft",
    "draft" + "_trace_session",
    "Catalog" + "Item",
    "halfvec" + "_cosine",
}


def _function_source(name: str) -> str:
    source = AI_COMMAND.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"function {name!r} not found")


def test_ai_reply_command_uses_seller_agent_runtime_bridge() -> None:
    source = _function_source("_reply_impl")

    assert "app.modules.action_runtime.seller_agent_runtime.bridge" in source
    assert "generate_seller_agent_reply" in source
    assert "app.brain.agent" not in source
    assert "generate_draft" not in source


def test_ai_reply_command_cleans_action_runtime_before_temp_message() -> None:
    source = _function_source("_reply_impl")

    assert "from app.models.action_runtime import ActionRuntime" in source
    assert "sql_delete(ActionRuntime)" in source
    assert source.index("sql_delete(ActionRuntime)") < source.index("sql_delete(Message)")


def test_ai_cli_does_not_keep_hidden_draft_alias() -> None:
    source = AI_COMMAND.read_text(encoding="utf-8")
    tree = ast.parse(source)
    command_names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "command"
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                command_names.append(decorator.args[0].value)

    assert "reply" in command_names
    assert "draft" not in command_names
    assert "Compatibility alias for `oqim ai reply`" not in source


def test_ai_trace_summary_uses_reply_language_for_persisted_reply() -> None:
    source = _function_source("_trace_summary_line")

    assert "reply.persisted" in source
    assert "draft.persisted" not in source


def test_ai_sandbox_json_exposes_reply_aliases() -> None:
    list_source = _function_source("_sandbox_list_impl")
    send_source = _function_source("_sandbox_send_impl")
    trace_source = _function_source("_sandbox_trace_impl")

    assert '"latest_reply_status"' in list_source
    assert "latest_draft_status" not in list_source
    assert '"reply": reply_payload' in send_source
    assert '"draft": reply_payload' not in send_source
    assert "wait_for_sandbox_reply" in send_source
    assert "wait_for_sandbox_draft" not in send_source
    assert '"reply": reply_payload' in trace_source
    assert '"draft": reply_payload' not in trace_source


def test_ai_runtime_signals_cli_reads_reply_freshness_contract() -> None:
    source = _function_source("_runtime_signals_impl")

    assert "seller_agent_reply_freshness" in source
    assert "draft_freshness" not in source
    assert "drafts_total" not in source


def test_ai_search_command_uses_retrieval_core_not_legacy_catalog_embeddings() -> None:
    source = _function_source("_search_impl")

    assert "RetrievalCoreService" in source
    assert "RetrievalContextRequest" in source
    assert "app.models.catalog_item" not in source
    assert "CatalogItem" not in source
    assert "EmbeddingService" not in source


def test_ai_voice_command_uses_business_brain_voice_projection() -> None:
    source = _function_source("_voice_impl")

    assert "BusinessVoiceLearningService" in source
    assert "voice_profile:seller_voice" in source
    assert "app.brain.voice_profile" not in source
    assert "ConversationPair" not in source


def test_ai_cli_has_no_legacy_runtime_imports() -> None:
    source = AI_COMMAND.read_text(encoding="utf-8")

    offenders = sorted(term for term in BANNED_AI_CLI_TERMS if term in source)

    assert offenders == []
