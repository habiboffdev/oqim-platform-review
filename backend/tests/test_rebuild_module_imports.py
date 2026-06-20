import importlib
from pathlib import Path

import pytest

APP_MODULES = Path(__file__).parent.parent / "app" / "modules"
APP_SCHEMAS = Path(__file__).parent.parent / "app" / "schemas"

RUNTIME_MODULES = (
    "app.modules.brain.business_document",
    "app.modules.brain.agent_document",
    "app.modules.agent_runtime_v2.runtime_service",
    "app.modules.agent_runtime_v2.runtime_profile",
    "app.modules.channel_layer.reconciler",
)

DELETED_P1_SCAFFOLDS = (
    APP_MODULES / "source_intake",
    APP_MODULES / "intelligence_v2",
    APP_MODULES / "brain" / "service.py",
    APP_MODULES / "agent_runtime_v2" / "contracts.py",
    APP_MODULES / "agent_runtime_v2" / "service.py",
    APP_MODULES / "channel_layer" / "contracts.py",
    APP_MODULES / "channel_layer" / "service.py",
)

DELETED_UNUSED_SCHEMA_ISLANDS = (
    APP_SCHEMAS / "embedding_runtime.py",
    APP_SCHEMAS / "seller_agent.py",
)


@pytest.mark.parametrize("module_path", RUNTIME_MODULES)
def test_runtime_module_is_importable(module_path: str) -> None:
    mod = importlib.import_module(module_path)
    assert mod is not None


@pytest.mark.parametrize("path", DELETED_P1_SCAFFOLDS)
def test_empty_p1_scaffold_stays_deleted(path: Path) -> None:
    assert not path.exists()


@pytest.mark.parametrize("path", DELETED_UNUSED_SCHEMA_ISLANDS)
def test_unused_schema_island_stays_deleted(path: Path) -> None:
    assert not path.exists()


def test_no_empty_p1_placeholder_docstrings_in_runtime_modules() -> None:
    placeholder_phrases = (
        "P1: skeleton only",
        "P1: empty placeholder",
    )
    for py_file in APP_MODULES.rglob("*.py"):
        source = py_file.read_text()
        for phrase in placeholder_phrases:
            assert phrase not in source, f"{py_file} still contains {phrase!r}"
