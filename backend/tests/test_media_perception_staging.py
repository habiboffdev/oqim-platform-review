import inspect

import pytest

from app.modules.agent_runtime_v2 import media_perception as mp

pytestmark = pytest.mark.asyncio


class _Msg:
    def __init__(self, mid, media_type, media_metadata=None):
        self.id = mid
        self.media_type = media_type
        self.media_metadata = media_metadata or {}


def _reader(mapping):
    def read(workspace_id, message_id):
        return mapping.get((workspace_id, message_id))
    return read


async def test_photo_message_stages_vision_part_from_cache():
    parts = await mp.stage_turn_media(
        [_Msg(42, "photo", {"mime_type": "image/png"})],
        workspace_id=1, chat_id=555, channel="telegram_dm",
        read_bytes=_reader({(1, 42): b"IMG"}),
    )
    assert len(parts) == 1
    p = parts[0]
    assert p.kind == "vision" and p.mime_type == "image/png"
    assert p.source == "inline" and p.data == b"IMG"
    assert p.message_ref == "message:42"


async def test_voice_message_stages_audio_with_default_mime():
    parts = await mp.stage_turn_media(
        [_Msg(7, "voice")],  # no mime recorded -> default audio/ogg
        workspace_id=1, chat_id=555, channel="telegram_dm",
        read_bytes=_reader({(1, 7): b"OGG"}),
    )
    assert len(parts) == 1 and parts[0].kind == "audio"
    assert parts[0].mime_type == "audio/ogg" and parts[0].data == b"OGG"


async def test_static_sticker_staged_as_vision():
    parts = await mp.stage_turn_media(
        [_Msg(8, "sticker", {"mime_type": "image/webp"})],
        workspace_id=1, chat_id=555, channel="telegram_dm",
        read_bytes=_reader({(1, 8): b"WEBP"}),
    )
    assert len(parts) == 1 and parts[0].kind == "vision"
    assert parts[0].mime_type == "image/webp" and parts[0].data == b"WEBP"


async def test_animated_sticker_skipped_non_image_mime():
    # .tgs sniffs as application/gzip -> not a vision image -> text fallback.
    parts = await mp.stage_turn_media(
        [_Msg(13, "sticker", {"mime_type": "application/gzip"})],
        workspace_id=1, chat_id=555, channel="telegram_dm",
        read_bytes=_reader({(1, 13): b"TGS"}),
    )
    assert parts == []


async def test_non_telegram_channel_degrades_to_nothing():
    parts = await mp.stage_turn_media(
        [_Msg(9, "photo")],
        workspace_id=1, chat_id=555, channel="instagram_dm",
        read_bytes=_reader({(1, 9): b"IMG"}),
    )
    assert parts == []


async def test_cache_miss_degrades_gracefully():
    # No cached bytes (e.g. hydration not finished / different VM) -> text fallback.
    parts = await mp.stage_turn_media(
        [_Msg(10, "photo")],
        workspace_id=1, chat_id=555, channel="telegram_dm",
        read_bytes=_reader({}),
    )
    assert parts == []


async def test_oversize_media_is_skipped():
    big = b"x" * (mp.MAX_INLINE_BYTES + 1)
    parts = await mp.stage_turn_media(
        [_Msg(11, "voice")],
        workspace_id=1, chat_id=555, channel="telegram_dm",
        read_bytes=_reader({(1, 11): big}),
    )
    assert parts == []


def test_agent_turn_context_carries_live_media_text():
    import dataclasses
    from app.modules.agent_runtime_v2.runtime_service import _AgentTurnContext

    fields = {f.name for f in dataclasses.fields(_AgentTurnContext)}
    assert "live_media_text" in fields


def test_gather_turn_context_accepts_live_media_text():
    from app.modules.agent_runtime_v2.runtime_service import AgentRuntimeService

    sig = inspect.signature(AgentRuntimeService.gather_turn_context)
    assert "live_media_text" in sig.parameters
    assert sig.parameters["live_media_text"].default is None
