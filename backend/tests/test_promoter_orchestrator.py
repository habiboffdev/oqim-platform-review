"""CampaignOrchestrator — create + materialize targets (no sending)."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection
from app.models.customer import Customer
from app.models.outreach import OutreachTarget
from app.modules.bi_promoter.contracts import SegmentSpec
from app.modules.bi_promoter.orchestrator import CampaignOrchestrator
from app.modules.crm_connector.contracts import CrmContactSnapshot

pytestmark = pytest.mark.asyncio


class _FakeProvider:
    def __init__(self, contacts):
        self._contacts = contacts
        self.calls = []

    async def fetch_contacts(self, conn, *, page):
        self.calls.append(("fetch_contacts", page))
        return self._contacts if page == 1 else []  # one page then empty


async def _conn(db_session, workspace):
    c = CrmConnection(workspace_id=workspace.id, provider="amocrm", status="active",
                      provider_account_ref="mybiz", webhook_token="wh1", pipeline_config={})
    db_session.add(c)
    await db_session.flush()
    return c


async def _customer_with_conversation(db_session, workspace, phone):
    cust = Customer(workspace_id=workspace.id, display_name="Warm", phone_number=phone)
    db_session.add(cust)
    await db_session.flush()
    conv = Conversation(workspace_id=workspace.id, customer_id=cust.id,
                        channel="telegram_dm", pipeline_stage="new")
    db_session.add(conv)
    await db_session.flush()
    return cust


async def _count_targets(db_session):
    return (await db_session.execute(select(func.count()).select_from(OutreachTarget))).scalar_one()


async def test_materialize_creates_targets_skips_no_phone_and_tiers(db_session, workspace):
    conn = await _conn(db_session, workspace)
    # one warm (existing conversation), one cold (new), one unreachable (no phone)
    await _customer_with_conversation(db_session, workspace, "+998901112233")
    contacts = [
        CrmContactSnapshot("5", "Warm", "+998901112233"),
        CrmContactSnapshot("6", "Cold", "+998905556677"),
        CrmContactSnapshot("7", "NoPhone", None),
    ]
    orch = CampaignOrchestrator(db_session, provider=_FakeProvider(contacts))
    campaign = await orch.create_campaign(
        workspace_id=workspace.id, connection_id=conn.id, name="C", goal="reactivate",
        segment=SegmentSpec(), base_message="Salom!")
    n = await orch.materialize(campaign)

    assert n == 2  # no-phone skipped
    rows = (await db_session.execute(select(OutreachTarget).order_by(OutreachTarget.phone))).scalars().all()
    by_phone = {r.phone: r for r in rows}
    assert by_phone["+998901112233"].tier == "warm"
    assert by_phone["+998905556677"].tier == "cold"
    assert all(r.state == "pending" for r in rows)
    assert by_phone["+998905556677"].idempotency_key  # non-empty, unique


async def test_materialize_is_idempotent(db_session, workspace):
    conn = await _conn(db_session, workspace)
    contacts = [CrmContactSnapshot("6", "Cold", "+998905556677")]
    orch = CampaignOrchestrator(db_session, provider=_FakeProvider(contacts))
    campaign = await orch.create_campaign(
        workspace_id=workspace.id, connection_id=conn.id, name="C", goal="reactivate",
        segment=SegmentSpec(), base_message="Salom!")
    await orch.materialize(campaign)
    await orch.materialize(campaign)  # second run, same contacts
    assert await _count_targets(db_session) == 1  # UNIQUE(campaign, phone) + ON CONFLICT


async def test_resolve_contacts_enforces_workspace_isolation(db_session, workspace, workspace_b):
    conn = await _conn(db_session, workspace)  # belongs to `workspace`
    orch = CampaignOrchestrator(db_session, provider=_FakeProvider([]))
    from sqlalchemy.exc import NoResultFound
    with pytest.raises(NoResultFound):
        # a DIFFERENT workspace must not resolve this workspace's connection
        await orch.resolve_contacts(connection_id=conn.id, workspace_id=workspace_b.id,
                                    segment=SegmentSpec())


async def test_materialize_warns_only_for_tags_not_stage_ids(db_session, workspace, caplog):
    """stage_ids alone must NOT produce the old segment_filter_not_applied warning;
    tags still warn because tag filtering is not yet implemented."""
    import logging

    conn = await _conn(db_session, workspace)

    class _StageOnlyProvider:
        async def fetch_leads_by_stage(self, conn, *, pipeline_id, status_ids, page):
            return [CrmContactSnapshot("6", "Cold", "+998905556677")] if page == 1 else []

        async def fetch_contacts_by_ids(self, conn, *, contact_ids):
            return [CrmContactSnapshot("6", "Cold", "+998905556677")]

        async def fetch_contacts(self, conn, *, page):
            raise AssertionError("stage segment must not call fetch_contacts")

    orch = CampaignOrchestrator(db_session, provider=_StageOnlyProvider())
    campaign = await orch.create_campaign(
        workspace_id=workspace.id, connection_id=conn.id, name="C", goal="reactivate",
        segment=SegmentSpec(pipeline_id="777", stage_ids=("111",)), base_message="Salom!")
    with caplog.at_level(logging.WARNING):
        n = await orch.materialize(campaign)
    assert n == 1
    assert not any("segment_filter_not_applied" in r.message for r in caplog.records)

    # tags DO warn
    class _TagProvider:
        async def fetch_contacts(self, conn, *, page):
            return [CrmContactSnapshot("6", "Cold", "+998905556677")] if page == 1 else []

    orch2 = CampaignOrchestrator(db_session, provider=_TagProvider())
    campaign2 = await orch2.create_campaign(
        workspace_id=workspace.id, connection_id=conn.id, name="C2", goal="reactivate",
        segment=SegmentSpec(tags=("vip",)), base_message="Salom!")
    with caplog.at_level(logging.WARNING):
        await orch2.materialize(campaign2)
    assert any("tags_filter_not_applied" in r.message for r in caplog.records)


class _StageProvider:
    """Leads-by-stage returns id-only stubs; hydration must use fetch_contacts_by_ids."""

    def __init__(self):
        self.calls = []

    async def fetch_leads_by_stage(self, conn, *, pipeline_id, status_ids, page):
        self.calls.append(("leads", pipeline_id, tuple(status_ids), page))
        if page == 1:
            return [
                CrmContactSnapshot("5", "", None),
                CrmContactSnapshot("6", "", None),
                CrmContactSnapshot("5", "", None),  # duplicate id across two leads
            ]
        return []

    async def fetch_contacts_by_ids(self, conn, *, contact_ids):
        self.calls.append(("hydrate", tuple(contact_ids)))
        return [
            CrmContactSnapshot("5", "Ali", "+998901112233"),
            CrmContactSnapshot("6", "Vali", None),
        ]

    async def fetch_contacts(self, conn, *, page):
        raise AssertionError("stage segment must not page the full contact list")


async def test_stage_segment_resolves_via_leads_and_hydrates(db_session, workspace):
    conn = await _conn(db_session, workspace)
    provider = _StageProvider()
    orch = CampaignOrchestrator(db_session, provider=provider)
    campaign = await orch.create_campaign(
        workspace_id=workspace.id, connection_id=conn.id, name="C", goal="reactivate",
        segment=SegmentSpec(pipeline_id="777", stage_ids=("111",)), base_message="Salom!")
    n = await orch.materialize(campaign)

    assert n == 1  # Vali has no phone -> skipped
    assert ("leads", "777", ("111",), 1) in provider.calls
    assert [c for c in provider.calls if c[0] == "hydrate"] == [("hydrate", ("5", "6"))]
    target = (await db_session.execute(select(OutreachTarget))).scalar_one()
    assert target.phone == "+998901112233"
    assert (campaign.segment_spec or {}).get("pipeline_id") == "777"


async def test_stage_segment_without_pipeline_id_is_loud(db_session, workspace):
    conn = await _conn(db_session, workspace)
    orch = CampaignOrchestrator(db_session, provider=_StageProvider())
    campaign = await orch.create_campaign(
        workspace_id=workspace.id, connection_id=conn.id, name="C", goal="reactivate",
        segment=SegmentSpec(stage_ids=("111",)), base_message="Salom!")
    with pytest.raises(ValueError):
        await orch.materialize(campaign)
