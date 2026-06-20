from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.models.workspace import Workspace
from app.modules.commercial_spine.repository import CommercialSpineRepository

router = APIRouter(prefix="/commercial-spine", tags=["commercial-spine"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/debug/{correlation_id}")
async def get_commercial_spine_debug_snapshot(
    correlation_id: str,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    snapshot = await CommercialSpineRepository(session).get_debug_snapshot(
        workspace_id=workspace.id,
        correlation_id=correlation_id,
    )
    return snapshot.to_dict()
