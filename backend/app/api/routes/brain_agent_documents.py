"""AGENT.md generate/read endpoints (per agent)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.models.workspace import Workspace
from app.modules.brain.agent_document import AgentDocumentBuilderService

router = APIRouter(prefix="/brain/agents", tags=["brain-documents"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class GenerateAgentMdBody(BaseModel):
    owner_input: str | None = None


@router.post("/{agent_id}/agent-md/generate")
async def generate_agent_md(
    agent_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
    payload: GenerateAgentMdBody | None = None,
) -> dict:
    service = AgentDocumentBuilderService(session)
    owner_input = payload.owner_input if payload else None
    try:
        rendered = await service.generate(
            workspace_id=workspace.id, agent_id=agent_id, owner_input=owner_input
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found")
    await session.commit()
    return rendered.model_dump(mode="json")


@router.get("/{agent_id}/agent-md")
async def get_agent_md(
    agent_id: int, workspace: WorkspaceDep, session: SessionDep
) -> dict:
    service = AgentDocumentBuilderService(session)
    try:
        rendered = await service.render_current(workspace_id=workspace.id, agent_id=agent_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found")
    return rendered.model_dump(mode="json")


class SectionEditBody(BaseModel):
    body: str


@router.patch("/{agent_id}/agent-md/sections/{section_key}")
async def edit_agent_md_section(
    agent_id: int,
    section_key: str,
    payload: SectionEditBody,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    service = AgentDocumentBuilderService(session)
    try:
        await service.edit_section(
            workspace_id=workspace.id, agent_id=agent_id, section_key=section_key, body=payload.body
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown section key: {section_key}")
    await session.commit()
    rendered = await service.render_current(workspace_id=workspace.id, agent_id=agent_id)
    return rendered.model_dump(mode="json")
