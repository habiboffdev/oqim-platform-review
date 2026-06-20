from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.workspace import Workspace
from app.services.channel_media_access import (
    ChannelMediaAccess,
    InvalidRangeError,
    MediaSourceUnavailableError,
    MediaUnavailableError,
)

router = APIRouter(prefix="/media", tags=["media"])
media_access = ChannelMediaAccess()


@router.get("/custom-emoji/{document_id}")
async def get_custom_emoji(
    document_id: str,
    workspace: Workspace = Depends(get_current_workspace),
):
    try:
        result = await media_access.open_custom_emoji_preview(
            workspace_id=workspace.id,
            document_id=document_id,
        )
    except MediaUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom emoji not found",
        )
    except MediaSourceUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Custom emoji download failed — sidecar unreachable",
        )

    return Response(
        content=result.content or b"",
        media_type=result.media_type,
        headers={
            "Cache-Control": result.cache_control,
            **({"Content-Length": str(result.content_length)} if result.content_length is not None else {}),
        },
    )


@router.get("/{chat_id}/{msg_id}")
async def get_media(
    chat_id: int,
    msg_id: int,
    request: Request,
    workspace: Workspace = Depends(get_current_workspace),
    session: AsyncSession = Depends(get_db_session),
    thumb: bool = False,
):
    """Proxy media download through the channel media access service."""
    # Verify chat_id belongs to this workspace
    result = await session.execute(
        select(Conversation.id).where(
            Conversation.workspace_id == workspace.id,
            Conversation.telegram_chat_id == chat_id,
        )
    )
    conversation_id = result.scalar_one_or_none()
    if not conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    try:
        if thumb:
            message_media_type = await session.scalar(
                select(Message.media_type).where(
                    Message.conversation_id == conversation_id,
                    Message.telegram_message_id == msg_id,
                )
            )
            if message_media_type == "video_note":
                result = await media_access.open_video_note_preview(
                    workspace_id=workspace.id,
                    chat_id=chat_id,
                    message_id=msg_id,
                )
                if result.cached_path:
                    return FileResponse(
                        path=str(result.cached_path),
                        media_type=result.media_type,
                        headers={"Cache-Control": result.cache_control},
                    )
                return Response(
                    content=result.content or b"",
                    media_type=result.media_type,
                    headers={"Cache-Control": result.cache_control},
                )

            cached_preview = media_access.open_cached_preview(
                workspace_id=workspace.id,
                chat_id=chat_id,
                message_id=msg_id,
            )
            if cached_preview is not None:
                return FileResponse(
                    path=str(cached_preview.cached_path),
                    media_type=cached_preview.media_type,
                    headers={"Cache-Control": cached_preview.cache_control},
                )

            stream_result = await media_access.open_preview_stream(
                workspace_id=workspace.id,
                chat_id=chat_id,
                message_id=msg_id,
            )
            return StreamingResponse(
                stream_result.stream,
                media_type=stream_result.media_type,
                headers={
                    "Cache-Control": stream_result.cache_control,
                    **(
                        {"Content-Length": str(stream_result.content_length)}
                        if stream_result.content_length is not None
                        else {}
                    ),
                },
            )

        stream_result = await media_access.open_full_stream(
            workspace_id=workspace.id,
            chat_id=chat_id,
            message_id=msg_id,
            byte_range=request.headers.get("range"),
        )
        return StreamingResponse(
            stream_result.stream,
            media_type=stream_result.media_type,
            status_code=stream_result.status_code,
            headers={
                "Cache-Control": stream_result.cache_control,
                **(
                    {"Content-Length": str(stream_result.content_length)}
                    if stream_result.content_length is not None
                    else {}
                ),
                **({"Accept-Ranges": stream_result.accept_ranges} if stream_result.accept_ranges else {}),
                **({"Content-Range": stream_result.content_range} if stream_result.content_range else {}),
            },
        )
    except InvalidRangeError:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Invalid byte range",
        )
    except MediaUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found",
        )
    except MediaSourceUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Media download failed — sidecar unreachable",
        )
