"""Promoter creation script — preview + persist (no sending)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound

from app.models.crm_connection import CrmConnection
from app.models.outreach import OutreachCampaign, OutreachTarget
from app.modules.bi_promoter import script_ops as script
from app.modules.bi_promoter.contracts import SegmentSpec
from app.modules.crm_connector.contracts import CrmContactSnapshot

pytestmark = pytest.mark.asyncio


class _FakeProvider:
    async def fetch_contacts(self, conn, *, page):
        return [CrmContactSnapshot("6", "Cold", "+998905556677")] if page == 1 else []


async def _conn(db_session, workspace):
    c = CrmConnection(workspace_id=workspace.id, provider="amocrm", status="active",
                      provider_account_ref="mybiz", webhook_token="wh1", pipeline_config={})
    db_session.add(c)
    await db_session.flush()
    return c


async def test_preview_personalizes_sample_without_persisting_send(db_session, workspace):
    conn = await _conn(db_session, workspace)
    fake_llm = AsyncMock(return_value=type("R", (), {"text": "Salom Cold!"})())
    with patch("app.modules.bi_promoter.personalizer.generate_with_fallback", fake_llm):
        samples = await script.preview_samples(
            db_session, provider=_FakeProvider(), workspace_id=workspace.id,
            connection_id=conn.id, base_message="Yangi intake", segment=SegmentSpec(), limit=2)
    assert samples and samples[0]["opener"] == "Salom Cold!"
    assert samples[0]["phone"] == "+998905556677"


async def test_create_and_materialize_persists_targets(db_session, workspace):
    conn = await _conn(db_session, workspace)
    _campaign = await script.create_and_materialize(
        db_session, provider=_FakeProvider(), workspace_id=workspace.id,
        connection_id=conn.id, name="Iyun", goal="reactivate",
        base_message="Yangi intake", segment=SegmentSpec())
    assert (await db_session.execute(select(OutreachCampaign))).scalar_one().status == "draft"
    assert (await db_session.execute(select(OutreachTarget))).scalars().all()


async def test_approve_and_start_flips_draft_to_running(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await script.create_and_materialize(
        db_session, provider=_FakeProvider(), workspace_id=workspace.id,
        connection_id=conn.id, name="Iyun", goal="reactivate",
        base_message="Yangi intake", segment=SegmentSpec())
    started = await script.approve_and_start(
        db_session, workspace_id=workspace.id, campaign_id=campaign.id)
    assert started.status == "running"
    assert started.approved_at is not None


async def test_approve_and_start_rejects_cross_workspace(db_session, workspace, workspace_b):
    conn = await _conn(db_session, workspace)
    campaign = await script.create_and_materialize(
        db_session, provider=_FakeProvider(), workspace_id=workspace.id,
        connection_id=conn.id, name="Iyun", goal="reactivate",
        base_message="Yangi intake", segment=SegmentSpec())
    with pytest.raises(NoResultFound):  # workspace_b can't start workspace's campaign
        await script.approve_and_start(
            db_session, workspace_id=workspace_b.id, campaign_id=campaign.id)
