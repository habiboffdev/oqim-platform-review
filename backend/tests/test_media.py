"""
Media proxy endpoint tests — caching + workspace isolation.
Gateway proxy tests removed — gateway is dead (Issue #69).
"""

import os
from unittest.mock import AsyncMock, patch

import httpx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import media as media_routes
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message
from app.models.workspace import Workspace
from app.services.channel_media_access import (
    ChannelMediaAccess,
    MediaStreamResult,
    MediaUnavailableError,
)
from app.services.channel_sync_runtime import ChannelSyncRateLimitError


class _StreamResponse:
    def __init__(self, *, status_code: int = 200, chunks: list[bytes] | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._chunks = chunks or []
        self.headers = headers or {}
        self.closed = False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class _StreamClient:
    def __init__(self, *, response: _StreamResponse | None = None, send_exc: Exception | None = None):
        self._response = response
        self._send_exc = send_exc
        self.sent_request = None
        self.sent_stream = None
        self.closed = False

    def build_request(self, method: str, url: str, **kwargs):
        return {
            "method": method,
            "url": url,
            **kwargs,
        }

    async def send(self, request, *, stream: bool = False):
        self.sent_request = request
        self.sent_stream = stream
        if self._send_exc is not None:
            raise self._send_exc
        return self._response

    async def aclose(self):
        self.closed = True


async def test_channel_media_access_open_full_stream_does_not_buffer_full_blob():
    """Full media defaults to sidecar streaming so backend avoids whole-blob buffering."""
    sidecar_response = _StreamResponse(
        chunks=[b"abc", b"def"],
        headers={"content-type": "video/mp4", "content-length": "6"},
    )
    mock_http = _StreamClient(response=sidecar_response)
    service = ChannelMediaAccess()

    with patch("app.services.channel_media_access.httpx.AsyncClient", return_value=mock_http):
        result = await service.open_full_stream(
            workspace_id=42,
            chat_id=777,
            message_id=888,
        )
        chunks = [chunk async for chunk in result.stream]

    assert chunks == [b"abc", b"def"]
    assert result.media_type == "video/mp4"
    assert result.cache_control == "private, max-age=3600"
    assert result.content_length == 6
    assert mock_http.sent_stream is True
    assert mock_http.sent_request["json"] == {
        "chatId": "777",
        "messageId": "888",
        "workspaceId": 42,
        "thumb": False,
    }
    assert sidecar_response.closed is True
    assert mock_http.closed is True


async def test_channel_media_access_open_full_stream_forwards_range_without_buffering():
    """Range playback stays stream-first instead of fetching the whole blob for slicing."""
    sidecar_response = _StreamResponse(
        status_code=206,
        chunks=[b"cd", b"ef"],
        headers={
            "content-type": "audio/ogg",
            "content-length": "4",
            "content-range": "bytes 2-5/10",
            "accept-ranges": "bytes",
        },
    )
    mock_http = _StreamClient(response=sidecar_response)
    service = ChannelMediaAccess()

    with patch("app.services.channel_media_access.httpx.AsyncClient", return_value=mock_http):
        result = await service.open_full_stream(
            workspace_id=42,
            chat_id=777,
            message_id=888,
            byte_range="bytes=2-5",
        )
        chunks = [chunk async for chunk in result.stream]

    assert chunks == [b"cd", b"ef"]
    assert result.status_code == 206
    assert result.media_type == "audio/ogg"
    assert result.content_length == 4
    assert result.content_range == "bytes 2-5/10"
    assert result.accept_ranges == "bytes"
    assert mock_http.sent_stream is True
    assert mock_http.sent_request["json"] == {
        "chatId": "777",
        "messageId": "888",
        "workspaceId": 42,
        "thumb": False,
        "byteRange": "bytes=2-5",
    }


def test_channel_media_access_owns_descriptor_repair_policy():
    service = ChannelMediaAccess()

    assert service.message_needs_descriptor_repair(Message(media_type=None)) is False
    assert service.message_needs_descriptor_repair(Message(media_type="contact")) is False
    assert service.message_needs_descriptor_repair(
        Message(media_type="document", media_metadata={})
    ) is True
    assert service.message_needs_descriptor_repair(
        Message(media_type="document", media_metadata={"mime_type": "application/pdf"})
    ) is True
    assert service.message_needs_descriptor_repair(
        Message(
            media_type="document",
            media_metadata={"mime_type": "application/pdf", "file_name": "invoice.pdf"},
        )
    ) is False
    assert service.message_needs_descriptor_repair(
        Message(
            media_type="document",
            media_metadata={
                "mime_type": "video/mp4",
                "file_name": "animation.mp4",
                "is_animated": True,
                "is_video": True,
            },
        )
    ) is True
    assert service.message_needs_descriptor_repair(
        Message(media_type="photo", media_metadata={"width": 640})
    ) is True
    assert service.message_needs_descriptor_repair(
        Message(media_type="photo", media_metadata={"width": 640, "height": 480})
    ) is False


def test_channel_media_access_owns_message_url_policy():
    service = ChannelMediaAccess()
    conversation = Conversation(telegram_chat_id=998877)
    photo = Message(media_type="photo", telegram_message_id=123)
    voice = Message(media_type="voice", telegram_message_id=124)
    location = Message(media_type="location", telegram_message_id=125)

    photo_urls = service.message_urls(conversation=conversation, message=photo)
    assert photo_urls.full_url == "/api/media/998877/123"
    assert photo_urls.preview_url == "/api/media/998877/123?thumb=true"

    voice_urls = service.message_urls(conversation=conversation, message=voice)
    assert voice_urls.full_url == "/api/media/998877/124"
    assert voice_urls.preview_url is None

    location_urls = service.message_urls(conversation=conversation, message=location)
    assert location_urls.full_url is None
    assert location_urls.preview_url is None


class TestMediaProxy:
    """Media proxy endpoint tests."""

    async def test_requires_auth(self, client: AsyncClient):
        """Media endpoint requires authentication."""
        res = await client.get("/api/media/123456789/100")
        assert res.status_code == 401

    async def test_uncached_sidecar_unreachable_returns_502(
        self, client: AsyncClient, auth_headers, conversation: Conversation
    ):
        """Uncached media returns 502 when the sidecar is unavailable."""
        chat_id = conversation.telegram_chat_id
        mock_http = _StreamClient(send_exc=httpx.ConnectError("sidecar offline"))

        with patch("app.services.channel_media_access.httpx.AsyncClient", return_value=mock_http):
            res = await client.get(f"/api/media/{chat_id}/100", headers=auth_headers)
        assert res.status_code == 502

    async def test_cache_hit(
        self, client: AsyncClient, auth_headers, workspace, conversation: Conversation, tmp_path
    ):
        """Returns cached preview file without calling sidecar."""
        chat_id = conversation.telegram_chat_id
        cache_dir = tmp_path / str(workspace.id)
        cache_dir.mkdir(parents=True)
        cache_file = cache_dir / f"{workspace.id}_{chat_id}_100_thumb.jpg"
        cache_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path):
            res = await client.get(f"/api/media/{chat_id}/100?thumb=true", headers=auth_headers)
            assert res.status_code == 200

    async def test_sidecar_request_includes_workspace_id(
        self,
        client: AsyncClient,
        auth_headers,
        workspace: Workspace,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        sidecar_response = _StreamResponse(
            chunks=[b"\xff\xd8\xff\xe0" + b"\x00" * 32],
            headers={"content-type": "image/jpeg", "content-length": "36"},
        )
        mock_http = _StreamClient(response=sidecar_response)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch(
            "app.services.channel_media_access.httpx.AsyncClient",
            return_value=mock_http,
        ):
            res = await client.get(f"/api/media/{chat_id}/101", headers=auth_headers)

        assert res.status_code == 200
        assert mock_http.sent_stream is True
        assert mock_http.sent_request["json"]["workspaceId"] == workspace.id
        assert mock_http.sent_request["json"]["thumb"] is False

    async def test_sidecar_request_for_thumbnail_sets_thumb_flag(
        self,
        client: AsyncClient,
        auth_headers,
        workspace: Workspace,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        sidecar_response = _StreamResponse(
            chunks=[b"\xff\xd8\xff\xe0" + b"\x00" * 32],
            headers={"content-type": "image/jpeg", "content-length": "36"},
        )
        mock_http = _StreamClient(response=sidecar_response)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch(
            "app.services.channel_media_access.httpx.AsyncClient",
            return_value=mock_http,
        ):
            res = await client.get(f"/api/media/{chat_id}/101?thumb=true", headers=auth_headers)

        assert res.status_code == 200
        assert res.content == b"\xff\xd8\xff\xe0" + b"\x00" * 32
        assert mock_http.sent_stream is True
        assert mock_http.sent_request["json"]["workspaceId"] == workspace.id
        assert mock_http.sent_request["json"]["thumb"] is True

    async def test_zero_byte_thumbnail_returns_404(
        self,
        client: AsyncClient,
        auth_headers,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        sidecar_response = _StreamResponse(
            chunks=[],
            headers={"content-type": "application/octet-stream"},
        )
        mock_http = _StreamClient(response=sidecar_response)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch(
            "app.services.channel_media_access.httpx.AsyncClient",
            return_value=mock_http,
        ):
            res = await client.get(f"/api/media/{chat_id}/101?thumb=true", headers=auth_headers)

        assert res.status_code == 404

    async def test_thumbnail_stream_does_not_persist_blob_to_disk(
        self,
        client: AsyncClient,
        auth_headers,
        workspace: Workspace,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        streamed_bytes = b"\xff\xd8\xff\xe0" + b"\x99" * 32
        sidecar_response = _StreamResponse(
            chunks=[streamed_bytes[:8], streamed_bytes[8:]],
            headers={"content-type": "image/jpeg", "content-length": str(len(streamed_bytes))},
        )
        mock_http = _StreamClient(response=sidecar_response)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch(
            "app.services.channel_media_access.httpx.AsyncClient",
            return_value=mock_http,
        ):
            res = await client.get(f"/api/media/{chat_id}/206?thumb=true", headers=auth_headers)

        assert res.status_code == 200
        assert res.content == streamed_bytes
        cache_dir = tmp_path / str(workspace.id)
        assert list(cache_dir.glob(f"{workspace.id}_{chat_id}_206_thumb.*")) == []

    async def test_custom_emoji_proxy_uses_workspace_scoped_preview(
        self,
        client: AsyncClient,
        auth_headers,
        workspace: Workspace,
    ):
        with patch.object(
            media_routes.media_access,
            "open_custom_emoji_preview",
            new=AsyncMock(
                return_value=MediaStreamResult(
                    content=b"RIFF....WEBP",
                    media_type="image/webp",
                    cache_control="private, max-age=86400",
                    content_length=12,
                )
            ),
        ) as mocked_open:
            res = await client.get("/api/media/custom-emoji/123456789", headers=auth_headers)

        assert res.status_code == 200
        assert res.headers["content-type"].startswith("image/webp")
        mocked_open.assert_awaited_once_with(
            workspace_id=workspace.id,
            document_id="123456789",
        )

    async def test_uncached_full_media_does_not_persist_blob_to_disk(
        self,
        client: AsyncClient,
        auth_headers,
        workspace: Workspace,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        sidecar_response = _StreamResponse(
            chunks=[b"\xff\xd8\xff\xe0" + b"\x00" * 32],
            headers={"content-type": "image/jpeg", "content-length": "36"},
        )
        mock_http = _StreamClient(response=sidecar_response)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch(
            "app.services.channel_media_access.httpx.AsyncClient",
            return_value=mock_http,
        ):
            res = await client.get(f"/api/media/{chat_id}/102", headers=auth_headers)

        assert res.status_code == 200
        assert mock_http.sent_stream is True
        cache_dir = tmp_path / str(workspace.id)
        assert list(cache_dir.glob(f"{workspace.id}_{chat_id}_102.*")) == []

    async def test_full_media_range_request_returns_partial_content(
        self,
        client: AsyncClient,
        auth_headers,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        sidecar_response = _StreamResponse(
            status_code=206,
            chunks=[b"cd", b"ef"],
            headers={
                "content-type": "audio/ogg",
                "content-length": "4",
                "content-range": "bytes 2-5/10",
                "accept-ranges": "bytes",
            },
        )
        mock_http = _StreamClient(response=sidecar_response)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch(
            "app.services.channel_media_access.httpx.AsyncClient",
            return_value=mock_http,
        ):
            res = await client.get(
                f"/api/media/{chat_id}/103",
                headers={**auth_headers, "Range": "bytes=2-5"},
            )

        assert res.status_code == 206
        assert res.content == b"cdef"
        assert res.headers["accept-ranges"] == "bytes"
        assert res.headers["content-range"] == "bytes 2-5/10"
        assert res.headers["content-length"] == "4"
        assert mock_http.sent_stream is True
        assert mock_http.sent_request["json"]["byteRange"] == "bytes=2-5"

    async def test_full_media_open_ended_range_returns_tail_content(
        self,
        client: AsyncClient,
        auth_headers,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        sidecar_response = _StreamResponse(
            status_code=206,
            chunks=[b"cdef", b"ghij"],
            headers={
                "content-type": "video/mp4",
                "content-length": "8",
                "content-range": "bytes 2-9/10",
                "accept-ranges": "bytes",
            },
        )
        mock_http = _StreamClient(response=sidecar_response)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch(
            "app.services.channel_media_access.httpx.AsyncClient",
            return_value=mock_http,
        ):
            res = await client.get(
                f"/api/media/{chat_id}/103",
                headers={**auth_headers, "Range": "bytes=2-"},
            )

        assert res.status_code == 206
        assert res.content == b"cdefghij"
        assert res.headers["accept-ranges"] == "bytes"
        assert res.headers["content-range"] == "bytes 2-9/10"
        assert res.headers["content-length"] == "8"
        assert mock_http.sent_stream is True
        assert mock_http.sent_request["json"]["byteRange"] == "bytes=2-"

    async def test_invalid_full_media_range_returns_416(
        self,
        client: AsyncClient,
        auth_headers,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        sidecar_response = _StreamResponse(status_code=416)
        mock_http = _StreamClient(response=sidecar_response)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch(
            "app.services.channel_media_access.httpx.AsyncClient",
            return_value=mock_http,
        ):
            res = await client.get(
                f"/api/media/{chat_id}/104",
                headers={**auth_headers, "Range": "bytes=20-30"},
            )

        assert res.status_code == 416
        assert mock_http.sent_stream is True
        assert mock_http.sent_request["json"]["byteRange"] == "bytes=20-30"

    async def test_stale_preview_cache_is_ignored_and_streamed_from_source(
        self,
        client: AsyncClient,
        auth_headers,
        workspace: Workspace,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        cache_dir = tmp_path / str(workspace.id)
        cache_dir.mkdir(parents=True)
        stale = cache_dir / f"{workspace.id}_{chat_id}_204_thumb.jpg"
        stale.write_bytes(b"old-preview")
        os.utime(stale, (1, 1))

        fresh_bytes = b"\xff\xd8\xff\xe0" + b"\x99" * 32
        sidecar_response = _StreamResponse(
            chunks=[fresh_bytes],
            headers={"content-type": "image/jpeg", "content-length": str(len(fresh_bytes))},
        )
        mock_http = _StreamClient(response=sidecar_response)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch(
            "app.services.channel_media_access.PREVIEW_CACHE_TTL_SECONDS",
            10,
        ), patch(
            "app.services.channel_media_access.time.time",
            return_value=100,
        ), patch(
            "app.services.channel_media_access.httpx.AsyncClient",
            return_value=mock_http,
        ):
            res = await client.get(f"/api/media/{chat_id}/204?thumb=true", headers=auth_headers)

        assert res.status_code == 200
        assert res.content == fresh_bytes
        assert stale.exists() is False
        assert list(cache_dir.glob(f"{workspace.id}_{chat_id}_204_thumb.*")) == []

    async def test_zero_byte_cached_preview_is_ignored_and_streamed_from_source(
        self,
        client: AsyncClient,
        auth_headers,
        workspace: Workspace,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        cache_dir = tmp_path / str(workspace.id)
        cache_dir.mkdir(parents=True)
        broken = cache_dir / f"{workspace.id}_{chat_id}_205_thumb.jpg"
        broken.write_bytes(b"")

        fresh_bytes = b"\xff\xd8\xff\xe0" + b"\x11" * 32
        sidecar_response = _StreamResponse(
            chunks=[fresh_bytes],
            headers={"content-type": "image/jpeg", "content-length": str(len(fresh_bytes))},
        )
        mock_http = _StreamClient(response=sidecar_response)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch(
            "app.services.channel_media_access.httpx.AsyncClient",
            return_value=mock_http,
        ):
            res = await client.get(f"/api/media/{chat_id}/205?thumb=true", headers=auth_headers)

        assert res.status_code == 200
        assert broken.exists() is False
        assert list(cache_dir.glob(f"{workspace.id}_{chat_id}_205_thumb.*")) == []

    async def test_video_note_thumbnail_uses_derived_frame_preview(
        self,
        client: AsyncClient,
        auth_headers,
        db_session: AsyncSession,
        workspace: Workspace,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        message = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="[video_note]",
            media_type="video_note",
            telegram_message_id=207,
        )
        db_session.add(message)
        await db_session.flush()

        derived_bytes = b"\xff\xd8\xff\xe0" + b"\x55" * 32
        fetch_mock = AsyncMock(return_value=(b"fake-video", "video/mp4"))
        extract_mock = AsyncMock(return_value=derived_bytes)

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch.object(
            media_routes.media_access,
            "_fetch_from_source",
            new=fetch_mock,
        ), patch.object(
            media_routes.media_access,
            "_extract_video_note_preview",
            new=extract_mock,
        ):
            res = await client.get(f"/api/media/{chat_id}/207?thumb=true", headers=auth_headers)

        assert res.status_code == 200
        assert res.content == derived_bytes
        fetch_mock.assert_awaited_once()
        assert fetch_mock.await_args.kwargs["thumb"] is False
        extract_mock.assert_awaited_once()
        assert (
            tmp_path / str(workspace.id) / f"{workspace.id}_{chat_id}_207_thumb_vnote.jpg"
        ).exists()

    async def test_video_note_thumbnail_prefers_cached_derived_preview(
        self,
        client: AsyncClient,
        auth_headers,
        db_session: AsyncSession,
        workspace: Workspace,
        conversation: Conversation,
        tmp_path,
    ):
        chat_id = conversation.telegram_chat_id
        message = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="[video_note]",
            media_type="video_note",
            telegram_message_id=208,
        )
        db_session.add(message)
        await db_session.flush()

        cache_dir = tmp_path / str(workspace.id)
        cache_dir.mkdir(parents=True)
        derived_preview = cache_dir / f"{workspace.id}_{chat_id}_208_thumb_vnote.jpg"
        derived_preview.write_bytes(b"\xff\xd8\xff\xe0" + b"\x77" * 32)

        fetch_mock = AsyncMock()
        extract_mock = AsyncMock()

        with patch("app.services.channel_media_access.MEDIA_CACHE_DIR", tmp_path), patch.object(
            media_routes.media_access,
            "_fetch_from_source",
            new=fetch_mock,
        ), patch.object(
            media_routes.media_access,
            "_extract_video_note_preview",
            new=extract_mock,
        ):
            res = await client.get(f"/api/media/{chat_id}/208?thumb=true", headers=auth_headers)

        assert res.status_code == 200
        assert res.content == b"\xff\xd8\xff\xe0" + b"\x77" * 32
        fetch_mock.assert_not_awaited()
        extract_mock.assert_not_awaited()


class TestMediaIsolation:
    async def test_media_rejects_other_workspace_chat(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        auth_headers_b: dict,
    ):
        """M7: media proxy must verify chat_id belongs to the authenticated workspace."""
        customer = Customer(workspace_id=workspace.id, display_name="Test")
        db_session.add(customer)
        await db_session.flush()

        conv = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            telegram_chat_id=88888,
        )
        db_session.add(conv)
        await db_session.flush()

        res = await client.get(
            f"/api/media/{conv.telegram_chat_id}/1",
            headers=auth_headers_b,
        )
        assert res.status_code == 404


class TestAiMediaHydration:
    async def test_photo_hydration_updates_message_for_ai(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        message = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="[photo] Mijoz rasm yubordi",
            media_type="photo",
            telegram_message_id=777,
            external_message_id="777",
        )
        db_session.add(message)
        await db_session.commit()

        service = ChannelMediaAccess()
        with (
            patch.object(
                service,
                "_fetch_from_source",
                new=AsyncMock(return_value=(b"fake-jpeg", "image/jpeg")),
            ),
            patch("app.modules.extraction_runtime.media_semantics.normalize_image_message") as mock_normalize,
        ):
            async def _normalize(_bytes, _mime, **_kwargs):
                from app.modules.extraction_runtime.media_semantics import NormalizedMessage

                return NormalizedMessage(
                    text="[photo] Red iPhone case",
                    confidence=0.91,
                    original_type="photo",
                )

            mock_normalize.side_effect = _normalize
            result = await service.hydrate_for_ai(
                session=db_session,
                workspace_id=workspace.id,
                conversation=conversation,
                message=message,
            )

        refreshed = await db_session.get(Message, message.id)
        assert refreshed.content == "[photo] Red iPhone case"
        assert refreshed.media_description == "[photo] Red iPhone case"
        assert refreshed.media_url == f"/api/media/{conversation.telegram_chat_id}/{message.telegram_message_id}"
        assert refreshed.media_metadata["hydrated"] is True
        assert refreshed.media_metadata["hydration_status"] == "hydrated"
        assert refreshed.media_metadata["mime_type"] == "image/jpeg"
        assert refreshed.media_metadata["normalized_text"] == "[photo] Red iPhone case"
        assert result.status == "hydrated"
        assert result.media_bytes_b64 is not None
        assert result.media_mime_type == "image/jpeg"
        assert result.normalized_text == "[photo] Red iPhone case"

    async def test_hydrate_for_ai_returns_empty_when_media_unavailable(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        message = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="[voice] Mijoz ovoz yubordi",
            media_type="voice",
            telegram_message_id=778,
            external_message_id="778",
        )
        db_session.add(message)
        await db_session.commit()

        service = ChannelMediaAccess()
        with patch.object(
            service,
            "_fetch_from_source",
            new=AsyncMock(side_effect=MediaUnavailableError),
        ):
            result = await service.hydrate_for_ai(
                session=db_session,
                workspace_id=workspace.id,
                conversation=conversation,
                message=message,
            )

        refreshed = await db_session.get(Message, message.id)
        assert result.status == "unavailable"
        assert result.media_bytes_b64 is None
        assert result.media_mime_type is None
        assert result.normalized_text is None
        assert refreshed.transcription is None
        assert refreshed.media_metadata["hydration_status"] == "unavailable"

    async def test_hydrate_for_ai_persists_deferred_status_when_rate_limited(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        message = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="[voice] Mijoz ovoz yubordi",
            media_type="voice",
            telegram_message_id=779,
            external_message_id="779",
        )
        db_session.add(message)
        await db_session.commit()

        service = ChannelMediaAccess()
        with patch.object(
            service,
            "_fetch_from_source",
            new=AsyncMock(
                side_effect=ChannelSyncRateLimitError(
                    retry_after_seconds=12.5,
                    channel="telegram_dm",
                    operation="media",
                )
            ),
        ):
            result = await service.hydrate_for_ai(
                session=db_session,
                workspace_id=workspace.id,
                conversation=conversation,
                message=message,
            )

        refreshed = await db_session.get(Message, message.id)
        assert result.status == "deferred"
        assert result.retry_after_seconds == 12.5
        assert result.media_bytes_b64 is None
        assert result.normalized_text is None
        assert refreshed.media_metadata["hydration_status"] == "deferred"
        assert refreshed.media_metadata["retry_after_seconds"] == 12.5


class TestPreviewCacheSnapshot:
    def test_preview_cache_snapshot_reports_cross_workspace_usage(self, tmp_path):
        service = ChannelMediaAccess()
        cache_dir_a = tmp_path / "1"
        cache_dir_b = tmp_path / "2"
        cache_dir_a.mkdir(parents=True)
        cache_dir_b.mkdir(parents=True)
        (cache_dir_a / "1_10_11_thumb.jpg").write_bytes(b"abc")
        (cache_dir_b / "2_20_21_thumb.jpg").write_bytes(b"defgh")

        snapshot = service.preview_cache_snapshot(tmp_path)

        assert snapshot.file_count == 2
        assert snapshot.total_bytes == 8
        assert snapshot.workspace_count == 2
