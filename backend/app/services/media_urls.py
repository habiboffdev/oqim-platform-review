from __future__ import annotations

NON_DOWNLOADABLE_MEDIA_TYPES = frozenset(
    {
        "contact",
        "link",
        "live_location",
        "location",
        "poll",
        "venue",
    }
)

PREVIEWABLE_MEDIA_TYPES = frozenset(
    {
        "photo",
        "video",
        "video_note",
        "sticker",
        "gif",
    }
)


def build_message_media_url(
    telegram_chat_id: int | None,
    telegram_message_id: int | None,
    media_type: str | None,
) -> str | None:
    if (
        not telegram_chat_id
        or not telegram_message_id
        or not media_type
        or media_type in NON_DOWNLOADABLE_MEDIA_TYPES
    ):
        return None
    return f"/api/media/{telegram_chat_id}/{telegram_message_id}"


def build_message_media_preview_url(
    telegram_chat_id: int | None,
    telegram_message_id: int | None,
    media_type: str | None,
) -> str | None:
    full_url = build_message_media_url(
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        media_type=media_type,
    )
    if not full_url or media_type not in PREVIEWABLE_MEDIA_TYPES:
        return None
    return f"{full_url}?thumb=true"


def canonicalize_message_media_url(
    media_url: str | None,
    telegram_chat_id: int | None,
    telegram_message_id: int | None,
    media_type: str | None,
) -> str | None:
    canonical_url = build_message_media_url(
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        media_type=media_type,
    )
    if canonical_url is None:
        return None
    if media_url != canonical_url:
        return canonical_url
    return media_url
