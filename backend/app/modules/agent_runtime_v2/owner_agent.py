"""Idempotent provisioning of a workspace's Owner Agent (agent_type='owner').

The Owner Agent is created as part of control-bot provisioning, and lazily
ensured on the first owner turn (which backfills workspaces — e.g. the pilot —
that provisioned their bot before this hook existed). One per workspace.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent

OWNER_AGENT_TYPE = "owner"


async def ensure_owner_agent(session: AsyncSession, workspace_id: int) -> Agent:
    """Return the workspace's Owner Agent, creating one if none exists."""
    existing = (
        await session.execute(
            select(Agent)
            .where(Agent.workspace_id == workspace_id, Agent.agent_type == OWNER_AGENT_TYPE)
            .order_by(Agent.id)
        )
    ).scalars().first()
    if existing is not None:
        return existing
    agent = Agent(
        workspace_id=workspace_id,
        name="OQIM boshqaruv agenti",
        agent_type=OWNER_AGENT_TYPE,
        is_default=False,
        trust_mode="disabled",
    )
    session.add(agent)
    await session.flush()
    return agent
