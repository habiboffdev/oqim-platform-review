from app.brain.media_parts import TurnMediaPart, to_gemini_part


def test_inline_part_becomes_from_bytes():
    part = TurnMediaPart(
        message_ref="message:42",
        kind="vision",
        mime_type="image/jpeg",
        source="inline",
        data=b"\xff\xd8\xff",
    )
    gp = to_gemini_part(part)
    assert gp.inline_data is not None
    assert gp.inline_data.mime_type == "image/jpeg"
    assert gp.inline_data.data == b"\xff\xd8\xff"


def test_file_uri_part_becomes_from_uri():
    part = TurnMediaPart(
        message_ref="message:43",
        kind="vision",
        mime_type="video/mp4",
        source="file_uri",
        file_uri="https://generativelanguage.googleapis.com/v1beta/files/abc",
    )
    gp = to_gemini_part(part)
    assert gp.file_data is not None
    assert gp.file_data.file_uri.endswith("/files/abc")
    assert gp.file_data.mime_type == "video/mp4"
