from __future__ import annotations

from collections.abc import Iterable

from app.modules.telegram_tools.contracts import TELEGRAM_TOOL_DEFINITIONS

EXTERNAL_TOOL_DEFINITIONS = dict(TELEGRAM_TOOL_DEFINITIONS)
EXTERNAL_TOOL_SCOPES = frozenset(EXTERNAL_TOOL_DEFINITIONS)


def is_external_tool_scope(scope: str) -> bool:
    return scope in EXTERNAL_TOOL_SCOPES


def external_tool_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    return tuple(scope for scope in scopes if is_external_tool_scope(scope))


def internal_capability_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    return tuple(scope for scope in scopes if not is_external_tool_scope(scope))
