from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceMediaArtifactWrite:
    workspace_id: int
    media_ref: str
    content_bytes: bytes
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class SourceMediaArtifactResult:
    artifact_ref: str
    content_hash: str
    byte_size: int
    content_type: str | None


@dataclass(frozen=True, slots=True)
class SourceMediaArtifactReadResult:
    artifact_ref: str
    content_bytes: bytes
    content_type: str | None


class SourceMediaArtifactStore:
    """Filesystem-backed source media blob store for retryable learner jobs."""

    _SCHEME = "source_media_artifact:v1"

    def __init__(self, *, base_path: Path) -> None:
        self._base_path = base_path

    async def write(
        self,
        request: SourceMediaArtifactWrite,
    ) -> SourceMediaArtifactResult | None:
        if not request.content_bytes:
            return None
        content_hash = hashlib.sha256(request.content_bytes).hexdigest()
        media_key = hashlib.sha256(request.media_ref.encode("utf-8")).hexdigest()
        extension = _extension_for_content_type(request.content_type)
        relative_path = (
            Path(str(request.workspace_id))
            / media_key[:2]
            / media_key
            / f"{content_hash}{extension}"
        )
        artifact_path = self._base_path / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        if not artifact_path.exists():
            await asyncio.to_thread(artifact_path.write_bytes, request.content_bytes)
        artifact_ref = f"{self._SCHEME}:{request.workspace_id}:{media_key}:{content_hash}{extension}"
        return SourceMediaArtifactResult(
            artifact_ref=artifact_ref,
            content_hash=content_hash,
            byte_size=len(request.content_bytes),
            content_type=request.content_type,
        )

    async def read(
        self,
        *,
        artifact_ref: str,
        workspace_id: int,
    ) -> SourceMediaArtifactReadResult | None:
        parsed = _parse_artifact_ref(artifact_ref)
        if parsed is None or parsed["workspace_id"] != workspace_id:
            return None
        artifact_path = (
            self._base_path
            / str(parsed["workspace_id"])
            / parsed["media_key"][:2]
            / parsed["media_key"]
            / parsed["filename"]
        )
        if not artifact_path.exists() or not artifact_path.is_file():
            return None
        return SourceMediaArtifactReadResult(
            artifact_ref=artifact_ref,
            content_bytes=await asyncio.to_thread(artifact_path.read_bytes),
            content_type=mimetypes.guess_type(str(artifact_path))[0],
        )


def _parse_artifact_ref(artifact_ref: str) -> dict[str, Any] | None:
    parts = artifact_ref.split(":")
    if len(parts) != 5:
        return None
    scheme, version, workspace_id, media_key, filename = parts
    if f"{scheme}:{version}" != SourceMediaArtifactStore._SCHEME:
        return None
    try:
        parsed_workspace_id = int(workspace_id)
    except ValueError:
        return None
    if not media_key or "/" in media_key or not filename or "/" in filename:
        return None
    return {
        "workspace_id": parsed_workspace_id,
        "media_key": media_key,
        "filename": filename,
    }


def _extension_for_content_type(content_type: str | None) -> str:
    extension = mimetypes.guess_extension(content_type or "")
    if extension in {".jpe"}:
        return ".jpg"
    return extension or ".bin"
