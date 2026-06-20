"""SKILL.md learner trigger + candidate review endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.models.learned_skill_candidate import LearnedSkillCandidate
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import AgentSkillInput
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.brain.skill_learner import SkillLearnerService
from app.modules.brain.skill_upload import SkillUploadService

router = APIRouter(prefix="/brain/skills", tags=["brain-documents"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class ApproveBody(BaseModel):
    name: str | None = None
    trigger: str | None = None
    action: str | None = None
    example_phrase: str | None = None


class UploadBody(BaseModel):
    content: str


def _candidate_json(c: LearnedSkillCandidate) -> dict:
    return {
        "id": c.id, "slug": c.slug, "name": c.name, "trigger": c.trigger,
        "action": c.action, "example_phrase": c.example_phrase, "dimension": c.dimension,
        "confidence": c.confidence, "evidence_conv_ids": c.evidence_conv_ids,
        "status": c.status, "source": c.source,
    }


async def _load_candidate(session: AsyncSession, *, workspace_id: int, candidate_id: int) -> LearnedSkillCandidate:
    c = await session.get(LearnedSkillCandidate, candidate_id)
    if c is None or c.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return c


@router.post("/learn")
async def learn_skills(workspace: WorkspaceDep, session: SessionDep) -> dict:
    service = SkillLearnerService(session)
    report = await service.learn(workspace_id=workspace.id)
    await session.commit()
    return report.model_dump(mode="json")


@router.post("/upload")
async def upload_skill(payload: UploadBody, workspace: WorkspaceDep, session: SessionDep) -> dict:
    service = SkillUploadService(session)
    try:
        candidate = await service.import_from_text(workspace_id=workspace.id, content=payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return _candidate_json(candidate)


@router.get("/candidates")
async def list_candidates(workspace: WorkspaceDep, session: SessionDep) -> dict:
    rows = (await session.execute(
        select(LearnedSkillCandidate)
        .where(
            LearnedSkillCandidate.workspace_id == workspace.id,
            LearnedSkillCandidate.status == "proposed",
        )
        .order_by(LearnedSkillCandidate.confidence.desc(), LearnedSkillCandidate.id)
    )).scalars().all()
    return {"items": [_candidate_json(c) for c in rows]}


@router.post("/candidates/{candidate_id}/approve")
async def approve_candidate(
    candidate_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
    payload: ApproveBody | None = None,
) -> dict:
    c = await _load_candidate(session, workspace_id=workspace.id, candidate_id=candidate_id)
    body = payload or ApproveBody()
    name = body.name or c.name
    trigger = body.trigger or c.trigger
    action = body.action or c.action
    example_phrase = body.example_phrase if body.example_phrase is not None else c.example_phrase
    docs = AgentDocumentService(session)
    skill = await docs.upsert_skill(
        workspace_id=workspace.id,
        payload=AgentSkillInput(
            slug=c.slug,
            name=name,
            description=action,
            instructions=action,
            when_to_use=trigger,
            examples=[{"phrase": example_phrase}] if example_phrase else [],
        ),
    )
    c.status = "approved"
    await session.commit()
    return {"status": "approved", "skill": skill.model_dump(mode="json")}


@router.post("/candidates/{candidate_id}/reject")
async def reject_candidate(
    candidate_id: int, workspace: WorkspaceDep, session: SessionDep
) -> dict:
    c = await _load_candidate(session, workspace_id=workspace.id, candidate_id=candidate_id)
    c.status = "rejected"
    await session.commit()
    return {"status": "rejected", "id": candidate_id}
