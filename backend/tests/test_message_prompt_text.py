from app.modules.agent_runtime_v2.prompt_text import message_prompt_text


class _Msg:
    def __init__(self, content, media_type=None):
        self.content = content
        self.media_type = media_type


def test_returns_trimmed_content_when_present():
    assert message_prompt_text(_Msg("  salom  ")) == "salom"


def test_falls_back_to_media_type_placeholder():
    assert message_prompt_text(_Msg("", "voice")) == "[voice]"


def test_empty_when_no_content_no_media():
    assert message_prompt_text(_Msg("")) == ""


def test_native_voice_renders_labeled_transcript():
    # Labeled so the model knows it was SPOKEN (audio is attached separately as a
    # native part), and the labeled transcript is what persists in history.
    out = message_prompt_text(_Msg("narx qancha", "voice"), native_media=True)
    assert out == '[Voice message: "narx qancha"]'


def test_native_photo_renders_labeled_description():
    out = message_prompt_text(_Msg("a desk", "photo"), native_media=True)
    assert out == '[Photo: "a desk"]'


def test_native_voice_without_transcript_falls_back_to_marker():
    out = message_prompt_text(_Msg("", "voice"), native_media=True)
    assert out == "[Voice message]"


def test_native_false_returns_transcript():
    assert message_prompt_text(_Msg("Yodalarimni aytdim", "voice")) == "Yodalarimni aytdim"


def test_native_ignored_for_text_message():
    assert message_prompt_text(_Msg("salom", None), native_media=True) == "salom"


def test_native_voice_bare_drops_transcript():
    # The live Gemini call must NOT see the transcript next to the audio (the
    # text-dominance confound). Bare marker only; the audio Part carries meaning.
    out = message_prompt_text(_Msg("narx qancha", "voice"), native_media=True, bare=True)
    assert out == "[Voice message]"


def test_native_photo_bare_drops_description():
    out = message_prompt_text(_Msg("a desk", "photo"), native_media=True, bare=True)
    assert out == "[Photo]"


def test_bare_ignored_without_native_media():
    # bare only applies to staged native media; plain text is unaffected.
    assert message_prompt_text(_Msg("salom", None), bare=True) == "salom"


def test_burst_prompt_text_bare_renders_marker_for_staged():
    from app.modules.agent_runtime_v2.dispatcher import _burst_prompt_text

    class _M:
        def __init__(self, id, content, media_type):
            self.id = id
            self.content = content
            self.media_type = media_type

    voice = _M(7, "narx qancha", "voice")
    # Staged (id in native_media_ids) + bare -> marker only, no transcript.
    out = _burst_prompt_text([voice], voice, native_media_ids={7}, bare=True)
    assert out == "[Voice message]"
    # Same burst, labeled (session) rendering keeps the transcript.
    labeled = _burst_prompt_text([voice], voice, native_media_ids={7})
    assert labeled == '[Voice message: "narx qancha"]'
