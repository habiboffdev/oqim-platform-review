from __future__ import annotations

import json

from app.modules.agent_runtime_v2.hermes.talk_tools import (
    register_talk_tools,
    talk_delete_message,
    talk_reply_to_msg,
    talk_send_media,
    talk_send_msg,
    talk_send_msgs,
)
from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext, use_tool_context
from app.modules.agent_talking.contracts import TalkBundle, TalkingPolicy


def _ctx() -> ToolContext:
    bundle = TalkBundle(
        workspace_id=1,
        agent_id=2,
        hermes_run_id="hermes_run:talk-tools",
        conversation_id=3,
        channel_account_id="telegram:1",
        actions=[],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )
    return ToolContext(
        workspace_id=1,
        agent_id=2,
        conversation_id=3,
        grounding=[],
        history=[],
        talk_bundle=bundle,
    )


def test_talk_send_msg_appends_action_to_context_bundle() -> None:
    ctx = _ctx()

    with use_tool_context(ctx):
        result = json.loads(talk_send_msg({"text": "Salom"}))

    assert result == {"status": "queued", "action_kind": "send_msg", "action_index": 0}
    assert ctx.talk_bundle is not None
    assert ctx.talk_bundle.actions[0].kind == "send_msg"
    assert ctx.talk_bundle.actions[0].text == "Salom"


def test_talk_send_msg_blocks_without_bundle() -> None:
    ctx = ToolContext(
        workspace_id=1,
        agent_id=2,
        conversation_id=3,
        grounding=[],
        history=[],
    )

    with use_tool_context(ctx):
        result = json.loads(talk_send_msg({"text": "Salom"}))

    assert result["status"] == "blocked"
    assert result["reason"] == "no_talk_bundle"


def test_talk_send_msgs_queues_separate_telegram_bubbles() -> None:
    ctx = _ctx()

    with use_tool_context(ctx):
        result = json.loads(
            talk_send_msgs(
                {
                    "bubbles": [
                        "ha, platforma haqida aytaman",
                        "narxi bo'yicha aniq tasdiqlangan ma'lumot bo'lmasa taxmin qilmayman",
                    ]
                }
            )
        )

    assert result == {
        "status": "queued",
        "action_kind": "send_msg_batch",
        "action_indexes": [0, 1],
        "queued_count": 2,
    }
    assert ctx.talk_bundle is not None
    assert [action.kind for action in ctx.talk_bundle.actions] == [
        "send_msg",
        "send_msg",
    ]
    assert [action.text for action in ctx.talk_bundle.actions] == [
        "ha, platforma haqida aytaman",
        "narxi bo'yicha aniq tasdiqlangan ma'lumot bo'lmasa taxmin qilmayman",
    ]


def test_talk_send_msgs_can_anchor_each_bubble_to_message_ref() -> None:
    ctx = _ctx()

    with use_tool_context(ctx):
        result = json.loads(
            talk_send_msgs(
                {
                    "bubbles": [
                        {"text": "ha, bor", "reply_message_id": "message:44"},
                        {
                            "text": "qaysi bo'limga tayyorlanyapsiz?",
                            "reply_message_id": "message:45",
                        },
                    ],
                }
            )
        )

    assert result["status"] == "queued"
    assert ctx.talk_bundle is not None
    assert [action.kind for action in ctx.talk_bundle.actions] == [
        "reply_to_msg",
        "reply_to_msg",
    ]
    assert ctx.talk_bundle.actions[0].target_message_ref == "message:44"
    assert ctx.talk_bundle.actions[1].target_message_ref == "message:45"


def test_talk_send_msgs_respects_remaining_bubble_budget() -> None:
    """Over-budget requests clip to the remaining budget (with a warning)
    instead of refusing the whole reply — a hard block costs another LLM
    round-trip or, with terminal talk tools, the reply itself."""
    ctx = _ctx()
    assert ctx.talk_bundle is not None
    ctx.talk_bundle.talking_policy_snapshot.max_bubbles_per_turn = 2
    with use_tool_context(ctx):
        json.loads(talk_send_msg({"text": "birinchi"}))
        result = json.loads(
            talk_send_msgs({"bubbles": ["ikkinchi", "uchinchi"]})
        )

    assert result["status"] == "queued"
    assert result["queued_count"] == 1
    assert [action.text for action in ctx.talk_bundle.actions] == ["birinchi", "ikkinchi"]
    assert "talking_policy_bubbles_clipped" in ctx.authority_warnings

    # a turn with NO remaining budget still blocks outright
    with use_tool_context(ctx):
        result = json.loads(talk_send_msgs({"bubbles": ["to'rtinchi"]}))
    assert result["status"] == "blocked"
    assert result["reason"] == "max_bubbles_exceeded"


def test_talk_send_media_appends_media_action() -> None:
    ctx = _ctx()

    with use_tool_context(ctx):
        result = json.loads(
            talk_send_media({"media_ref": "catalog_media:1", "caption": "Mana rasmi"})
        )

    assert result["status"] == "queued"
    assert ctx.talk_bundle is not None
    assert ctx.talk_bundle.actions[0].kind == "send_media"
    assert ctx.talk_bundle.actions[0].media_ref == "catalog_media:1"
    assert ctx.talk_bundle.actions[0].text == "Mana rasmi"


def test_delete_message_is_blocked_by_default_policy() -> None:
    ctx = _ctx()

    with use_tool_context(ctx):
        result = json.loads(talk_delete_message({"target_message_ref": "message:1"}))

    assert result["status"] == "blocked"
    assert result["reason"] == "talking_policy_delete_disabled"
    assert "talking_policy_delete_disabled" in ctx.authority_warnings


def test_register_talk_tools_idempotent() -> None:
    from tools.registry import registry

    register_talk_tools()
    register_talk_tools()
    names = registry.get_tool_names() if hasattr(registry, "get_tool_names") else []
    if names:
        assert "talk.send_msg" in names
        assert "talk.send_msgs" in names
        assert "talk.send_media" in names


def test_send_reaction_schema_is_prescriptive_for_acknowledgements() -> None:
    from app.modules.agent_runtime_v2.hermes import talk_tools

    description = talk_tools._SEND_REACTION_SCHEMA["description"]
    # Reactions are a normal move, not a buried option: the schema itself
    # tells the model when a reaction IS the reply.
    assert "pure social acknowledgements" in description
    assert "phone number" in description


def test_send_msgs_schema_coaches_one_idea_and_voice_owned_length() -> None:
    from app.modules.agent_runtime_v2.hermes import talk_tools

    description = talk_tools._SEND_MSGS_SCHEMA["description"]
    # one idea per bubble + the anti-multi-beat rule stay; the hardcoded ~180
    # cap is gone (length is owned by the agent's voice/AGENT.md), and the
    # tool description must be em-dash free (the model imitates the form).
    assert "one clear idea" in description
    assert "the agent's voice and AGENT.md" in description
    assert "Do not pack multiple beats" in description
    assert "~180" not in description
    assert "—" not in description


def test_talk_send_msgs_splits_blank_line_beats_into_separate_bubbles() -> None:
    """The live ~280-char two-beat bubble (2026-06-10) must never ship again."""
    ctx = _ctx()
    packed = (
        "Hozircha yangi ma'lumot yo'q, aka. Operatorlarimiz ro'yxat asosida "
        "bog'lanishmoqda, navbatingiz kelganda albatta chiqishadi.\n\n"
        "Kutish jarayonida kurs dasturi yoki tashkiliy masalalar bo'yicha yana "
        "savollaringiz bo'lsa, yozib qoldiring, javob berishga harakat qilaman."
    )

    with use_tool_context(ctx):
        result = json.loads(talk_send_msgs({"bubbles": [{"text": packed}]}))

    assert result["status"] == "queued"
    assert result["queued_count"] == 2
    cap = ctx.talk_bundle.talking_policy_snapshot.max_chars_per_bubble
    for action in ctx.talk_bundle.actions:
        assert len(action.text) <= cap
        assert "\n\n" not in action.text


def test_talk_send_msgs_splits_overlong_beat_at_sentence_boundaries() -> None:
    ctx = _ctx()
    long_text = " ".join(
        f"Bu {i}-jumla bo'lib, mijozga yetarlicha uzun tushuntirish beradi." for i in range(6)
    )
    assert len(long_text) > 220

    with use_tool_context(ctx):
        result = json.loads(talk_send_msgs({"bubbles": [{"text": long_text}]}))

    assert result["status"] == "queued"
    cap = ctx.talk_bundle.talking_policy_snapshot.max_chars_per_bubble
    assert all(len(action.text) <= cap for action in ctx.talk_bundle.actions)
    # nothing lost: rejoining the parts reproduces every sentence
    rejoined = " ".join(action.text for action in ctx.talk_bundle.actions)
    assert rejoined == long_text


def test_talk_send_msgs_clips_to_bubble_budget_instead_of_refusing() -> None:
    ctx = _ctx()
    ctx.talk_bundle.talking_policy_snapshot.max_bubbles_per_turn = 2
    packed = "Birinchi beat.\n\nIkkinchi beat.\n\nUchinchi beat."

    with use_tool_context(ctx):
        result = json.loads(talk_send_msgs({"bubbles": [{"text": packed}]}))

    assert result["status"] == "queued"
    assert result["queued_count"] == 2
    assert "talking_policy_bubbles_clipped" in ctx.authority_warnings


def test_send_msg_strips_em_dash() -> None:
    ctx = _ctx()
    with use_tool_context(ctx):
        talk_send_msg({"text": "Salom — aka"})
    assert ctx.talk_bundle is not None
    assert ctx.talk_bundle.actions[0].text == "Salom, aka"


def test_send_msgs_strips_em_dash_and_keeps_number_hyphens() -> None:
    ctx = _ctx()
    with use_tool_context(ctx):
        talk_send_msgs(
            {"bubbles": [{"text": "Maqsad — HR sohasiga, 5-7 mln dan 10-20 mln+"}]}
        )
    assert ctx.talk_bundle is not None
    joined = " ".join(a.text for a in ctx.talk_bundle.actions)
    assert joined, "no bubbles queued"
    assert "—" not in joined
    assert "Maqsad, HR sohasiga" in joined
    assert "5-7 mln" in joined  # number hyphens preserved


def test_reply_to_msg_strips_em_dash() -> None:
    ctx = _ctx()
    with use_tool_context(ctx):
        talk_reply_to_msg({"text": "Ha — bor", "target_message_ref": "message:44"})
    assert ctx.talk_bundle is not None
    assert ctx.talk_bundle.actions[0].kind == "reply_to_msg"
    assert ctx.talk_bundle.actions[0].target_message_ref == "message:44"
    assert ctx.talk_bundle.actions[0].text == "Ha, bor"


def test_talk_send_msgs_drops_exact_duplicate_bubble_in_same_turn() -> None:
    """A later loop iteration that re-emits an identical bubble (forced talk
    via mode=ANY with nothing new to say) must NOT ship a second identical
    Telegram message. Live 2026-06-13: run 120 closed in iteration 1 (ack +
    work.handoff), then iteration 3 re-emitted the byte-identical ack and both
    shipped (msgs 399 == 400)."""
    ctx = _ctx()
    ack = "Rahmat, raqamingizni qabul qildim. Operatorimiz tez orada bog'lanadi."
    with use_tool_context(ctx):
        first = json.loads(talk_send_msgs({"bubbles": [ack]}))
        second = json.loads(talk_send_msgs({"bubbles": [ack]}))

    assert first["status"] == "queued"
    assert first["queued_count"] == 1
    # the identical re-emission is dropped, not queued again
    assert second["status"] == "skipped"
    assert second["reason"] == "duplicate_bubble"
    assert second["queued_count"] == 0
    assert ctx.talk_bundle is not None
    texts = [a.text for a in ctx.talk_bundle.actions]
    assert texts == [ack], f"duplicate bubble shipped: {texts}"


def test_talk_send_msg_drops_exact_duplicate_bubble_in_same_turn() -> None:
    """The single-bubble entry point dedupes against the same turn too."""
    ctx = _ctx()
    with use_tool_context(ctx):
        first = json.loads(talk_send_msg({"text": "Rahmat, qabul qildim."}))
        second = json.loads(talk_send_msg({"text": "Rahmat, qabul qildim."}))

    assert first["status"] == "queued"
    assert second["status"] == "skipped"
    assert second["reason"] == "duplicate_bubble"
    assert ctx.talk_bundle is not None
    assert [a.text for a in ctx.talk_bundle.actions] == ["Rahmat, qabul qildim."]


def test_talk_send_msgs_keeps_new_bubbles_and_drops_only_the_repeat() -> None:
    """A mixed batch queues the genuinely-new bubble and drops only the repeat
    (over-dedup must not swallow distinct content)."""
    ctx = _ctx()
    with use_tool_context(ctx):
        json.loads(talk_send_msg({"text": "Narxi 9 790 000 so'm."}))
        result = json.loads(
            talk_send_msgs(
                {"bubbles": ["Narxi 9 790 000 so'm.", "Ism va telefon qoldiring."]}
            )
        )

    assert result["status"] == "queued"
    assert result["queued_count"] == 1
    assert ctx.talk_bundle is not None
    assert [a.text for a in ctx.talk_bundle.actions] == [
        "Narxi 9 790 000 so'm.",
        "Ism va telefon qoldiring.",
    ]
