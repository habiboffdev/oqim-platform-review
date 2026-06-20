"""Active sync session route.

`/api/sync/session` is the current reconnect contract. Legacy check/backfill
routes were deleted from the active app; use explicit hydrate/admin repair
paths for bounded recovery.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.core.logging import get_logger
from app.models.workspace import Workspace
from app.services.sync_session import build_sync_session

logger = get_logger("api.sync")

router = APIRouter(prefix="/sync", tags=["sync"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class SyncSessionRequest(BaseModel):
    last_sequence: int = 0
    server_sequence: int | None = None
    active_conversation_id: int | None = None
    last_seen_conversation_seq: int | None = None
    last_seen_conversation_revision: int | None = None


class SyncSessionResponseModel(BaseModel):
    kind: str
    action: str
    server_sequence: int
    client_sequence: int
    conversation_id: int | None = None
    after_conversation_seq: int | None = None
    latest_conversation_seq: int | None = None
    latest_conversation_revision: int | None = None
    conversation_state: dict | None = None
    projections: list[dict]


@router.post("/session", response_model=SyncSessionResponseModel)
async def sync_session(
    data: SyncSessionRequest,
    workspace: WorkspaceDep,
    db: SessionDep,
):
    """Return the authoritative projection resume contract for reconnects."""
    response = await build_sync_session(
        session=db,
        workspace_id=workspace.id,
        server_sequence=data.server_sequence or data.last_sequence,
        client_sequence=data.last_sequence,
        active_conversation_id=data.active_conversation_id,
        last_seen_conversation_seq=data.last_seen_conversation_seq,
        last_seen_conversation_revision=data.last_seen_conversation_revision,
    )
    return SyncSessionResponseModel(**response.to_websocket_data())
