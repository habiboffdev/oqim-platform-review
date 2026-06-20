from app.brain.llm import _openai_messages_to_gemini_contents
from app.brain.media_parts import TurnMediaPart


def _img(ref: str) -> TurnMediaPart:
    return TurnMediaPart(
        message_ref=ref, kind="vision", mime_type="image/jpeg",
        source="inline", data=b"\xff\xd8\xff",
    )


def test_no_media_is_byte_identical_text():
    msgs = [{"role": "user", "content": "salom"}]
    out = _openai_messages_to_gemini_contents(msgs, None)
    assert len(out) == 1
    assert out[0].role == "user"
    assert len(out[0].parts) == 1
    assert out[0].parts[0].text == "salom"


def test_media_attaches_only_to_last_user_turn():
    # A replayed history user turn, an assistant turn, then the live user turn.
    msgs = [
        {"role": "user", "content": "oldingi xabar"},       # replayed history
        {"role": "assistant", "content": "javob"},
        {"role": "user", "content": "hozirgi xabar"},        # the live turn
    ]
    out = _openai_messages_to_gemini_contents(msgs, [_img("message:99")])
    user_turns = [c for c in out if c.role == "user"]
    # History user turn untouched (text only) -> pay-once.
    assert len(user_turns[0].parts) == 1
    assert user_turns[0].parts[0].text == "oldingi xabar"
    # Live user turn carries text + the image part.
    assert len(user_turns[1].parts) == 2
    assert user_turns[1].parts[0].text == "hozirgi xabar"
    assert user_turns[1].parts[1].inline_data is not None
    assert user_turns[1].parts[1].inline_data.mime_type == "image/jpeg"


def _audio(ref: str) -> TurnMediaPart:
    return TurnMediaPart(
        message_ref=ref, kind="audio", mime_type="audio/ogg",
        source="inline", data=b"OggS\x00\x01",
    )


def test_live_media_text_swaps_live_user_text_only():
    msgs = [
        {"role": "user", "content": "oldingi xabar"},                       # history
        {"role": "assistant", "content": "javob"},
        {"role": "user", "content": '[Voice message: "narx qancha"]'},      # live (labeled)
    ]
    out = _openai_messages_to_gemini_contents(
        msgs, [_audio("message:7")], live_media_text="[Voice message]"
    )
    user_turns = [c for c in out if c.role == "user"]
    # History untouched.
    assert user_turns[0].parts[0].text == "oldingi xabar"
    # Live turn text swapped to the bare marker; transcript gone from the call.
    assert user_turns[1].parts[0].text == "[Voice message]"
    assert user_turns[1].parts[1].inline_data.mime_type == "audio/ogg"


def test_no_live_media_text_keeps_existing_text():
    # Backward-compatible: without the override, behavior is unchanged.
    msgs = [{"role": "user", "content": "hozirgi"}]
    out = _openai_messages_to_gemini_contents(msgs, [_audio("message:7")])
    assert out[0].parts[0].text == "hozirgi"
    assert out[0].parts[1].inline_data is not None
