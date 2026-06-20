"""BUSINESS.md generate/read endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.models.workspace import Workspace
from app.modules.brain.business_document import BusinessDocumentService

router = APIRouter(prefix="/brain/business-md", tags=["brain-documents"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.post("/generate")
async def generate_business_md(workspace: WorkspaceDep, session: SessionDep) -> dict:
    service = BusinessDocumentService(session)
    rendered = await service.generate(workspace_id=workspace.id, workspace_name=workspace.name)
    await session.commit()
    return rendered.model_dump(mode="json")


@router.get("")
async def get_business_md(workspace: WorkspaceDep, session: SessionDep) -> dict:
    service = BusinessDocumentService(session)
    rendered = await service.render_current(workspace_id=workspace.id, workspace_name=workspace.name)
    return rendered.model_dump(mode="json")


class SectionEditBody(BaseModel):
    body: str


@router.patch("/sections/{section_key}")
async def edit_business_md_section(
    section_key: str,
    payload: SectionEditBody,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    service = BusinessDocumentService(session)
    try:
        await service.edit_section(
            workspace_id=workspace.id, section_key=section_key, body=payload.body
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown section key: {section_key}")
    await session.commit()
    rendered = await service.render_current(
        workspace_id=workspace.id, workspace_name=workspace.name
    )
    return rendered.model_dump(mode="json")
