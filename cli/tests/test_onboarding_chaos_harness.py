from __future__ import annotations

from pathlib import Path


TEST_COMMAND = Path(__file__).resolve().parents[1] / "commands" / "test_cmd.py"


def test_onboarding_chaos_harness_covers_pdf_files_api_gateway_path() -> None:
    source = TEST_COMMAND.read_text(encoding="utf-8")

    assert "tests/test_onboarding_source_learning_runtime.py" in source
    assert (
        "tests/test_vertex_gemini_gateway_phase75.py::"
        "test_llm_gateway_uploads_file_api_content_parts"
    ) in source


def test_onboarding_source_learning_runtime_covers_restart_and_rate_limit_cases() -> None:
    test_source = (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "tests"
        / "test_onboarding_source_learning_runtime.py"
    ).read_text(encoding="utf-8")

    assert (
        "test_onboarding_source_runtime_recovers_stale_learning_projection_after_restart"
        in test_source
    )
    assert (
        "test_onboarding_source_runtime_marks_rate_limit_as_retryable_provider_pressure"
        in test_source
    )
