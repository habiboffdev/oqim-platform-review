"""Owner-guided campaign creation helpers (no sending). Used by
scripts/promoter_campaign.py and tested directly."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.outreach import OutreachCampaign
from app.modules.bi_promoter.contracts import SegmentSpec
from app.modules.bi_promoter.orchestrator import CampaignOrchestrator
from app.modules.bi_promoter.personalizer import personalize_opener


async def preview_samples(
    session: AsyncSession, *, provider, workspace_id: int, connection_id: int,
    base_message: str, segment: SegmentSpec, limit: int = 3,
) -> list[dict]:
    """Personalize a few sample openers so the owner sees what will go out."""
    orch = CampaignOrchestrator(session, provider=provider)
    contacts = await orch.resolve_contacts(
        connection_id=connection_id, workspace_id=workspace_id, segment=segment)
    samples = []
    for contact in [c for c in contacts if c.phone][:limit]:
        opener = await personalize_opener(
            workspace_id=workspace_id, base_message=base_message,
            contact_name=contact.name, crm_context="")
        samples.append({"phone": contact.phone, "name": contact.name, "opener": opener})
    return samples


async def create_and_materialize(
    session: AsyncSession, *, provider, workspace_id: int, connection_id: int,
    name: str, goal: str, base_message: str, segment: SegmentSpec, caps: dict | None = None,
):
    orch = CampaignOrchestrator(session, provider=provider)
    campaign = await orch.create_campaign(
        workspace_id=workspace_id, connection_id=connection_id, name=name, goal=goal,
        segment=segment, base_message=base_message, caps=caps)
    await orch.materialize(campaign)
    await session.commit()
    return campaign


async def approve_and_start(
    session: AsyncSession, *, workspace_id: int, campaign_id: int,
) -> OutreachCampaign:
    """Owner approval: draft/approved -> running (approved_at stamped). The drip
    worker only drains campaigns in 'running'. Only draft or approved campaigns
    can be started — paused/completed/running campaigns are rejected so this
    cannot silently un-pause a deliberately-paused campaign (use the resume
    path for that)."""
    campaign = (
        await session.execute(
            select(OutreachCampaign).where(
                OutreachCampaign.id == campaign_id,
                OutreachCampaign.workspace_id == workspace_id,
            )
        )
    ).scalar_one()
    if campaign.status not in ("draft", "approved"):
        raise ValueError(
            f"Campaign {campaign_id} is '{campaign.status}' — "
            "only draft or approved campaigns can be started "
            "(use /campaign resume to un-pause)."
        )
    campaign.status = "running"
    campaign.approved_at = utc_now()
    await session.commit()
    return campaign
