from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.models.agent import Agent
from app.models.workspace import Workspace
from app.schemas.agent import AgentCreate, AgentResponse, AgentUpdate

router = APIRouter(prefix="/agents", tags=["agents"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def _get_agent_for_workspace(
    agent_id: int,
    workspace: Workspace,
    session: AsyncSession,
) -> Agent:
    """Fetch an agent and verify it belongs to the workspace."""
    result = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.workspace_id == workspace.id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )
    return agent


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    workspace: WorkspaceDep,
    session: SessionDep,
):
    result = await session.execute(
        select(Agent)
        .where(Agent.workspace_id == workspace.id, Agent.is_active.is_(True))
        .order_by(Agent.is_default.desc(), Agent.created_at)
    )
    agents = result.scalars().all()
    return [AgentResponse.model_validate(a) for a in agents]


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    data: AgentCreate,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    agent = Agent(
        workspace_id=workspace.id,
        name=data.name,
        persona=data.persona,
        instructions=data.instructions,
        example_responses=data.example_responses,
        knowledge_config=data.knowledge_config,
        channel_config=data.channel_config,
        tools_config=data.tools_config,
        trust_mode=data.trust_mode,
        auto_send_threshold=data.auto_send_threshold,
        escalation_topics=data.escalation_topics,
        agent_type=data.agent_type,
        contact_scope=data.contact_scope,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return AgentResponse.model_validate(agent)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    agent = await _get_agent_for_workspace(agent_id, workspace, session)
    return AgentResponse.model_validate(agent)


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: int,
    data: AgentUpdate,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    agent = await _get_agent_for_workspace(agent_id, workspace, session)

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(agent, field, value)

    await session.commit()
    await session.refresh(agent)
    return AgentResponse.model_validate(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    agent = await _get_agent_for_workspace(agent_id, workspace, session)
    agent.is_active = False
    await session.commit()


@router.post("/{agent_id}/default", response_model=AgentResponse)
async def set_default_agent(
    agent_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    agent = await _get_agent_for_workspace(agent_id, workspace, session)

    # Unset other defaults
    result = await session.execute(
        select(Agent).where(
            Agent.workspace_id == workspace.id,
            Agent.is_default.is_(True),
            Agent.id != agent_id,
        )
    )
    for other in result.scalars().all():
        other.is_default = False

    agent.is_default = True
    await session.commit()
    await session.refresh(agent)
    return AgentResponse.model_validate(agent)
