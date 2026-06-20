import inspect

from app.modules.agent_runtime_v2.hermes.engine import (
    HermesEngineAdapter,
    _live_turn_override,
)


def test_run_accepts_current_turn_media_param():
    sig = inspect.signature(HermesEngineAdapter.run)
    assert "current_turn_media" in sig.parameters
    assert sig.parameters["current_turn_media"].default is None


def test_run_accepts_live_media_text_param():
    sig = inspect.signature(HermesEngineAdapter.run)
    assert "live_media_text" in sig.parameters
    assert sig.parameters["live_media_text"].default is None


# The live user turn is a <turn_context> wrapper (conversation_state +
# <current_message>), NOT a bare string. The boundary swap must keep that wrapper
# and swap ONLY the inner media rendering — replacing the whole turn stripped
# conversation_state and broke perception live (2026-06-13). These tests use the
# REAL wrapper shape that the earlier plain-text tests missed.
def test_live_turn_override_keeps_wrapper_swaps_inner_media():
    turn = (
        '<turn_context>\n'
        '<conversation_state authority="false">{"stage": "engaged"}</conversation_state>\n'
        '<current_message reply_to="message:453">\n'
        '[Voice message: "Hello. How are you?"]\n'
        '</current_message>\n</turn_context>'
    )
    out = _live_turn_override(turn, '[Voice message: "Hello. How are you?"]', "[Voice message]")
    assert out is not None
    # Wrapper + conversation_state framing preserved for the live call.
    assert "<turn_context>" in out
    assert "<conversation_state" in out
    assert "<current_message" in out
    assert '{"stage": "engaged"}' in out
    # Inner media rendering swapped to the bare marker; transcript gone live.
    assert "[Voice message]" in out
    assert "Hello. How are you?" not in out


def test_live_turn_override_none_when_nothing_to_swap():
    # Text-only turn (bare == labeled) -> no override, live == stored.
    assert _live_turn_override("<turn_context>salom</turn_context>", "salom", "salom") is None
    # No live media text.
    assert _live_turn_override("x", "x", None) is None
    # Defensive: customer_message not present in the turn -> do not corrupt it.
    assert (
        _live_turn_override('<turn_context>other</turn_context>',
                            '[Voice message: "x"]', "[Voice message]")
        is None
    )
