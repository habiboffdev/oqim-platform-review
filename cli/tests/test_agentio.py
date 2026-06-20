from __future__ import annotations

from cli.agentio import bubble, dur, emit, kv, money, pct, tokens


def test_humanizers():
    assert tokens(1180) == "1180"
    assert tokens(10_000) == "10k"
    assert tokens(210_000) == "210k"
    assert tokens(999_999) == "1.0M"
    assert tokens(1_240_000) == "1.2M"
    assert money(0) == "$0"
    assert money(0.00019) == "$0.00019"
    assert money(4.2) == "$4.20"
    assert pct(0.864) == "86%"
    assert dur(11300) == "11s"
    assert dur(1240) == "1.2s"
    assert dur(820) == "820ms"


def test_kv_drops_none_and_keeps_order():
    assert kv(cost="$0.0002", in_=1180, skip=None, cache="86%") == "cost=$0.0002 in=1180 cache=86%"


def test_bubble_truncates():
    assert bubble("customer", "salom") == "c> salom"
    long = "x" * 200
    out = bubble("seller", long, max_chars=10)
    assert out.startswith("a> ") and out.endswith("…") and len(out) <= 3 + 10 + 1


def test_emit_terse_by_default(capsys):
    emit({"cost": "$0.0002", "in": 1180}, json_mode=False, render=lambda r: print(kv(**r)))
    out = capsys.readouterr().out
    assert out.strip() == "cost=$0.0002 in=1180"
    assert "\x1b[" not in out  # no ANSI escapes


def test_emit_json_is_compact(capsys):
    emit({"a": 1, "b": [1, 2]}, json_mode=True, render=lambda r: None)
    out = capsys.readouterr().out.strip()
    assert out == '{"a": 1, "b": [1, 2]}'  # no indent


def test_emit_json_via_global_flag(capsys):
    import cli.agentio as agentio

    agentio.OUTPUT_JSON = True
    try:
        emit({"x": 1}, json_mode=False, render=lambda r: print("terse"))
    finally:
        agentio.OUTPUT_JSON = False
    assert capsys.readouterr().out.strip() == '{"x": 1}'
