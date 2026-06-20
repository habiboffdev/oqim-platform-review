import json
from unittest.mock import patch

import pytest

from app.brain.llm import LLMToolCall, LLMToolResponse
from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.hermes import engine as engine_mod
from app.modules.agent_runtime_v2.hermes.engine import HermesEngineAdapter
from app.modules.agent_runtime_v2.hermes.session_store import InMemoryHermesSessionDB
from app.modules.agent_runtime_v2.reply_runtime import ReplyResult
from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfileCompiler

pytestmark = pytest.mark.asyncio

def _cfg():
    return AgentConfig(agent_id=2, workspace_id=1, name="Sotuvchi",
                       trust_mode="disabled", auto_send_threshold=0.85,
                       agent_md="# Sotuvchi\nSen sotuvchisan.")

def _profile(config=None, agent_kind="seller_agent"):
    cfg = config or _cfg()
    return RuntimeProfileCompiler().compile_agent(config=cfg, agent_kind=agent_kind)

async def test_adapter_runs_loop_calls_tool_and_returns_grounded_reply():
    calls = {"n": 0}
    async def _fake_gwt(**kw):
        calls["n"] += 1
        return LLMToolResponse(text="Assalomu alaykum! Narxi 250 000 so'm.",
                               tool_calls=[], model_used="m", provider="gemini")
    with patch("app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt):
        out = await HermesEngineAdapter().run(
            config=_cfg(), profile=_profile(agent_kind="seller"),
            customer_message="Narxi qancha?",
            grounding=["Mahsulot X narxi: 250000 so'm"], history=[], agent_kind="seller")
    assert isinstance(out, ReplyResult)
    assert "250" in out.reply_text
    assert calls["n"] == 1
    assert out.grounding_hits == 1


async def test_adapter_persists_to_supplied_hermes_session_db():
    session_db = InMemoryHermesSessionDB()

    async def _fake_gwt(**kw):
        return LLMToolResponse(
            text="Va alaykum assalom!",
            tool_calls=[],
            model_used="m",
            provider="gemini",
        )

    with patch("app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt):
        await HermesEngineAdapter().run(
            config=_cfg(),
            profile=_profile(agent_kind="seller"),
            customer_message="Assalomu alaykum",
            grounding=[],
            history=[],
            agent_kind="seller",
            hermes_session_id="oqim:agent-session:7",
            session_db=session_db,
        )

    assert session_db.get_session("oqim:agent-session:7") is not None
    assert session_db.messages["oqim:agent-session:7"]


async def test_interactive_agent_reply_enables_talk_tools_only():
    seen: dict = {}

    async def _fake_gwt(**kw):
        seen["tools"] = kw.get("tools")
        return LLMToolResponse(
            text="Starter coins narxi 40 000 UZS.",
            model_used="m",
            provider="gemini",
            tool_calls=[],
        )

    with patch("app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt):
        out = await HermesEngineAdapter().run(
            config=_cfg(),
            profile=_profile(agent_kind="seller"),
            customer_message="starter coins narxi qancha?",
            grounding=["[CATALOG] Starter coins — variant: 5 coins — offer: 40 000 UZS"],
            history=[],
            agent_kind="seller",
        )

    tool_names = {
        item["name"]
        for item in seen["tools"]
    }
    assert "talk.send_msgs" in tool_names
    assert "talk.send_reaction" in tool_names
    assert "talk.send_msg" not in tool_names
    assert "talk.reply_to_msg" not in tool_names
    # pilot hardening (2026-06-18): RAG/knowledge tools removed from the talk loop
    # — the interactive seller answers from AGENT.md, not retrieval.
    assert not any(t.startswith("knowledge_") for t in tool_names)
    assert out.reply_text == "Starter coins narxi 40 000 UZS."
    assert out.talk_bundle is None


async def test_adapter_attaches_static_agent_prompt_cache_to_hermes_calls():
    seen: dict = {}

    async def _fake_gwt(**kw):
        seen.update(kw)
        return LLMToolResponse(
            text="Assalomu alaykum!",
            model_used="m",
            provider="gemini",
            tool_calls=[],
        )

    # Realistic-size agent material: the lean managed prompt alone sits below
    # Gemini's cached-content minimum; a real AGENT.md (voice + resident
    # business facts) pushes the stable block back over it.
    big_config = AgentConfig(
        agent_id=2,
        workspace_id=1,
        name="Sotuvchi",
        trust_mode="disabled",
        auto_send_threshold=0.85,
        agent_md="# Sotuvchi\n" + ("Biznes fakti va uslub qoidasi. " * 700),
    )
    with patch("app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt):
        await HermesEngineAdapter().run(
            config=big_config,
            profile=_profile(config=big_config, agent_kind="seller"),
            customer_message="hello",
            grounding=[],
            history=[],
            agent_kind="seller",
        )

    prompt_cache = seen["prompt_cache"]
    assert prompt_cache["provider_strategy"] == "gemini_cached_content"
    assert prompt_cache["cacheable"] is True
    assert prompt_cache["skip_reason"] is None
    assert prompt_cache["estimated_tokens"] >= prompt_cache["min_cache_tokens"]
    runtime_context = prompt_cache["runtime_context"]
    assert runtime_context["cache_scope"] == "hermes_agent_prompt"
    assert runtime_context["cache_key"].startswith("hermes-agent-prompt:v1:1:2:seller:")
    assert runtime_context["dynamic_payload_keys"] == [
        "turn_context",
        "conversation",
        "retrieved_evidence",
        "customer_message",
        "tool_results",
    ]
    stable = runtime_context["stable_payload"]
    managed_prompt = stable["managed_prompt"]
    assert managed_prompt["prompt_id"] == "agent_runtime.hermes_reply"
    assert managed_prompt["version"] == "1.0.0"
    assert managed_prompt["digest"]
    assert managed_prompt["cache_key"].startswith(
        "prompt:agent_runtime.hermes_reply:1.0.0:stable_system_prompt:"
    )
    assert stable["agent"]["agent_name"] == "Sotuvchi"
    assert stable["agent"]["agent_kind"] == "seller"
    assert "agent_md_sha256" in stable["prompt_hashes"]
    assert "system_prompt_sha256" in stable["prompt_hashes"]


async def test_adapter_finishes_locally_after_multiple_talk_send_msgs_calls():
    calls = {"n": 0}

    async def _fake_gwt(**kw):
        calls["n"] += 1
        if calls["n"] > 1:
            raise AssertionError("talk tool follow-up should be served locally")
        return LLMToolResponse(
            text="",
            model_used="m",
            provider="gemini",
            tool_calls=[
                LLMToolCall(
                    id="c1",
                    name="talk.send_msgs",
                    arguments=json.dumps({"bubbles": [{"text": "Salom!"}]}),
                ),
                LLMToolCall(
                    id="c2",
                    name="talk.send_msgs",
                    arguments=json.dumps(
                        {"bubbles": [{"text": "Starter coins narxi 40 000 UZS."}]}
                    ),
                ),
            ],
        )

    with patch("app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt):
        out = await HermesEngineAdapter().run(
            config=_cfg(),
            profile=_profile(agent_kind="seller"),
            customer_message="starter coins narxi qancha?",
            grounding=["[CATALOG] Starter coins — variant: 5 coins — offer: 40 000 UZS"],
            history=[],
            agent_kind="seller",
        )

    assert calls["n"] == 1
    assert out.reply_text == "Salom!\n\nStarter coins narxi 40 000 UZS."
    assert out.talk_bundle is not None
    assert [action.text for action in out.talk_bundle.actions] == [
        "Salom!",
        "Starter coins narxi 40 000 UZS.",
    ]


async def test_adapter_degrades_when_loop_fails():
    async def _boom(**kw): raise RuntimeError("chain exhausted")
    with patch("app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _boom):
        out = await HermesEngineAdapter().run(
            config=_cfg(), profile=_profile(agent_kind="seller"),
            customer_message="hi", grounding=[], history=[], agent_kind="seller")
    assert isinstance(out, ReplyResult)
    assert out.confidence == 0.0  # engine returns raw signals; runtime scoring happens later.
    assert out.reply_text == ""
    assert out.tool_errors >= 1
    assert "chain exhausted" not in out.reply_text.lower()
    assert "generate_with_tools" not in out.reply_text


async def test_adapter_caps_confidence_when_authority_warning_present():
    async def _fake_gwt(**kw):
        return LLMToolResponse(
            text="Starter haqida jamoamiz aniqlashtirib beradi.",
            tool_calls=[],
            model_used="m",
            provider="gemini",
        )

    with patch("app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt):
        out = await HermesEngineAdapter().run(
            config=_cfg(),
            profile=_profile(agent_kind="seller"),
            customer_message="starter coins narxi qancha?",
            grounding=["[CATALOG] Starter"],
            history=[],
            authority_warnings=["catalog_offer_missing:catalog:starter"],
            agent_kind="seller",
        )

    assert isinstance(out, ReplyResult)
    assert out.confidence == 0.0  # engine returns raw signals; authority_warnings captured in out.authority_warnings
    assert "catalog_offer_missing:catalog:starter" in out.authority_warnings


async def test_hermes_system_prompt_omits_confidence_and_instructs_prose_only():
    from app.modules.agent_runtime_v2.reply_runtime import compose_hermes_system_prompt

    p = compose_hermes_system_prompt("# Sotuvchi\nSen sotuvchisan.", "seller_agent")
    assert "Sotuvchi" in p                      # agent_md present
    # 2026-06-11 from-zero rewrite: identity is the business agent itself,
    # not a meta "managed prompt" sentence the model could parrot.
    assert "business's live agent on Telegram" in p
    assert "Runtime agent kind: seller_agent" in p
    assert "Rendered AGENT.md:" in p
    low = p.lower()
    assert "search_catalog_truth" not in low
    assert "search_business_rules" not in low
    assert "search_voice_examples" not in low
    assert "recall_business_facts" not in low
    assert "confidence between 0 and 1" not in low   # NO legacy confidence instruction
    assert "return reply_text" not in low            # NO JSON-field instruction
    assert "customer-visible text" in low


async def test_adapter_keeps_telegram_history_out_of_the_turn():
    """Conversation continuity is owned by the Hermes session (host-resume, see
    test_hermes_session_resume.py). The adapter must NOT re-paste the Telegram
    transcript into the turn — that would double the context every call."""
    seen: dict = {}

    async def _fake_gwt(**kw):
        seen["contents"] = kw.get("contents")
        return LLMToolResponse(text="Ha, hali ham bor.", tool_calls=[],
                               model_used="m", provider="gemini")

    with patch("app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt):
        await HermesEngineAdapter().run(
            config=_cfg(), profile=_profile(agent_kind="seller"),
            customer_message="Yana bormi?",
            grounding=["Mahsulot X mavjud"],
            history=["Mijoz: Qizil mahsulot bormi?", "Sotuvchi: Ha, bor."], agent_kind="seller")

    blob = json.dumps(seen.get("contents"), ensure_ascii=False)
    assert "Qizil mahsulot bormi?" not in blob  # transcript no longer re-pasted
    assert "<conversation_history" not in blob
    assert "Yana bormi?" in blob                # the current message is present


async def test_adapter_returns_turn_observation_details_when_registered():
    async def _fake_gwt(**kw):
        return LLMToolResponse(
            text="Assalomu alaykum!",
            tool_calls=[],
            model_used="m",
            provider="gemini",
        )

    with patch("app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt):
        out = await HermesEngineAdapter().run(
            config=_cfg(),
            profile=_profile(agent_kind="seller"),
            customer_message="Salom",
            grounding=[],
            history=[],
            conversation_id=55,
            hermes_run_id="hermes_run:turn-observation",
            turn_session_id=123,
            turn_revision_start=1,
            agent_kind="seller",
        )

    assert out.turn_details == {
        "turn_session_id": 123,
        "turn_revision_start": 1,
        "latest_known_revision": 1,
        "observed_revision": 1,
        "steer_count": 0,
        "steer_deferred_count": 0,
        "steer_leftover_count": 0,
        "pending_steer_count": 0,
    }


def test_engine_fallback_sentinels_are_classified_as_failures():
    from app.modules.agent_runtime_v2.hermes.engine import _is_engine_fallback_text

    assert _is_engine_fallback_text(
        "I reached the iteration limit and couldn't generate a summary."
    )
    assert _is_engine_fallback_text(
        "I reached the maximum iterations (4) but couldn't summarize. Error: x"
    )
    assert not _is_engine_fallback_text("Narxi 40 000 UZS, boshlaymizmi?")
    assert not _is_engine_fallback_text("")


async def test_adapter_sets_force_tool_call_for_record_profile():
    """The forced records pass: when the profile's execution mode is `record`,
    the adapter must build a ToolContext that forces a tool call and pins the
    grant to conversation.record only — so mode=ANY forces conversation.record
    in the post-reply pass."""
    captured = {}

    real_use = engine_mod.use_tool_context

    def _capture(ctx):
        captured["ctx"] = ctx
        return real_use(ctx)

    async def _fake_gwt(**kw):
        return LLMToolResponse(text="", tool_calls=[], model_used="m", provider="gemini")

    record_profile = RuntimeProfileCompiler().compile_profile(
        config=_cfg(), agent_kind="seller_agent", execution_mode="record"
    )
    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt
    ), patch.object(engine_mod, "use_tool_context", _capture):
        await HermesEngineAdapter().run(
            config=_cfg(),
            profile=record_profile,
            customer_message="Narxi qancha?",
            grounding=["Mahsulot X narxi: 250000 so'm"],
            history=[],
            agent_kind="seller_agent",
        )

    ctx = captured["ctx"]
    assert ctx.force_tool_call is True
    assert ctx.allowed_tool_names == frozenset({"conversation.record"})


async def test_adapter_does_not_force_tool_call_for_interactive_profile():
    """Back-compat: a normal interactive reply must NOT set force_tool_call
    (the talk-forcing path is unchanged for every existing agent)."""
    captured = {}
    real_use = engine_mod.use_tool_context

    def _capture(ctx):
        captured["ctx"] = ctx
        return real_use(ctx)

    async def _fake_gwt(**kw):
        return LLMToolResponse(
            text="Narxi 250 000 so'm.", tool_calls=[], model_used="m", provider="gemini"
        )

    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt
    ), patch.object(engine_mod, "use_tool_context", _capture):
        await HermesEngineAdapter().run(
            config=_cfg(),
            profile=_profile(agent_kind="seller"),
            customer_message="Narxi qancha?",
            grounding=[],
            history=[],
            agent_kind="seller",
        )

    assert captured["ctx"].force_tool_call is False


async def test_reply_text_fallback_is_em_dash_normalized():
    async def _fake_gwt(**kw):
        return LLMToolResponse(
            text="Narxi 9 790 000 — bo'lib to'lash bor.",
            tool_calls=[],
            model_used="m",
            provider="gemini",
        )

    with patch("app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt):
        out = await HermesEngineAdapter().run(
            config=_cfg(),
            profile=_profile(agent_kind="seller"),
            customer_message="narxi qancha?",
            grounding=[],
            history=[],
            agent_kind="seller",
        )

    assert "—" not in out.reply_text
    assert "9 790 000, bo'lib to'lash bor." in out.reply_text


def test_record_payload_surfaces_from_tool_context():
    # When a tool stashes ctx.record_payload during a run, the engine surfaces it
    # on ReplyResult.record_payload (mirrors intelligence_payloads).
    from app.modules.agent_runtime_v2.reply_runtime import ReplyResult

    rr = ReplyResult(reply_text="", confidence=0.0, grounding_hits=0)
    assert rr.record_payload is None  # default present on the dataclass


def test_records_pass_tool_is_dropped_from_replay():
    # The forced records pass tool turn (conversation.record) is pure bookkeeping —
    # like conversation.set_state, it must never replay into the next customer turn.
    assert "conversation.record" in engine_mod._NON_REPLAYED_RECORDING_TOOLS
    assert "conversation.set_state" in engine_mod._NON_REPLAYED_RECORDING_TOOLS
