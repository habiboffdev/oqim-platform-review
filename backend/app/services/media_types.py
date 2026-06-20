from __future__ import annotations

_GRAMJS_MEDIA_TYPE_MAP = {
    "MessageMediaPhoto": "photo",
    "MessageMediaDocument": "document",
    "MessageMediaContact": "contact",
    "MessageMediaGeo": "location",
    "MessageMediaGeoLive": "live_location",
    "MessageMediaPoll": "poll",
    "MessageMediaVenue": "location",
}


def normalize_media_type(value: str | None, media_metadata: dict | None = None) -> str | None:
    if not value:
        return None

    normalized = _GRAMJS_MEDIA_TYPE_MAP.get(value, value)
    lowered = normalized.lower()

    if lowered in {
        "photo",
        "video",
        "gif",
        "sticker",
        "voice",
        "video_note",
        "audio",
        "document",
        "contact",
        "location",
        "live_location",
        "poll",
        "venue",
        "link",
    }:
        inferred = _infer_document_media_kind(lowered, media_metadata)
        return inferred

    return normalized


def _infer_document_media_kind(
    media_type: str,
    media_metadata: dict | None,
) -> str:
    if media_type != "document" or not isinstance(media_metadata, dict):
        return media_type

    mime = str(media_metadata.get("mime_type") or "").lower()
    file_name = str(media_metadata.get("file_name") or "").lower()
    emoji = media_metadata.get("emoji")
    is_video = bool(media_metadata.get("is_video"))
    is_animated = bool(media_metadata.get("is_animated"))
    is_round = bool(media_metadata.get("is_round")) or bool(media_metadata.get("length"))

    if (
        mime in {"application/x-tgsticker", "application/vnd.ms-tgsticker"}
        or file_name.endswith(".tgs")
        or (mime == "video/webm" and (emoji or "sticker" in file_name or is_video))
        or (mime == "image/webp" and (emoji or "sticker" in file_name or is_animated))
    ):
        return "sticker"

    if mime == "image/gif" or file_name.endswith(".gif"):
        return "gif"

    if mime.startswith("image/"):
        return "photo"

    if mime.startswith("video/") and is_round:
        return "video_note"

    if is_animated and mime in {"video/mp4", "video/webm"}:
        return "gif"

    if mime.startswith("video/") and is_video:
        return "video"

    if mime.startswith("audio/"):
        if media_metadata.get("waveform"):
            return "voice"
        return "audio"

    return media_type
