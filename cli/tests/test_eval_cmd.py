from pathlib import Path

from cli.commands.eval_cmd import (
    QUALITY_SUITE_COMMANDS,
    _load_quality_report,
    _reply_eval_voice_fact_value,
    _reply_eval_warranty_fact_value,
)
from cli.commands.eval_cmd import (
    app as eval_app,
)


def test_reply_eval_workspace_seeds_store_agnostic_business_brain_voice_fact() -> None:
    payload = _reply_eval_voice_fact_value()

    assert payload["quality_score"] == "strong"
    assert payload["message_count_analyzed"] > 0
    assert payload["voice_card"]["tone"] == "short_helpful_honest"

    combined = " ".join(
        [
            payload["profile_text"],
            *payload["anti_patterns"],
            *[
                example
                for examples in payload["exemplar_bank"].values()
                for example in examples
            ],
        ]
    ).lower()
    assert "iphone" not in combined
    assert "ayfon" not in combined
    assert "qaysi model yoki variant" not in combined
    assert "qaysi variant" not in combined


def test_reply_eval_workspace_seeds_business_brain_warranty_fact() -> None:
    payload = _reply_eval_warranty_fact_value()

    assert payload["topic"] == "warranty"
    assert "7 kunlik" in payload["answer"]
    assert "kafolat" in payload["guidance"]
    assert "telefon" not in payload["answer"].lower()


def test_quality_eval_commands_cover_release_scorecard_targets() -> None:
    assert {
        "golden-demo",
        "onboarding",
        "seller-agent",
        "grounding",
        "autopilot",
        "bi",
        "learning-loop",
    } <= set(QUALITY_SUITE_COMMANDS)


def test_retired_extraction_eval_command_is_not_registered() -> None:
    command_names = {command.name for command in eval_app.registered_commands}
    retired_command = "auto" + "crm-extraction"

    assert retired_command not in command_names
    assert "buyer-intent" in command_names
    assert "retrieval-core" in command_names
    assert "runtime-profiles" in command_names
    assert "channel-source" in command_names
    assert "channel-delivery" in command_names
    assert "catalog-core" in command_names
    assert "sales-replay" in command_names
    assert "adversarial-replay" in command_names
    assert "shadow-autopilot" in command_names


def test_quality_eval_commands_can_fail_on_latency_budget() -> None:
    eval_cmd = Path(__file__).parents[1] / "commands" / "eval_cmd.py"
    text = eval_cmd.read_text()

    assert "latency_p95_budget" in text
    assert "source_latency_p95_budget" in text
    assert "report.p95_case_duration_ms <= max_p95_ms" in text
    assert "report.p95_source_duration_ms <= max_p95_ms" in text
    assert text.count("--max-p95-ms") >= 4


def test_channel_source_eval_command_exposes_durable_worker_proof() -> None:
    eval_cmd = Path(__file__).parents[1] / "commands" / "eval_cmd.py"
    text = eval_cmd.read_text()

    assert "--durable" in text
    assert "run_channel_source_durable_eval_suite" in text
    assert "queued_learning_count" in text
    assert "claimed_source_count" in text
    assert "hermes_run_trace_count" in text


def test_channel_delivery_eval_command_exposes_partial_replay_proof() -> None:
    eval_cmd = Path(__file__).parents[1] / "commands" / "eval_cmd.py"
    text = eval_cmd.read_text()

    assert "channel-delivery" in text
    assert "run_channel_delivery_eval_suite" in text
    assert "unknown_count" in text
    assert "replayed_count" in text
    assert "duplicate_delivery_count" in text


def test_runtime_profile_eval_command_exposes_background_lane_proof() -> None:
    eval_cmd = Path(__file__).parents[1] / "commands" / "eval_cmd.py"
    text = eval_cmd.read_text()

    assert "runtime-profiles" in text
    assert "run_runtime_profile_background_eval_suite" in text
    assert "completed_run_count" in text
    assert "deduped_replay_count" in text
    assert "tool_schema_count" in text


def test_replay_eval_commands_expose_shadow_autopilot_proof() -> None:
    eval_cmd = Path(__file__).parents[1] / "commands" / "eval_cmd.py"
    text = eval_cmd.read_text()

    assert "sales-replay" in text
    assert "adversarial-replay" in text
    assert "shadow-autopilot" in text
    assert "run_sales_replay_eval_suite" in text
    assert "run_adversarial_replay_eval_suite" in text
    assert "run_shadow_autopilot_eval_suite" in text
    assert "customer_visible_delivery_count == 0" in text
    assert "business_truth_fact_delta" in text


def test_golden_demo_quality_report_is_executable_and_red() -> None:
    report = _load_quality_report("golden-demo")

    assert report.suite == "golden-demo"
    assert report.release_class == "red"
    assert report.hard_failure_count > 0
    assert report.total_cases >= 10


def test_eval_cli_has_no_legacy_reply_seed_models() -> None:
    eval_cmd = Path(__file__).parents[1] / "commands" / "eval_cmd.py"
    text = eval_cmd.read_text()

    assert "app.models.voice_profile" not in text
    assert "app.models.knowledge" not in text
    assert "VoiceProfile(" not in text
    assert "BusinessKnowledge(" not in text
    assert '"catalog_search"' not in text
    assert '"knowledge_search"' not in text
    assert "SourceUnitRebuildRequest" in text
    assert "update_fact_state" in text
