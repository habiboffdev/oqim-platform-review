from app.services.media_types import normalize_media_type


def test_round_video_documents_normalize_to_video_note() -> None:
    assert normalize_media_type(
        "MessageMediaDocument",
        {
            "mime_type": "video/mp4",
            "is_video": True,
            "is_round": True,
        },
    ) == "video_note"


def test_regular_video_documents_stay_video() -> None:
    assert normalize_media_type(
        "MessageMediaDocument",
        {
            "mime_type": "video/mp4",
            "is_video": True,
        },
    ) == "video"


def test_animated_mp4_documents_normalize_to_gif() -> None:
    assert normalize_media_type(
        "MessageMediaDocument",
        {
            "mime_type": "video/mp4",
            "file_name": "animation.mp4",
            "is_video": True,
            "is_animated": True,
        },
    ) == "gif"


def test_gif_documents_normalize_to_gif() -> None:
    assert normalize_media_type(
        "MessageMediaDocument",
        {
            "mime_type": "image/gif",
            "file_name": "promo.gif",
        },
    ) == "gif"


def test_legacy_round_video_documents_normalize_to_video_note_from_length() -> None:
    assert normalize_media_type(
        "MessageMediaDocument",
        {
            "mime_type": "video/mp4",
            "is_video": True,
            "length": 240,
        },
    ) == "video_note"
