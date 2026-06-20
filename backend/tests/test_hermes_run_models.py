from __future__ import annotations

from pathlib import Path

import app.models as models
from app.models import HermesRun, HermesRunEvent

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION = _ROOT / "migrations/versions/1d2e3f4a5b6c_phase2_hermes_runs.py"


def test_hermes_run_model_is_canonical_without_v2_aliases() -> None:
    assert not hasattr(models, "AgentRunV2")
    assert not hasattr(models, "AgentRunEventV2")
    assert HermesRun.__tablename__ == "hermes_runs"
    assert HermesRunEvent.__tablename__ == "hermes_run_events"


def test_hermes_run_model_exposes_phase2_query_columns() -> None:
    columns = HermesRun.__table__.columns

    assert "engine_run_id" in columns
    assert columns["engine_run_id"].nullable is True
    assert "idempotency_key" in columns
    assert "lane" in columns
    assert "run_mode" in columns
    assert "adk_session_id" not in columns


def test_phase2_migration_keeps_engine_run_id_nullable_until_runtime_starts() -> None:
    migration_text = _MIGRATION.read_text()

    assert 'op.alter_column("hermes_runs", "engine_run_id", nullable=True)' in migration_text


def test_hermes_run_docstrings_do_not_present_adk_as_current_runtime() -> None:
    model_doc = HermesRun.__doc__ or ""
    event_doc = HermesRunEvent.__doc__ or ""

    assert "ADK" not in model_doc
    assert "ADK" not in event_doc
