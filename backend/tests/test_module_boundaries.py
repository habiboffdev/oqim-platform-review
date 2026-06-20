import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).parent.parent / "app" / "modules"

BOUNDARY_RULES: dict[str, set[str]] = {
    "brain": {"source_intake", "agent_runtime_v2", "channel_layer", "intelligence_v2"},
    "channel_layer": {"brain", "agent_runtime_v2", "intelligence_v2"},
    # agent_runtime_v2 may call brain + channel_layer + retrieval_core; only block these:
    "agent_runtime_v2": {"source_intake", "intelligence_v2"},
}


def _iter_py_files(module: str) -> Iterator[Path]:
    root = BACKEND_ROOT / module
    if not root.exists():
        return
    yield from root.rglob("*.py")


def _imports_of(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(), filename=str(file_path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
    return imports


@pytest.mark.parametrize("module,forbidden", list(BOUNDARY_RULES.items()))
def test_module_boundary(module: str, forbidden: set[str]) -> None:
    for py_file in _iter_py_files(module):
        for imp in _imports_of(py_file):
            for banned in forbidden:
                assert f"app.modules.{banned}" not in imp, (
                    f"{py_file} imports {imp} which violates "
                    f"boundary rule: {module} may not import {banned}"
                )
