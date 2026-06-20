"""Budget + content guards for the restructured hermes_reply prompt.

The managed prompt carries only generic invariants; voice/cadence/playbook
prose is owner material (AGENT.md). The whole body is re-processed every LLM
iteration, so size is a latency/cost lever — guard it.
"""

from __future__ import annotations

from app.modules.agent_runtime_v2.reply_runtime import load_hermes_reply_prompt


def test_prompt_is_lean_and_keeps_invariants():
    body = load_hermes_reply_prompt().body
    # 7000 budget set 2026-06-11: from-zero rewrite — the 13k accreted rulebook
    # made replies sound like AI lectures; the contract is now ~5.5k and growth
    # requires a deliberate budget bump here.
    assert len(body) <= 7000, f"hermes_reply.md grew again: {len(body)} chars"
    for tag in (
        "<identity>",
        "<material_hierarchy>",
        "<capabilities>",
        "<untrusted_observed_content>",
        "<authority_and_truth>",
        "<tool_protocol>",
        "<output_protocol>",
    ):
        assert tag in body, f"missing invariant section {tag}"
    # voice/cadence/playbook moved to AGENT.md — must NOT come back:
    assert "<default_sales_skill_pack>" not in body
    assert "<sales_micro_skill>" not in body


def test_prompt_makes_reactions_prescriptive_and_length_voice_owned():
    body = load_hermes_reply_prompt().body
    assert "pure social acknowledgements" in body  # reaction-only scope is explicit
    assert "confirming bubble" in body  # customer deliverables get words, not just emoji
    # Length is owned by the agent's voice + AGENT.md, not a hardcoded ~180 cap
    # in this general-rules contract (it contradicted per-agent voice configs).
    assert "the agent's voice and AGENT.md" in body
    assert "~180" not in body
    assert "smallest response" not in body


def test_bubble_rule_appears_once_not_three_times():
    body = load_hermes_reply_prompt().body
    assert body.lower().count("one sentence, one idea") <= 1
    assert body.lower().count("one sentence and one idea") == 0


def test_prompt_keeps_human_register_invariants():
    flat = " ".join(load_hermes_reply_prompt().body.split())
    # greeting reciprocity is unconditional (the missed-salom live failure)
    assert "every time the customer greets" in flat
    # AND open the FIRST reply of a conversation with a greeting even if the
    # customer led with a bare question (live: "kozimxon aka ..." got no salom).
    assert "open the first reply of a conversation with a brief greeting" in flat
    # texting register, not corporate-letter register (the "AI-sounding" failure)
    assert "typing on a phone" in flat
    assert "ticket-queue phrasing" in flat
    assert "the same emoji" in flat


def test_prompt_trusts_agent_md_facts_without_research():
    body = load_hermes_reply_prompt().body
    assert "Business facts present in AGENT.md" in body


def test_prompt_does_not_reference_removed_history_block():
    # The <conversation_history> turn block was retired (Hermes session owns
    # continuity); the prompt must not instruct the model about it.
    body = load_hermes_reply_prompt().body
    assert "<conversation_history>" not in body


def test_prompt_does_not_name_handoff_or_bookkeeping_tools():
    # Slice 3: the seller no longer records handoffs/prices/lead state itself;
    # the records pass captures it after the reply. The prompt must not name any
    # handoff/bookkeeping tool.
    flat = " ".join(load_hermes_reply_prompt().body.split())
    assert "work.handoff" not in flat
    assert "work.create_task" not in flat
    assert "owner.notify" not in flat
    assert "conversation.record_intelligence" not in flat
    assert "captured for you" in flat  # the records pass does it post-reply


def test_prompt_layers_stay_in_order():
    """The layer architecture is a contract (docs/PROMPTS.md): identity ->
    hierarchy -> capabilities -> input -> SECURITY -> behavior -> channel ->
    truth -> tools -> output. Security must precede all behavioral content."""
    body = load_hermes_reply_prompt().body
    order = [
        "<identity>",
        "<material_hierarchy>",
        "<capabilities>",
        "<input_protocol>",
        "<untrusted_observed_content>",
        "<conversation_core>",
        "<telegram_mechanics>",
        "<authority_and_truth>",
        "<tool_protocol>",
        "<output_protocol>",
    ]
    positions = [body.index(tag) for tag in order]
    assert positions == sorted(positions), "prompt sections drifted out of layer order"


def test_prompt_rejects_owner_impersonation():
    flat = " ".join(load_hermes_reply_prompt().body.split())
    assert "claiming to speak for the owner" in flat


def test_prompt_covers_returning_uncontacted_leads():
    # Slice 3: returning/uncontacted leads are handled conversationally from the
    # `handoffs` status (a `stale` handoff -> apologize + promise a fresh follow-up),
    # not by recording a new handoff tool call.
    flat = " ".join(load_hermes_reply_prompt().body.split())
    assert "promise a fresh follow-up" in flat


def test_prompt_bans_templated_escalation_replies():
    flat = " ".join(load_hermes_reply_prompt().body.split())
    assert "add something new" in flat  # each escalation turn must progress


def test_prompt_drops_dead_retrieval_language_and_hardens_single_source():
    """Pilot hardening (2026-06-18): RAG/knowledge tools were removed from the
    interactive lane, so the reply contract must not point the model at
    "knowledge tools", "catalog search results", or an <authority_evidence>
    input that never arrives (dead + misleading). It must make AGENT.md the
    single source of truth and ban turning background facts (a bio, history)
    into a priced/sellable offering — prod run 182: a knowledge_search returned
    Murabbiy's speaker bio, the model promoted it to a "consulting service" and
    invented a price comparison to the real course."""
    body = load_hermes_reply_prompt().body
    flat = " ".join(body.split())
    # dead retrieval language is gone:
    assert "knowledge tools" not in flat
    assert "catalog search results" not in flat
    assert "<authority_evidence>" not in body
    # AGENT.md is the single source of truth, stated plainly:
    assert "AGENT.md is your single source of truth" in flat
    # background/bio is context, never a sellable or priceable offering:
    assert "background" in flat.lower()


def test_prompt_off_catalog_is_honest_uncertainty_not_denial():
    """Two symmetric failures on the same fishing question ('does Murabbiy do
    consulting?'): run 182 invented a POSITIVE (he does, it costs X); after the
    single-source fix, run 186 invented a NEGATIVE (he does NOT do consulting).
    Both are fabrications — the agent only knows its own catalog, not whether a
    person privately consults. The contract must require honest uncertainty +
    escalation for anything AGENT.md doesn't cover: do not confirm AND do not
    deny; say you don't have the info and offer a handoff."""
    flat = " ".join(load_hermes_reply_prompt().body.split())
    assert "answer from those facts directly and confidently" in flat  # positive default for real facts
    assert "do not confirm it and do not deny it" in flat  # honest uncertainty, not a guess either way
    assert "never claim that something outside it exists or does not exist" in flat  # no denial of unknowns
    assert "never a service you sell or a price you quote" in flat  # bio is not an offering
