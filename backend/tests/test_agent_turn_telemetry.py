from __future__ import annotations

from app.modules.agent_runtime_v2.runtime_service import build_turn_telemetry


def test_telemetry_carries_profile_and_faithfulness():
    payload = build_turn_telemetry(
        profile_hash="abc123", profile_kind="agent", execution_mode="interactive",
        allowed_tool_names=("talk.send_msgs", "knowledge_search_catalog"),
        authority_bundle_size=2, warning_count=1, missing_field_count=1,
        unsupported_authority_claims=0, faithfulness_mode="deferred_critic",
        confidence=0.8, decision="auto_send",
    )
    assert payload["schema_version"] == "agent_turn_telemetry.v1"
    assert payload["profile_kind"] == "agent"
    assert payload["execution_mode"] == "interactive"
    assert payload["profile_hash"] == "abc123"
    assert payload["tools_exposed"] == ["talk.send_msgs", "knowledge_search_catalog"]
    assert payload["authority"]["bundle_size"] == 2
    assert payload["authority"]["warning_count"] == 1
    assert payload["authority"]["missing_field_count"] == 1
    assert payload["faithfulness"]["mode"] == "deferred_critic"
    assert payload["faithfulness"]["unsupported_authority_claims"] == 0
    assert payload["confidence"] == 0.8
    assert payload["decision"] == "auto_send"
