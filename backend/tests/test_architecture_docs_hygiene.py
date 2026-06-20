"""Architecture docs hygiene checks."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_retired_webk_architecture_docs_are_marked_historical():
    retired_docs = [
        PROJECT_ROOT / "docs/archive/2026-05-08-doc-reset/architecture/FORMIRZOSHARIF.md",
        PROJECT_ROOT
        / "docs/archive/2026-05-08-doc-reset/architecture/telegram-web-fork-comparison.md",
    ]

    for doc in retired_docs:
        text = doc.read_text()

        assert "Status: historical" in text, doc
        assert "Do not use as current architecture" in text, doc


def test_event_spine_module_docstring_matches_authoritative_runtime():
    from app.core import event_spine

    docstring = event_spine.__doc__ or ""

    assert "authoritative" in docstring.lower()
    assert "observability-only" not in docstring
    assert "will be added in a later task" not in docstring


def test_architecture_docs_index_separates_current_and_historical_docs():
    index = PROJECT_ROOT / "docs/architecture/README.md"
    text = index.read_text()

    assert "Active Decision Docs" in text
    assert "../COMMAND_CENTER.md" in text
    assert "Archived Docs" in text
    assert "2026-05-08-codebase-audit-and-closure-roadmap.md" in text
    assert "target-runtime-v1.md" in text
    assert "../archive/2026-05-08-doc-reset/architecture/" in text
    assert (
        "../archive/2026-05-08-doc-reset/architecture/"
        "2026-05-06-current-state-and-master-roadmap.md"
    ) in text


def test_docs_entrypoint_keeps_decision_path_small():
    """Enforce the 2026-05-16 Agentic Business OS pivot decision path.

    The doc system declares an authority order in DOCS_SYSTEM.md. This test
    enforces that the entrypoint (`docs/README.md`), the command center,
    AGENTS.md / CLAUDE.md, and DOCS_SYSTEM.md all point at the active pivot
    docs and do not regress to pre-pivot lineage.
    """
    docs_index = PROJECT_ROOT / "docs/README.md"
    command_center = PROJECT_ROOT / "docs/COMMAND_CENTER.md"
    docs_system = PROJECT_ROOT / "docs/DOCS_SYSTEM.md"
    agents = PROJECT_ROOT / "AGENTS.md"
    claude = PROJECT_ROOT / "CLAUDE.md"
    pivot = PROJECT_ROOT / "docs/architecture/2026-05-16-agentic-business-os-pivot.md"
    audit = PROJECT_ROOT / "docs/architecture/2026-05-16-conversation-requirements-audit.md"

    index_text = docs_index.read_text()
    command_text = command_center.read_text()
    system_text = docs_system.read_text()
    agents_text = agents.read_text()
    claude_text = claude.read_text()
    pivot_text = pivot.read_text()
    audit_text = audit.read_text()

    # docs/README.md is the entrypoint and points at the command center first.
    assert "Read First" in index_text
    assert "COMMAND_CENTER.md" in index_text
    assert "2026-05-16-agentic-business-os-pivot.md" in index_text

    # COMMAND_CENTER.md frames the current large slice.
    assert "Current Large Slice" in command_text
    assert "Agentic Business OS reset" in command_text
    assert "visual concept generation briefs" in command_text

    # DOCS_SYSTEM.md states the decision authority order.
    assert "one command center, few active docs, many archived references" in system_text
    assert "Decision Authority" in system_text
    assert "Current code and tests" in system_text

    # AGENTS.md and CLAUDE.md both point at the command center and pivot.
    for text in (agents_text, claude_text):
        assert "docs/COMMAND_CENTER.md" in text
        assert "docs/architecture/2026-05-16-agentic-business-os-pivot.md" in text
        assert "docs/architecture/2026-05-16-conversation-requirements-audit.md" in text

    # The pivot doc names the product primitives and the target runtime.
    assert "Product Primitives" in pivot_text
    assert "Source Intake is continuous" in pivot_text
    assert "Universal Extraction produces candidates" in pivot_text

    # The audit doc maps requirements back to the user's sketch/conversation.
    assert "Traceability" in audit_text
    assert "Anti-Drift Rule" in audit_text
