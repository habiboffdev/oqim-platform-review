from __future__ import annotations

import cli.commands.agent_cmd as ac


def test_render_tail_shows_bubbles_and_trace(capsys):
    bubbles = [
        {"sender_type": "customer", "content": "salom narxi qancha"},
        {"sender_type": "seller", "content": "Assalomu alaykum! Kurs 9 790 000 so'm..."},
    ]
    latest = {
        "tokens_in": 1180, "llm_calls": 1, "total_latency_ms": 11000,
        "trace_metrics": {"cached_content_tokens": 1015,
                          "token_breakdown": {"raw_input_tokens": 1180},
                          "fallback_calls": 0},
        "output_action": "talk.send_msgs",
        "source_refs": ["ground:a", "ground:b"],
    }
    ac._render_tail(bubbles, latest)
    out = capsys.readouterr().out
    assert "c> salom narxi qancha" in out
    assert out.count("a> ") == 1
    assert "cache=" in out and "ground=2" in out
    assert "\x1b[" not in out


def test_render_tail_without_run(capsys):
    bubbles = [{"sender_type": "customer", "content": "salom"}]
    ac._render_tail(bubbles, None)
    out = capsys.readouterr().out
    assert "c> salom" in out  # no trace line, no crash


def test_agent_tail_registered():
    assert "tail" in {c.name for c in ac.app.registered_commands}


def test_render_tail_full_does_not_truncate(capsys):
    long = "x" * 200
    ac._render_tail([{"sender_type": "customer", "content": long}], None, full=True)
    out = capsys.readouterr().out
    assert "…" not in out and long in out


def test_render_tail_run_without_bubbles(capsys):
    latest = {
        "run_id": "r-9", "tokens_in": 100, "llm_calls": 1, "total_latency_ms": 900,
        "output_action": "talk.send_msgs", "source_refs": [],
        "trace_metrics": {"cached_content_tokens": 0,
                          "token_breakdown": {"raw_input_tokens": 100}, "fallback_calls": 0},
    }
    ac._render_tail([], latest)
    out = capsys.readouterr().out
    assert "turn=r-9" in out
