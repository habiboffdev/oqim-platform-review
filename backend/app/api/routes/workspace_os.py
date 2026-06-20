from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.models.workspace import Workspace
from app.modules.workspace_os.projection import WorkspaceOSProjectionService
from app.modules.workspace_os.provisioner import WorkspaceOSProvisioner

router = APIRouter(prefix="/workspace-os", tags=["workspace-os"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/state")
async def get_workspace_os_state(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    projection = await WorkspaceOSProjectionService(session).build(workspace=workspace)
    return projection.model_dump(mode="json")


@router.post("/provision")
async def provision_workspace_os(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    profile = dict(workspace.onboarding_profile or {})
    await WorkspaceOSProvisioner(session).provision(
        workspace=workspace,
        profile=profile,
        preferences=dict(profile.get("preferences") or {}),
    )
    await session.commit()
    projection = await WorkspaceOSProjectionService(session).build(workspace=workspace)
    return projection.model_dump(mode="json")
