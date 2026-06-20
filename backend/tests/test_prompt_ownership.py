from __future__ import annotations

import re
from pathlib import Path

from app.brain.prompt_ownership import covered_prompt_ids, load_prompt_ownership_ledger
from app.brain.prompt_registry import get_prompt_registry, load_prompt_manifest


def test_commercial_prompt_ownership_ledger_covers_high_risk_ai_paths() -> None:
    ledger = load_prompt_ownership_ledger()
    entries_by_prompt = {
        entry.prompt_id: entry for entry in ledger.entries if entry.prompt_id
    }

    required_prompts = {
        "extraction.seller_voice": "signal-producer",
    }

    for prompt_id, expected_classification in required_prompts.items():
        entry = entries_by_prompt[prompt_id]
        assert entry.classification == expected_classification
        assert entry.target_owner in {
            "AutoCRM Kernel",
            "AutoCRM Kernel and Action Runtime",
            "Business Brain",
            "Business Brain and AutoCRM Kernel",
            "Business Brain Learning Lab",
            "BI Agent compatibility",
            "Promoter Agent compatibility",
            "Seller Agent",
        }
        assert entry.commercial_truth_inferred
        assert entry.replacement_memory_fields
        assert entry.cutover_status
        assert entry.may_mutate_commercial_truth is False


def test_prompt_ownership_ledger_marks_legacy_truth_paths_for_cutover() -> None:
    ledger = load_prompt_ownership_ledger()
    entries_by_prompt = {
        entry.prompt_id: entry for entry in ledger.entries if entry.prompt_id
    }

    assert "crm.extraction_system" not in entries_by_prompt
    assert "action_engine.seller_follow_up_classifier" not in entries_by_prompt
    assert "action_engine.customer_follow_up_classifier" not in entries_by_prompt
    assert "api.customer_summary_system" not in entries_by_prompt
    assert "api.customer_summary_user" not in entries_by_prompt


def test_prompt_ownership_ledger_covers_every_registered_prompt() -> None:
    ledger = load_prompt_ownership_ledger()
    registered = {prompt.id for prompt in get_prompt_registry().list()}

    assert registered - covered_prompt_ids(ledger) == set()


def test_prompt_registry_manifest_matches_every_markdown_asset() -> None:
    manifest = load_prompt_manifest()
    registry_prompts = {
        (prompt.id, prompt.version): prompt
        for prompt in get_prompt_registry().list()
    }

    assert {
        (entry.id, entry.version)
        for entry in manifest.entries
    } == set(registry_prompts)
    assert all(entry.eval_suite for entry in manifest.entries)
    for entry in manifest.entries:
        prompt = registry_prompts[(entry.id, entry.version)]
        assert entry.model_policy == prompt.model_policy
        assert entry.output_schema == prompt.output_schema
        assert entry.cache_policy == prompt.cache_policy
        assert entry.cache_policy in {"stable_system_prompt", "no_cache"}


def test_hermes_reply_prompt_keeps_telegram_mechanics_invariants() -> None:
    prompt = get_prompt_registry().load("agent_runtime.hermes_reply", version="1.0.0")
    body = re.sub(r"\s+", " ", prompt.body)

    # Tool mechanics stay in the managed prompt (voice/playbook moved to AGENT.md).
    assert "one bubble = one item inside a single `talk.send_msgs` call" in body
    assert "reply_message_id" in body
    assert "talk.send_reaction" in body
    assert "Reaction ALONE is the complete reply only for pure social acknowledgements" in body
    assert "how long bubbles run follows the agent's voice and AGENT.md" in body
    # pilot hardening (2026-06-18): RAG removed from the talk loop — AGENT.md is
    # the single source of truth (no "catalog search results" language anymore).
    assert "AGENT.md is your single source of truth" in body
    assert "Do not claim you will notify, check, reserve, or schedule unless it is true" in body
    assert "Business facts present in AGENT.md" in body
    # Continuity guidance reflects the Hermes session (no re-pasted transcript).
    assert "Prior conversation arrives natively as earlier turns" in body
    # Slice 3: the seller no longer records handoffs/prices/lead state itself —
    # the records pass captures it after the reply, so the prompt is conversation-only.
    assert "You MAY tell a committed customer that a person" in body
    assert "You do not record" in body
    assert "conversation.set_state" not in body
    assert "shown_prices" not in body
    assert "work.handoff" not in body


def test_hermes_reply_prompt_is_company_agnostic() -> None:
    prompt = get_prompt_registry().load("agent_runtime.hermes_reply", version="1.0.0")
    banned_fragments = (
        "SATStation",
        "starter coins",
        "Starter coins",
        "40 000 so'm",
        "baseline test",
    )

    offenders = [fragment for fragment in banned_fragments if fragment in prompt.body]

    assert offenders == []


def test_hermes_reply_prompt_uses_xml_style_sections() -> None:
    prompt = get_prompt_registry().load("agent_runtime.hermes_reply", version="1.0.0")

    for tag in (
        "identity",
        "material_hierarchy",
        "capabilities",
        "input_protocol",
        "untrusted_observed_content",
        "conversation_core",
        "telegram_mechanics",
        "authority_and_truth",
        "tool_protocol",
        "output_protocol",
    ):
        assert f"<{tag}>" in prompt.body
        assert f"</{tag}>" in prompt.body


def test_agent_md_synthesis_prompt_uses_xml_style_sections() -> None:
    prompt = get_prompt_registry().load(
        "agent_documents.agent_md_synthesis",
        version="1.0.0",
    )

    for tag in (
        "task",
        "language",
        "source_priority",
        "capability_boundary",
        "section_contract",
        "evidence_policy",
    ):
        assert f"<{tag}>" in prompt.body
        assert f"</{tag}>" in prompt.body


def test_prompt_asset_payload_exposes_stable_cache_metadata() -> None:
    from app.brain.prompt_payload import prompt_asset_payload

    payload = prompt_asset_payload("extraction.seller_voice", version="1.0.0")

    assert payload["registry_state"] == "loaded"
    assert payload["cache_policy"] == "stable_system_prompt"
    assert payload["digest"]
    assert payload["cache_key"] == (
        f"prompt:extraction.seller_voice:1.0.0:stable_system_prompt:{payload['digest']}"
    )


def test_small_prompt_cache_payload_skips_gemini_cached_content() -> None:
    from app.brain.prompt_payload import prompt_cache_payload_for_asset

    prompt = get_prompt_registry().load("agent_runtime.faithfulness_judge", version="1.0.0")
    payload = prompt_cache_payload_for_asset(
        prompt,
        cache_scope="agent_runtime.faithfulness_judge",
    )

    assert payload is not None
    assert payload["prompt_asset"]["prompt_id"] == "agent_runtime.faithfulness_judge"
    assert payload["cacheable"] is False
    assert payload["provider_strategy"] == "none"
    assert payload["skip_reason"] == "gemini_min_cache_tokens"
    assert payload["estimated_tokens"] < payload["min_cache_tokens"]


def test_trace_metrics_preserve_hermes_output_and_thinking_tokens() -> None:
    from app.modules.agent_runtime_v2.trace import summarize_trace_metrics

    metrics = summarize_trace_metrics(
        [
            {
                "sequence": 4,
                "stage": "llm",
                "event": "success",
                "operation": "hermes_reply",
                "provider": "gemini",
                "model": "gemini-3.5-flash",
                "latency_ms": 1234,
                "fallback": False,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cached_content_tokens": 40,
                    "thought_tokens": 9,
                },
                "output_text_preview": "Assalomu alaykum!",
                "tool_calls": [{"name": "talk.send_msg"}],
                "thought_summaries": ["Answer greeting briefly."],
            }
        ]
    )

    assert metrics["input_tokens"] == 100
    assert metrics["output_tokens"] == 20
    assert metrics["cached_content_tokens"] == 40
    assert metrics["cache_savings_tokens"] == 40
    assert metrics["cache_effective_input_tokens"] == 60
    assert metrics["cache_effective_total_tokens"] == 89
    assert metrics["token_breakdown"] == {
        "raw_input_tokens": 100,
        "cached_content_tokens": 40,
        "cache_savings_tokens": 40,
        "cache_effective_input_tokens": 60,
        "output_tokens": 20,
        "thought_tokens": 9,
        "raw_total_tokens": 120,
        "cache_effective_total_tokens": 89,
    }
    assert metrics["thought_tokens"] == 9
    assert metrics["calls"][0]["cache_effective_input_tokens"] == 60
    assert metrics["calls"][0]["cache_effective_total_tokens"] == 89
    assert metrics["calls"][0]["output_text_preview"] == "Assalomu alaykum!"
    assert metrics["calls"][0]["tool_calls"] == [{"name": "talk.send_msg"}]
    assert metrics["calls"][0]["thought_summaries"] == ["Answer greeting briefly."]


def test_llm_gateway_model_payload_keeps_cache_metadata_and_strips_body() -> None:
    from app.brain.prompt_payload import prompt_asset_payload
    from app.modules.commercial_spine.contracts import LLMGatewayRequest
    from app.modules.commercial_spine.llm_gateway import _gateway_model_input_payload

    prompt = prompt_asset_payload("extraction.seller_voice", version="1.0.0")
    request = LLMGatewayRequest(
        route_key="structured_fast",
        workflow_name="prompt_cache_metadata",
        prompt_id="extraction.seller_voice",
        prompt_version="1.0.0",
        input_payload={
            "prompt": prompt,
            "customer_text": "Salom",
        },
        output_schema_name="SellerVoiceExtractionOutput",
        workspace_id=1,
        correlation_id="prompt-cache:test",
    )

    payload = _gateway_model_input_payload(request)

    assert payload["prompt"]["prompt_id"] == "extraction.seller_voice"
    assert payload["prompt"]["cache_policy"] == "stable_system_prompt"
    assert payload["prompt"]["cache_key"] == prompt["cache_key"]
    assert payload["prompt"]["digest"] == prompt["digest"]
    assert "body" not in payload["prompt"]


def test_legacy_prompt_island_is_deleted() -> None:
    ledger = load_prompt_ownership_ledger()
    registered = {prompt.id for prompt in get_prompt_registry().list()}

    assert not any(prompt_id.startswith("legacy_brain.") for prompt_id in registered)
    assert not any(
        entry.prompt_prefix == "legacy_brain." or (
            entry.prompt_id and entry.prompt_id.startswith("legacy_brain.")
        )
        for entry in ledger.entries
    )


def test_seller_brain_prompt_island_is_deleted() -> None:
    ledger = load_prompt_ownership_ledger()
    registered = {prompt.id for prompt in get_prompt_registry().list()}
    prompt_root = Path("app/brain/prompt_assets/seller_brain")

    assert list(prompt_root.glob("**/*.md")) == []
    assert not any(prompt_id.startswith("seller_brain.") for prompt_id in registered)
    assert not any(
        entry.prompt_prefix == "seller_brain." or (
            entry.prompt_id and entry.prompt_id.startswith("seller_brain.")
        )
        for entry in ledger.entries
    )


def test_route_local_customer_summary_prompts_are_deleted() -> None:
    ledger = load_prompt_ownership_ledger()
    registered = {prompt.id for prompt in get_prompt_registry().list()}

    assert "api.customer_summary_system" not in registered
    assert "api.customer_summary_user" not in registered
    assert not any(
        entry.prompt_prefix == "api." or (
            entry.prompt_id and entry.prompt_id.startswith("api.customer_summary")
        )
        for entry in ledger.entries
    )


def test_old_commercial_intelligence_module_is_not_a_prompt_owner() -> None:
    module_root = Path("app/modules/commercial_intelligence")
    assert not module_root.exists()

    ledger = load_prompt_ownership_ledger()
    proof_refs = {
        proof
        for entry in ledger.entries
        for proof in entry.proof_refs
    }
    assert not any("test_commercial_intelligence" in proof for proof in proof_refs)
    assert "backend/tests/test_commercial_media_signal_producer.py" not in proof_refs


def test_active_prompt_assets_do_not_use_store_specific_global_examples() -> None:
    offenders: list[str] = []
    for prompt in get_prompt_registry().list():
        if prompt.id.startswith("legacy_brain."):
            continue
        for banned_term in (
            "iPhone",
            "Samsung",
            "ayfon",
            "Pro Max",
            "electronics",
            "storage",
            " GB",
            "red iPhone case",
        ):
            if banned_term in prompt.body:
                offenders.append(f"{prompt.id}:{banned_term}")

    assert offenders == []


def test_active_prompt_assets_do_not_use_old_draft_language() -> None:
    offenders: list[str] = []
    allowed_phrases = {
        "draft_for_review",
    }
    for prompt in get_prompt_registry().list():
        if prompt.id.startswith("legacy_brain."):
            continue
        body = prompt.body
        for allowed in allowed_phrases:
            body = body.replace(allowed, "")
        if "draft" in body.lower():
            offenders.append(prompt.id)

    assert offenders == []


def test_seller_agent_prompts_do_not_default_to_product_model_language() -> None:
    prompts = {
        prompt.id: prompt.body
        for prompt in get_prompt_registry().list()
        if prompt.id.startswith("seller_agent.")
    }
    banned_fragments = (
        "Vague product ask",
        "vague product question",
        "which product/model/variant",
        "which model/product/variant",
        "qaysi model yoki variant",
        "Shu model bo'yicha",
    )

    offenders = [
        f"{prompt_id}:{fragment}"
        for prompt_id, body in prompts.items()
        for fragment in banned_fragments
        if fragment in body
    ]

    assert offenders == []


def test_active_prompts_are_candidate_or_composer_boundaries() -> None:
    prompts = {prompt.id: prompt.body for prompt in get_prompt_registry().list()}

    assert "crm.extraction_system" not in prompts
    assert "action_engine.seller_follow_up_classifier" not in prompts
    assert "action_engine.customer_follow_up_classifier" not in prompts
    assert "draft_engine.direct_generation_examples" not in prompts


def test_runtime_prompt_id_defaults_are_registered_markdown_assets() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    app_root = backend_root / "app"
    registered = {prompt.id for prompt in get_prompt_registry().list()}
    ignored_paths = {
        app_root / "models" / "commercial_spine.py",
        app_root / "modules" / "commercial_spine" / "prompt_registry.py",
        app_root / "brain" / "prompt_ownership.py",
        app_root / "brain" / "prompt_payload.py",
        app_root / "brain" / "prompt_registry.py",
    }
    discovered: dict[str, list[str]] = {}
    pattern = re.compile(r'prompt_id(?:\s*:\s*str)?\s*=\s*"([^"]+)"')

    for path in sorted(app_root.rglob("*.py")):
        if path in ignored_paths:
            continue
        text = path.read_text(encoding="utf-8")
        for prompt_id in pattern.findall(text):
            discovered.setdefault(prompt_id, []).append(str(path.relative_to(backend_root)))

    unmanaged = {
        prompt_id: paths
        for prompt_id, paths in discovered.items()
        if prompt_id not in registered
    }
    assert unmanaged == {}
