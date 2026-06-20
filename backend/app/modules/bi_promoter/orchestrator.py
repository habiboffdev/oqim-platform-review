"""CampaignOrchestrator — create campaigns and materialize paced targets.

Reads a segment from the CRM, decides a warm/cold tier per contact, and writes
one idempotent outreach_target per reachable contact. No sending. No Customer
rows created here (the drip worker links/creates lazily at send time).
"""
from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection
from app.models.customer import Customer
from app.models.outreach import OutreachCampaign, OutreachTarget
from app.modules.bi_promoter.contracts import SegmentSpec
from app.modules.crm_connector.contracts import CrmContactSnapshot

logger = get_logger("promoter.orchestrator")


class CampaignOrchestrator:
    def __init__(self, session: AsyncSession, *, provider) -> None:
        self._session = session
        self._provider = provider

    async def create_campaign(
        self, *, workspace_id: int, connection_id: int, name: str, goal: str,
        segment: SegmentSpec, base_message: str, caps: dict | None = None,
    ) -> OutreachCampaign:
        campaign = OutreachCampaign(
            workspace_id=workspace_id, connection_id=connection_id, name=name, goal=goal,
            segment_spec={
                "pipeline_id": segment.pipeline_id,
                "stage_ids": list(segment.stage_ids),
                "tags": list(segment.tags),
            },
            base_message=base_message, caps=caps or {}, status="draft")
        self._session.add(campaign)
        await self._session.flush()
        return campaign

    async def materialize(self, campaign: OutreachCampaign) -> int:
        """Resolve the segment and insert one target per reachable contact."""
        spec = SegmentSpec(
            pipeline_id=str((campaign.segment_spec or {}).get("pipeline_id") or ""),
            stage_ids=tuple((campaign.segment_spec or {}).get("stage_ids", [])),
            tags=tuple((campaign.segment_spec or {}).get("tags", [])))
        if spec.tags:
            logger.warning(
                "promoter.tags_filter_not_applied campaign=%s tags=%s "
                "— tag filtering is not implemented; segment resolves by stage/full list",
                campaign.id, list(spec.tags),
            )
        contacts = await self.resolve_contacts(
            connection_id=campaign.connection_id, workspace_id=campaign.workspace_id,
            segment=spec)
        inserted = 0
        for contact in contacts:
            phone = (contact.phone or "").strip()
            if not phone:
                continue  # unreachable on Telegram
            tier = "warm" if await self._has_dialog(campaign.workspace_id, phone) else "cold"
            idem = hashlib.sha256(f"{campaign.id}:{phone}".encode()).hexdigest()[:32]
            stmt = (
                insert(OutreachTarget)
                .values(
                    campaign_id=campaign.id, workspace_id=campaign.workspace_id,
                    provider_contact_id=contact.contact_id, phone=phone,
                    display_name=contact.name or "", tier=tier, state="pending",
                    idempotency_key=idem)
                .on_conflict_do_nothing(constraint="uq_outreach_targets_campaign_phone")
                .returning(OutreachTarget.id)
            )
            if (await self._session.execute(stmt)).first() is not None:
                inserted += 1
        await self._session.flush()
        return inserted

    async def resolve_contacts(
        self, *, connection_id: int, workspace_id: int, segment: SegmentSpec,
    ) -> list[CrmContactSnapshot]:
        """Resolve the segment's contacts from the CRM. Stage segments page
        fetch_leads_by_stage (id-only stubs), dedupe, then hydrate phone/name via
        fetch_contacts_by_ids; otherwise page the full contact list."""
        conn = (await self._session.execute(
            select(CrmConnection).where(
                CrmConnection.id == connection_id,
                CrmConnection.workspace_id == workspace_id,
            ))
        ).scalar_one()
        if segment.stage_ids:
            if not segment.pipeline_id:
                raise ValueError("stage-filtered segment requires SegmentSpec.pipeline_id")
            ids: list[str] = []
            seen: set[str] = set()
            page = 1
            while page <= 200:  # hard safety bound
                batch = await self._provider.fetch_leads_by_stage(
                    conn, pipeline_id=segment.pipeline_id,
                    status_ids=list(segment.stage_ids), page=page)
                if not batch:
                    break
                for stub in batch:
                    if stub.contact_id not in seen:
                        seen.add(stub.contact_id)
                        ids.append(stub.contact_id)
                page += 1
            else:
                logger.warning("promoter.segment_page_bound_hit connection=%s", connection_id)
            return await self._provider.fetch_contacts_by_ids(conn, contact_ids=ids)
        out: list[CrmContactSnapshot] = []
        page = 1
        while page <= 200:  # hard safety bound
            batch = await self._provider.fetch_contacts(conn, page=page)
            if not batch:
                break
            out.extend(batch)
            page += 1
        return out

    async def _has_dialog(self, workspace_id: int, phone: str) -> bool:
        row = await self._session.execute(
            select(Conversation.id)
            .join(Customer, Customer.id == Conversation.customer_id)
            .where(Customer.workspace_id == workspace_id, Customer.phone_number == phone)
            .limit(1)
        )
        return row.first() is not None
