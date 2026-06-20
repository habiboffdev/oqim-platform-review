from __future__ import annotations

import cli.commands.metrics_cmd as mc


def _row(**over):
    base = {
        "run_id": "r-1", "started_at": "2026-06-13T14:22:00",
        "tokens_in": 1180, "tokens_out": 240, "llm_calls": 1,
        "total_latency_ms": 12300,
        "trace_metrics": {
            "cache_effective_input_tokens": 165,
            "cached_content_tokens": 1015,
            "fallback_calls": 0,
            "token_breakdown": {"raw_input_tokens": 1180, "output_tokens": 240,
                                "cached_content_tokens": 1015},
        },
    }
    base.update(over)
    return base


def test_estimate_cost_uses_cached_discount():
    cost = mc._estimate_cost(raw_in=1180, cached=1015, out=240)
    assert cost > 0
    assert cost < mc._estimate_cost(raw_in=1180, cached=0, out=240)


def test_aggregate_reports_last_24h_worst():
    rows = [
        _row(run_id="r-1", tokens_in=1180, tokens_out=240),
        _row(run_id="r-2", tokens_in=33000, tokens_out=300,
             trace_metrics={"cache_effective_input_tokens": 33000, "cached_content_tokens": 0,
                            "fallback_calls": 0, "token_breakdown": {}}),
    ]
    agg = mc._aggregate(rows)
    assert agg["last"]["run_id"] == "r-1"
    assert agg["window"]["turns"] == 2
    assert agg["window"]["fallbacks"] == 0
    assert agg["worst"]["run_id"] == "r-2"


def test_render_is_terse(capsys):
    rows = [_row()]
    mc._render(mc._aggregate(rows))
    out = capsys.readouterr().out
    assert out.startswith("last ")
    assert "cache=" in out and "in=1180" in out
    assert "\x1b[" not in out


def test_render_empty_window(capsys):
    mc._render(mc._aggregate([]))
    assert capsys.readouterr().out.strip() == "no runs in window"


def test_capped_flag_surfaces_note(capsys):
    agg = mc._aggregate([_row()], capped=True)
    assert agg["capped"] is True
    mc._render(agg)
    assert "capped at 500 runs" in capsys.readouterr().out


def test_metrics_command_registered():
    import cli.app as appmod
    assert "metrics" in {c.name for c in appmod.app.registered_commands}
