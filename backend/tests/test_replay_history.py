from __future__ import annotations

import json
from types import SimpleNamespace

from app.modules.agent_runtime_v2.hermes.engine import _build_replay_history


def test_build_replay_history_normalizes_content_and_tool_call_args():
    sid = "oqim:agent-session:1"
    args = json.dumps({"bubbles": [{"text": "Salom — aka"}]}, ensure_ascii=False)
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "savol — bormi"},
        {
            "role": "assistant",
            "content": "Maqsad — HR sohasiga",
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "talk.send_msgs", "arguments": args}}
            ],
        },
        {"role": "tool", "content": "yuborildi — ok"},
    ]
    out = _build_replay_history(SimpleNamespace(messages={sid: msgs}), sid)

    assert all(m.get("role") != "system" for m in out)  # system stripped
    by_role = {m["role"]: m for m in out}
    # assistant content normalized
    assert by_role["assistant"]["content"] == "Maqsad, HR sohasiga"
    # tool-call arguments normalized AND still valid JSON
    new_args = by_role["assistant"]["tool_calls"][0]["function"]["arguments"]
    assert "—" not in new_args
    assert json.loads(new_args)["bubbles"][0]["text"] == "Salom, aka"
    # tool result content normalized
    assert by_role["tool"]["content"] == "yuborildi, ok"
    # customer (user) turn left verbatim
    assert by_role["user"]["content"] == "savol — bormi"


def test_build_replay_history_is_not_length_capped():
    sid = "oqim:agent-session:2"
    msgs = [{"role": "user", "content": f"u{i}"} for i in range(40)]
    out = _build_replay_history(SimpleNamespace(messages={sid: msgs}), sid)
    assert len(out) == 40  # length is Hermes's job; no cap here


def test_build_replay_history_empty_when_no_session():
    assert _build_replay_history(None, "sid") == []
    assert _build_replay_history(SimpleNamespace(messages={}), None) == []
    assert _build_replay_history(SimpleNamespace(messages={}), "missing") == []


def test_build_replay_history_drops_set_state_bookkeeping_turns():
    """Replay hygiene: the forced commercial-finalization pass appends a
    conversation.set_state assistant tool call + its tool result to the session.
    That bookkeeping exchange must NOT pollute the NEXT customer turn's replay —
    but legitimate customer (user) and talk turns must survive untouched."""
    sid = "oqim:agent-session:9"
    set_state_args = json.dumps(
        {"stage": "qualified", "shown_prices": [{"amount": 250000}]},
        ensure_ascii=False,
    )
    msgs = [
        {"role": "user", "content": "Narxi qancha?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "t1", "type": "function",
                 "function": {"name": "talk.send_msgs",
                              "arguments": json.dumps({"bubbles": [{"text": "250 000 so'm"}]})}}
            ],
        },
        {"role": "tool", "tool_name": "talk.send_msgs", "tool_call_id": "t1",
         "content": "yuborildi"},
        # the forced finalize pass — must be stripped:
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "s1", "type": "function",
                 "function": {"name": "conversation.set_state", "arguments": set_state_args}}
            ],
        },
        {"role": "tool", "tool_name": "conversation.set_state", "tool_call_id": "s1",
         "content": json.dumps({"status": "ok"})},
    ]
    out = _build_replay_history(SimpleNamespace(messages={sid: msgs}), sid)

    blob = json.dumps(out, ensure_ascii=False)
    # the set_state bookkeeping turn (assistant call + tool result) is gone
    assert "conversation.set_state" not in blob
    # the legitimate customer + talk turns survive
    assert any(m["role"] == "user" and m["content"] == "Narxi qancha?" for m in out)
    assert any(
        m["role"] == "assistant"
        and any(
            (tc.get("function") or {}).get("name") == "talk.send_msgs"
            for tc in (m.get("tool_calls") or [])
        )
        for m in out
    )
    assert any(m["role"] == "tool" and m.get("tool_name") == "talk.send_msgs" for m in out)


def test_build_replay_history_drops_set_state_flat_tool_call_shape():
    """run_agent stores assistant tool_calls flat as {name, arguments} (not nested
    under `function`). The set_state filter must handle that stored shape too."""
    sid = "oqim:agent-session:10"
    msgs = [
        {"role": "user", "content": "Salom"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"name": "conversation.set_state",
                 "arguments": json.dumps({"stage": "new"})}
            ],
        },
        {"role": "tool", "tool_name": "conversation.set_state", "tool_call_id": "s2",
         "content": "{}"},
    ]
    out = _build_replay_history(SimpleNamespace(messages={sid: msgs}), sid)

    assert "conversation.set_state" not in json.dumps(out, ensure_ascii=False)
    assert [m["role"] for m in out] == ["user"]
