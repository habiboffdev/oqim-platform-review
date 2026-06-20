from __future__ import annotations

import asyncio
import base64
import mimetypes
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.message import Message
from app.services.channel_adapter_contract import (
    ChannelMediaRangeNotSatisfiable,
    ChannelMediaRef,
    ChannelMediaSourceUnavailable,
    ChannelMediaUnavailable,
    TelegramChannelAdapter,
)
from app.services.channel_sync_runtime import ChannelSyncRateLimitError
from app.services.media_perception_cache import write_perception_bytes
from app.services.media_runtime import MEDIA_ACTION_FAILED, update_media_runtime_after_hydration
from app.services.media_types import normalize_media_type
from app.services.media_urls import (
    build_message_media_preview_url,
    build_message_media_url,
    canonicalize_message_media_url,
)

MEDIA_CACHE_DIR = Path("./media_cache")
PREVIEW_CACHE_MAX_FILES_PER_WORKSPACE = 200
PREVIEW_CACHE_TTL_SECONDS = 60 * 60 * 24
PREVIEW_CACHE_MAX_BYTES_GLOBAL = 512 * 1024 * 1024
VIDEO_NOTE_PREVIEW_CACHE_SUFFIX = "thumb_vnote"

logger = get_logger("services.channel_media_access")

_MAGIC_BYTES = [
    (b"RIFF", 0, b"WEBP", 8, "image/webp"),
    (b"\xff\xd8\xff", 0, None, 0, "image/jpeg"),
    (b"\x89PNG", 0, None, 0, "image/png"),
    (b"GIF8", 0, None, 0, "image/gif"),
    (b"\x1a\x45\xdf\xa3", 0, None, 0, "video/webm"),
    (b"OggS", 0, None, 0, "audio/ogg"),
    (b"\x00\x00\x00", 0, b"ftyp", 4, "video/mp4"),
    (b"\x1f\x8b", 0, None, 0, "application/gzip"),
]


class MediaUnavailableError(Exception):
    pass


class MediaSourceUnavailableError(Exception):
    pass


class InvalidRangeError(Exception):
    pass


@dataclass(slots=True)
class MediaStreamResult:
    content: bytes | None
    media_type: str
    cache_control: str
    status_code: int = 200
    content_length: int | None = None
    content_range: str | None = None
    accept_ranges: str | None = None
    cached_path: Path | None = None


@dataclass(slots=True)
class MediaHydrationResult:
    status: str
    media_bytes_b64: str | None
    media_mime_type: str | None
    normalized_text: str | None
    retry_after_seconds: float | None = None


@dataclass(slots=True)
class MediaLiveStreamResult:
    media_type: str
    cache_control: str
    stream: AsyncIterator[bytes]
    status_code: int = 200
    content_length: int | None = None
    content_range: str | None = None
    accept_ranges: str | None = None


@dataclass(slots=True)
class PreviewCacheSnapshot:
    file_count: int
    total_bytes: int
    workspace_count: int


@dataclass(slots=True)
class MessageMediaUrls:
    full_url: str | None
    preview_url: str | None


_AI_RELEVANT_MEDIA_TYPES = {"voice", "audio", "photo", "video", "video_note", "sticker"}
_UNSUPPORTED_SEMANTIC_MEDIA_TYPES = {"gif"}
_SEMANTIC_READY_STATUSES = {"hydrated"}
_SEMANTIC_RETRYING_STATUSES = {"deferred", "retrying"}
_SEMANTIC_UNAVAILABLE_STATUSES = {"unavailable", "failed", "expired", "unsupported"}
_DESCRIPTOR_OPTIONAL_MEDIA_TYPES = {
    "contact",
    "link",
    "live_location",
    "location",
    "poll",
    "venue",
}


def build_media_runtime_metadata(
    *,
    media_type: str | None,
    content: str | None,
    media_metadata: dict | None,
    transcription: str | None = None,
    media_description: str | None = None,
    hydration_status: str | None = None,
    retry_after_seconds: float | None = None,
) -> dict | None:
    if not media_type:
        return media_metadata

    metadata = dict(media_metadata or {})
    normalized_text = transcription or media_description
    ai_relevant = media_type in _AI_RELEVANT_MEDIA_TYPES
    descriptor_text = (normalized_text or (content or "").strip()) or None
    if hydration_status is None:
        if metadata.get("hydrated") or normalized_text:
            hydration_status = "hydrated"
        elif media_type in _UNSUPPORTED_SEMANTIC_MEDIA_TYPES:
            hydration_status = "unsupported"
        else:
            hydration_status = "pending" if ai_relevant else "not_applicable"

    metadata["ai_relevant"] = ai_relevant
    metadata["hydration_status"] = hydration_status
    if descriptor_text:
        metadata["descriptor_text"] = descriptor_text
    if normalized_text:
        metadata["normalized_text"] = normalized_text
    elif "normalized_text" in metadata:
        metadata.pop("normalized_text", None)

    if retry_after_seconds is not None:
        metadata["retry_after_seconds"] = round(float(retry_after_seconds), 3)
    else:
        metadata.pop("retry_after_seconds", None)

    metadata["media_runtime"] = {
        "asset_state": _asset_state_for_status(hydration_status),
        "semantic_state": _semantic_state_for_status(
            hydration_status=hydration_status,
            ai_relevant=ai_relevant,
        ),
        "ai_relevant": ai_relevant,
    }
    return metadata


def _asset_state_for_status(hydration_status: str) -> str:
    if hydration_status == "unsupported":
        return "unsupported"
    if hydration_status in _SEMANTIC_UNAVAILABLE_STATUSES:
        return "unavailable"
    if hydration_status in _SEMANTIC_RETRYING_STATUSES:
        return "retrying"
    if hydration_status in _SEMANTIC_READY_STATUSES:
        return "stream_ready"
    return "metadata_only"


def _semantic_state_for_status(*, hydration_status: str, ai_relevant: bool) -> str:
    if not ai_relevant:
        return "not_applicable"
    if hydration_status in _SEMANTIC_READY_STATUSES:
        return "ready"
    if hydration_status in _SEMANTIC_RETRYING_STATUSES:
        return "retrying"
    if hydration_status in _SEMANTIC_UNAVAILABLE_STATUSES:
        return "unavailable"
    return "pending"


def sniff_content_type(data: bytes) -> str:
    for sig, offset, sig2, offset2, mime in _MAGIC_BYTES:
        if len(data) > offset + len(sig) and data[offset:offset + len(sig)] == sig:
            if sig2 is not None:
                if len(data) > offset2 + len(sig2) and data[offset2:offset2 + len(sig2)] == sig2:
                    return mime
            else:
                return mime
    return "application/octet-stream"


def _stash_perception_bytes(workspace_id: int, message: Message, data: bytes) -> None:
    """Cache freshly-downloaded bytes for AI-relevant media so the reply turn can
    perceive it natively without a second sidecar fetch. Best-effort."""
    if message.media_type in _AI_RELEVANT_MEDIA_TYPES:
        write_perception_bytes(workspace_id, int(message.id), data)


class ChannelMediaAccess:
    def _telegram_adapter(self) -> TelegramChannelAdapter:
        settings = get_settings()
        return TelegramChannelAdapter(
            sidecar_url=settings.sidecar_url,
            sidecar_api_key=settings.sidecar_api_key,
            http_client_factory=httpx.AsyncClient,
        )

    @staticmethod
    def message_urls(
        *,
        conversation: Conversation,
        message: Message,
    ) -> MessageMediaUrls:
        media_type = normalize_media_type(message.media_type, message.media_metadata)
        full_url = build_message_media_url(
            telegram_chat_id=conversation.telegram_chat_id,
            telegram_message_id=message.telegram_message_id,
            media_type=media_type,
        )
        return MessageMediaUrls(
            full_url=full_url,
            preview_url=build_message_media_preview_url(
                telegram_chat_id=conversation.telegram_chat_id,
                telegram_message_id=message.telegram_message_id,
                media_type=media_type,
            ),
        )

    @staticmethod
    def message_needs_descriptor_repair(message: Message) -> bool:
        if (
            not message.media_type
            or message.media_type in _DESCRIPTOR_OPTIONAL_MEDIA_TYPES
        ):
            return False

        metadata = message.media_metadata if isinstance(message.media_metadata, dict) else None
        if not metadata:
            return True
        normalized_type = normalize_media_type(message.media_type, metadata)
        if normalized_type and normalized_type != message.media_type:
            return True
        if message.media_type == "document" and not metadata.get("file_name"):
            return True
        return message.media_type == "photo" and not (
            metadata.get("width") and metadata.get("height")
        )

    def preview_cache_snapshot(self, cache_root: Path | None = None) -> PreviewCacheSnapshot:
        root = cache_root or MEDIA_CACHE_DIR
        preview_files = list(self._iter_preview_files(root))
        workspaces = {path.parent.name for path in preview_files}
        return PreviewCacheSnapshot(
            file_count=len(preview_files),
            total_bytes=sum(path.stat().st_size for path in preview_files),
            workspace_count=len(workspaces),
        )

    async def hydrate_for_ai(
        self,
        *,
        session: AsyncSession,
        workspace_id: int,
        conversation: Conversation,
        message: Message,
        fetch_media: Callable[[], Awaitable[tuple[bytes, str] | None]] | None = None,
    ) -> MediaHydrationResult:
        if not message.media_type:
            return MediaHydrationResult("not_applicable", None, None, None)

        chat_id = conversation.telegram_chat_id or conversation.external_chat_id
        external_message_id = (
            str(message.telegram_message_id)
            if message.telegram_message_id is not None
            else (message.external_message_id or "")
        )
        if chat_id is None or not external_message_id:
            message.media_metadata = build_media_runtime_metadata(
                media_type=message.media_type,
                content=message.content,
                media_metadata=message.media_metadata,
                transcription=message.transcription,
                media_description=message.media_description,
                hydration_status="unavailable",
            )
            session.add(message)
            await update_media_runtime_after_hydration(
                session,
                workspace_id=workspace_id,
                conversation=conversation,
                message=message,
                error="missing_media_identity",
            )
            await session.commit()
            return MediaHydrationResult("unavailable", None, None, None)

        if fetch_media is not None:
            try:
                fetched = await fetch_media()
            except ChannelSyncRateLimitError as exc:
                logger.info(
                    "Deferring media hydration for workspace=%d conv=%d msg=%s retry_after=%.2fs",
                    workspace_id,
                    conversation.id,
                    external_message_id,
                    exc.retry_after_seconds,
                )
                message.media_metadata = build_media_runtime_metadata(
                    media_type=message.media_type,
                    content=message.content,
                    media_metadata=message.media_metadata,
                    transcription=message.transcription,
                    media_description=message.media_description,
                    hydration_status="deferred",
                    retry_after_seconds=exc.retry_after_seconds,
                )
                session.add(message)
                runtime = await update_media_runtime_after_hydration(
                    session,
                    workspace_id=workspace_id,
                    conversation=conversation,
                    message=message,
                    error="rate_limited",
                )
                await session.commit()
                if runtime is not None and runtime.action_state == MEDIA_ACTION_FAILED:
                    return MediaHydrationResult("unavailable", None, None, None)
                return MediaHydrationResult(
                    "deferred",
                    None,
                    None,
                    None,
                    retry_after_seconds=exc.retry_after_seconds,
                )
            if fetched is None:
                message.media_metadata = build_media_runtime_metadata(
                    media_type=message.media_type,
                    content=message.content,
                    media_metadata=message.media_metadata,
                    transcription=message.transcription,
                    media_description=message.media_description,
                    hydration_status="unavailable",
                )
                session.add(message)
                await update_media_runtime_after_hydration(
                    session,
                    workspace_id=workspace_id,
                    conversation=conversation,
                    message=message,
                    error="media_unavailable",
                )
                await session.commit()
                return MediaHydrationResult("unavailable", None, None, None)
            data, mime_type = fetched
        else:
            try:
                data, mime_type = await self._fetch_from_source(
                    workspace_id=workspace_id,
                    chat_id=chat_id,
                    message_id=external_message_id,
                    thumb=False,
                )
            except ChannelSyncRateLimitError as exc:
                logger.info(
                    "Deferring media hydration for workspace=%d conv=%d msg=%s retry_after=%.2fs",
                    workspace_id,
                    conversation.id,
                    external_message_id,
                    exc.retry_after_seconds,
                )
                message.media_metadata = build_media_runtime_metadata(
                    media_type=message.media_type,
                    content=message.content,
                    media_metadata=message.media_metadata,
                    transcription=message.transcription,
                    media_description=message.media_description,
                    hydration_status="deferred",
                    retry_after_seconds=exc.retry_after_seconds,
                )
                session.add(message)
                runtime = await update_media_runtime_after_hydration(
                    session,
                    workspace_id=workspace_id,
                    conversation=conversation,
                    message=message,
                    error="rate_limited",
                )
                await session.commit()
                if runtime is not None and runtime.action_state == MEDIA_ACTION_FAILED:
                    return MediaHydrationResult("unavailable", None, None, None)
                return MediaHydrationResult(
                    "deferred",
                    None,
                    None,
                    None,
                    retry_after_seconds=exc.retry_after_seconds,
                )
            except (MediaUnavailableError, MediaSourceUnavailableError):
                logger.info(
                    "Media hydration unavailable for workspace=%d conv=%d msg=%s",
                    workspace_id,
                    conversation.id,
                    external_message_id,
                )
                message.media_metadata = build_media_runtime_metadata(
                    media_type=message.media_type,
                    content=message.content,
                    media_metadata=message.media_metadata,
                    transcription=message.transcription,
                    media_description=message.media_description,
                    hydration_status="unavailable",
                )
                session.add(message)
                await update_media_runtime_after_hydration(
                    session,
                    workspace_id=workspace_id,
                    conversation=conversation,
                    message=message,
                    error="media_unavailable",
                )
                await session.commit()
                return MediaHydrationResult("unavailable", None, None, None)

        # Stash the freshly-downloaded bytes so the reply turn can perceive this
        # media NATIVELY without a second (fragile) sidecar fetch. Read at dispatch
        # by media_perception.stage_turn_media. Best-effort; never blocks hydration.
        _stash_perception_bytes(workspace_id, message, data)

        normalized_text: str | None = None
        media_evidence: dict | None = None
        if message.media_type in {"voice", "audio"}:
            from app.modules.commercial_spine.llm_gateway import LLMGateway
            from app.modules.commercial_spine.repository import CommercialSpineRepository
            from app.modules.extraction_runtime.media_semantics import normalize_voice_message

            normalized = await normalize_voice_message(
                data,
                mime_type,
                gateway=LLMGateway(repository=CommercialSpineRepository(session)),
                workspace_id=workspace_id,
                correlation_id=f"media:hydrate:{message.id}",
                source_refs=[f"message:{message.id}"],
            )
            message.transcription = normalized.text
            message.transcription_confidence = normalized.confidence
            normalized_text = normalized.text
            normalized_metadata = getattr(normalized, "metadata", None)
            media_evidence = (
                normalized_metadata.get("media_evidence")
                if isinstance(normalized_metadata, dict)
                else None
            )
        elif message.media_type == "photo" or (
            message.media_type == "sticker" and (mime_type or "").startswith("image/")
        ):
            from app.modules.commercial_spine.llm_gateway import LLMGateway
            from app.modules.commercial_spine.repository import CommercialSpineRepository
            from app.modules.extraction_runtime.media_semantics import normalize_image_message

            normalized = await normalize_image_message(
                data,
                mime_type,
                gateway=LLMGateway(repository=CommercialSpineRepository(session)),
                workspace_id=workspace_id,
                correlation_id=f"media:hydrate:{message.id}",
                source_refs=[f"message:{message.id}"],
            )
            message.media_description = normalized.text
            normalized_text = normalized.text
            media_evidence = (
                normalized.metadata.get("media_evidence")
                if isinstance(normalized.metadata, dict)
                else None
            )

        if normalized_text:
            existing = (message.content or "").strip()
            if not existing or existing.startswith(f"[{message.media_type}]"):
                message.content = normalized_text

        message.media_url = canonicalize_message_media_url(
            media_url=message.media_url,
            telegram_chat_id=conversation.telegram_chat_id,
            telegram_message_id=message.telegram_message_id,
            media_type=message.media_type,
        )

        metadata = dict(message.media_metadata or {})
        metadata["hydrated"] = True
        metadata["mime_type"] = mime_type
        metadata["hydrated_at"] = datetime.now(UTC).isoformat()
        if isinstance(media_evidence, dict):
            metadata["media_evidence"] = media_evidence
        else:
            metadata.pop("media_evidence", None)
        message.media_metadata = build_media_runtime_metadata(
            media_type=message.media_type,
            content=message.content,
            media_metadata=metadata,
            transcription=message.transcription,
            media_description=message.media_description,
            hydration_status="hydrated",
        )
        session.add(message)
        await update_media_runtime_after_hydration(
            session,
            workspace_id=workspace_id,
            conversation=conversation,
            message=message,
        )
        await session.commit()

        photo_bytes = (
            base64.b64encode(data).decode("ascii")
            if message.media_type == "photo"
            else None
        )
        photo_mime = mime_type if message.media_type == "photo" else None
        return MediaHydrationResult(
            status="hydrated",
            media_bytes_b64=photo_bytes,
            media_mime_type=photo_mime,
            normalized_text=normalized_text,
        )

    async def open_preview(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
    ) -> MediaStreamResult:
        return await self._open_media(
            workspace_id=workspace_id,
            chat_id=chat_id,
            message_id=message_id,
            thumb=True,
            persist_cache=True,
        )

    async def open_custom_emoji_preview(
        self,
        *,
        workspace_id: int,
        document_id: str,
    ) -> MediaStreamResult:
        data, content_type = await self._fetch_custom_emoji_preview_from_source(
            workspace_id=workspace_id,
            document_id=document_id,
        )
        if content_type == "application/octet-stream" and len(data) > 16:
            content_type = sniff_content_type(data[:16])
        return MediaStreamResult(
            content=data,
            media_type=content_type,
            cache_control="private, max-age=86400",
            content_length=len(data),
        )

    async def open_video_note_preview(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
    ) -> MediaStreamResult:
        cached_result = self._open_cached_video_note_preview(
            workspace_id=workspace_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        if cached_result is not None:
            return cached_result

        try:
            video_bytes, _ = await self._fetch_from_source(
                workspace_id=workspace_id,
                chat_id=chat_id,
                message_id=message_id,
                thumb=False,
            )
        except (MediaUnavailableError, MediaSourceUnavailableError):
            raise

        try:
            preview_bytes = await self._extract_video_note_preview(video_bytes)
        except Exception:
            logger.exception(
                "Falling back to Telegram thumbnail for video note preview workspace=%d chat=%s msg=%s",
                workspace_id,
                chat_id,
                message_id,
            )
            return await self._open_media(
                workspace_id=workspace_id,
                chat_id=chat_id,
                message_id=message_id,
                thumb=True,
                persist_cache=True,
            )

        cache_path = self._video_note_preview_cache_path(
            workspace_id=workspace_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(preview_bytes)
        self._enforce_preview_cache_limit(cache_path.parent, MEDIA_CACHE_DIR)
        return MediaStreamResult(
            content=preview_bytes,
            media_type="image/jpeg",
            cache_control="private, max-age=86400",
            cached_path=cache_path,
        )

    async def open_full_stream(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
        byte_range: str | None = None,
    ) -> MediaLiveStreamResult:
        return await self._open_sidecar_stream(
            workspace_id=workspace_id,
            chat_id=chat_id,
            message_id=message_id,
            thumb=False,
            cache_control="private, max-age=3600",
            byte_range=byte_range,
        )

    def open_cached_preview(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
    ) -> MediaStreamResult | None:
        return self._open_cached_preview(
            workspace_id=workspace_id,
            chat_id=chat_id,
            message_id=message_id,
        )

    async def open_preview_stream(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
    ) -> MediaLiveStreamResult:
        return await self._open_sidecar_stream(
            workspace_id=workspace_id,
            chat_id=chat_id,
            message_id=message_id,
            thumb=True,
            cache_control="private, max-age=86400",
            byte_range=None,
        )

    async def _open_sidecar_stream(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
        thumb: bool,
        cache_control: str,
        byte_range: str | None,
    ) -> MediaLiveStreamResult:
        try:
            stream = await self._telegram_adapter().open_media_stream(
                workspace_id=workspace_id,
                media=ChannelMediaRef(
                    channel="telegram_dm",
                    conversation_id=str(chat_id),
                    message_id=str(message_id),
                ),
                thumb=thumb,
                byte_range=byte_range,
            )
        except ChannelMediaRangeNotSatisfiable as exc:
            raise InvalidRangeError from exc
        except ChannelMediaSourceUnavailable as exc:
            raise MediaSourceUnavailableError from exc
        except (ChannelMediaUnavailable, ChannelSyncRateLimitError) as exc:
            raise MediaUnavailableError from exc

        return MediaLiveStreamResult(
            media_type=stream.media_type,
            cache_control=cache_control,
            status_code=stream.status_code,
            content_length=stream.content_length,
            content_range=stream.content_range,
            accept_ranges=stream.accept_ranges,
            stream=stream.stream,
        )

    async def _open_media(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
        thumb: bool,
        persist_cache: bool,
    ) -> MediaStreamResult:
        if persist_cache:
            cached_result = self._open_cached_preview(
                workspace_id=workspace_id,
                chat_id=chat_id,
                message_id=message_id,
            )
            if cached_result is not None:
                return cached_result

        data, content_type = await self._fetch_from_source(
            workspace_id=workspace_id,
            chat_id=chat_id,
            message_id=message_id,
            thumb=thumb,
        )
        if content_type == "application/octet-stream" and len(data) > 16:
            content_type = sniff_content_type(data[:16])

        if persist_cache:
            cache_key = f"{workspace_id}_{chat_id}_{message_id}_thumb"
            cache_dir = MEDIA_CACHE_DIR / str(workspace_id)
            cache_dir.mkdir(parents=True, exist_ok=True)
            ext = mimetypes.guess_extension(content_type) or ".bin"
            cache_path = cache_dir / f"{cache_key}{ext}"
            cache_path.write_bytes(data)
            self._enforce_preview_cache_limit(cache_dir, MEDIA_CACHE_DIR)
            return MediaStreamResult(
                content=data,
                media_type=content_type,
                cache_control="public, max-age=86400",
                cached_path=cache_path,
            )

        return MediaStreamResult(
            content=data,
            media_type=content_type,
            cache_control="private, max-age=3600",
            content_length=len(data),
            accept_ranges="bytes",
        )

    def _open_cached_preview(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
    ) -> MediaStreamResult | None:
        cache_key = f"{workspace_id}_{chat_id}_{message_id}_thumb"
        cache_dir = MEDIA_CACHE_DIR / str(workspace_id)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_files = list(cache_dir.glob(f"{cache_key}.*"))
        if not cached_files:
            return None

        cached = cached_files[0]
        if cached.stat().st_size > 0 and self._is_fresh_preview_cache(cached):
            content_type = mimetypes.guess_type(str(cached))[0] or "application/octet-stream"
            if content_type == "application/octet-stream":
                content_type = sniff_content_type(cached.read_bytes()[:16])
            return MediaStreamResult(
                content=None,
                media_type=content_type,
                cache_control="private, max-age=86400",
                cached_path=cached,
            )

        cached.unlink(missing_ok=True)
        return None

    def _open_cached_video_note_preview(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
    ) -> MediaStreamResult | None:
        cached = self._video_note_preview_cache_path(
            workspace_id=workspace_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        if not cached.exists():
            return None
        if cached.stat().st_size > 0 and self._is_fresh_preview_cache(cached):
            return MediaStreamResult(
                content=None,
                media_type="image/jpeg",
                cache_control="private, max-age=86400",
                cached_path=cached,
            )
        cached.unlink(missing_ok=True)
        return None

    async def fetch_perception_bytes(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
        thumb: bool = False,
    ) -> tuple[bytes, str]:
        """Public byte fetch for live multimodal perception. Returns (data, mime)."""
        return await self._fetch_from_source(
            workspace_id=workspace_id,
            chat_id=chat_id,
            message_id=message_id,
            thumb=thumb,
        )

    async def _fetch_from_source(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
        thumb: bool,
    ) -> tuple[bytes, str]:
        try:
            blob = await self._telegram_adapter().fetch_media_blob(
                workspace_id=workspace_id,
                media=ChannelMediaRef(
                    channel="telegram_dm",
                    conversation_id=str(chat_id),
                    message_id=str(message_id),
                ),
                thumb=thumb,
            )
        except ChannelMediaSourceUnavailable as exc:
            raise MediaSourceUnavailableError from exc
        except (ChannelMediaUnavailable, ChannelSyncRateLimitError) as exc:
            raise MediaUnavailableError from exc
        return blob.data, blob.mime_type

    async def _fetch_custom_emoji_preview_from_source(
        self,
        *,
        workspace_id: int,
        document_id: str,
    ) -> tuple[bytes, str]:
        try:
            blob = await self._telegram_adapter().fetch_custom_emoji_preview(
                workspace_id=workspace_id,
                document_id=document_id,
            )
        except ChannelMediaSourceUnavailable as exc:
            raise MediaSourceUnavailableError from exc
        except ChannelMediaUnavailable as exc:
            raise MediaUnavailableError from exc
        return blob.data, blob.mime_type

    async def _extract_video_note_preview(self, video_bytes: bytes) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_video:
            temp_video.write(video_bytes)
            temp_path = Path(temp_video.name)
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(temp_path),
                "-vf",
                "thumbnail",
                "-frames:v",
                "1",
                "-q:v",
                "3",
                "-f",
                "image2",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
        finally:
            temp_path.unlink(missing_ok=True)
        if process.returncode != 0 or not stdout:
            raise MediaUnavailableError(
                stderr.decode("utf-8", errors="ignore") or "ffmpeg failed to extract preview"
            )
        return stdout

    def _video_note_preview_cache_path(
        self,
        *,
        workspace_id: int,
        chat_id: int | str,
        message_id: int | str,
    ) -> Path:
        cache_dir = MEDIA_CACHE_DIR / str(workspace_id)
        return cache_dir / f"{workspace_id}_{chat_id}_{message_id}_{VIDEO_NOTE_PREVIEW_CACHE_SUFFIX}.jpg"

    def _enforce_preview_cache_limit(self, cache_dir: Path, cache_root: Path) -> None:
        preview_files = sorted(
            cache_dir.glob("*_thumb*.*"),
            key=lambda path: path.stat().st_mtime,
        )
        overflow = len(preview_files) - PREVIEW_CACHE_MAX_FILES_PER_WORKSPACE
        if overflow > 0:
            for path in preview_files[:overflow]:
                path.unlink(missing_ok=True)
        self._enforce_global_preview_cache_limit(cache_root)

    def _enforce_global_preview_cache_limit(self, cache_root: Path) -> None:
        preview_files = sorted(
            self._iter_preview_files(cache_root),
            key=lambda path: path.stat().st_mtime,
        )
        total_bytes = sum(path.stat().st_size for path in preview_files)
        if total_bytes <= PREVIEW_CACHE_MAX_BYTES_GLOBAL:
            return

        evicted_files = 0
        evicted_bytes = 0
        for path in preview_files:
            if total_bytes <= PREVIEW_CACHE_MAX_BYTES_GLOBAL:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total_bytes -= size
            evicted_files += 1
            evicted_bytes += size

        logger.info(
            "Evicted preview cache globally files=%d bytes=%d remaining_bytes=%d",
            evicted_files,
            evicted_bytes,
            total_bytes,
        )

    def _iter_preview_files(self, cache_root: Path):
        if not cache_root.exists():
            return iter(())
        return cache_root.rglob("*_thumb*.*")

    def _is_fresh_preview_cache(self, path: Path) -> bool:
        age_seconds = time.time() - path.stat().st_mtime
        return age_seconds <= PREVIEW_CACHE_TTL_SECONDS
