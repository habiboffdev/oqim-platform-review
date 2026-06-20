"""rediscover_connection: merge (preserve webhook), idempotent, drift -> card."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.modules.crm_connector.contracts import CrmAccountSchema, CrmPipeline, CrmPipelineStatus
from app.modules.crm_connector.rediscovery import rediscover_connection

pytestmark = pytest.mark.asyncio


class _Provider:
    def __init__(self, schema):
        self._schema = schema
    async def discover_schema(self, conn):
        return self._schema


def _schema(pid="111", stage_id="201"):
    return CrmAccountSchema(pipelines=[CrmPipeline(
        pipeline_id=pid, name="Main", is_main=True,
        statuses=[CrmPipelineStatus(stage_id=stage_id, name="First", sort=10, kind="active")])])


async def _conn(db_session, workspace, config):
    conn = CrmConnection(
        workspace_id=workspace.id, provider="amocrm", status="active",
        provider_account_ref="mybiz", webhook_token="tok-rd", pipeline_config=config)
    db_session.add(conn)
    await db_session.flush()
    return conn


async def _cards(db_session, workspace_id):
    return (await db_session.execute(
        select(BusinessBrainProjectionRecord).where(
            BusinessBrainProjectionRecord.workspace_id == workspace_id,
            BusinessBrainProjectionRecord.projection_type == "owner_notification"))).scalars().all()


async def test_rediscover_upgrades_flat_config_and_preserves_webhook(db_session, workspace):
    conn = await _conn(db_session, workspace, {
        "pipeline_id": "111", "stage_map": {"new": {"stage_id": "201", "sort": 10}},
        "webhook": {"id": "98765", "events": ["status_lead"]}})
    changed = await rediscover_connection(db_session, conn, _Provider(_schema()))
    assert changed is True
    cfg = conn.pipeline_config
    assert cfg["mapping"]["default_pipeline_id"] == "111"          # upgraded flat -> nested
    assert cfg["snapshot"]["pipelines"][0]["id"] == "111"
    assert cfg["webhook"] == {"id": "98765", "events": ["status_lead"]}  # carried over


async def test_rediscover_unchanged_schema_is_noop(db_session, workspace):
    conn = await _conn(db_session, workspace, {})
    assert await rediscover_connection(db_session, conn, _Provider(_schema())) is True   # first: writes
    before = conn.pipeline_config
    assert await rediscover_connection(db_session, conn, _Provider(_schema())) is False  # second: no-op
    assert conn.pipeline_config == before


async def test_rediscover_drift_on_removed_referenced_stage_cards_owner(db_session, workspace, customer):
    # seed nested config + a lead pinned to stage 201
    conn = await _conn(db_session, workspace, {})
    await rediscover_connection(db_session, conn, _Provider(_schema(stage_id="201")))
    link = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id, conversation_id=1, customer_id=customer.id,
        pipeline_id="111", last_synced_stage_id="201")
    db_session.add(link)
    await db_session.flush()
    # re-discover with stage 201 GONE (renamed id)
    await rediscover_connection(db_session, conn, _Provider(_schema(stage_id="999")))
    cards = await _cards(db_session, workspace.id)
    assert len(cards) == 1


# --- S2 hardening (cross-seam review remediation) -------------------------------
def _pilot_schema(stages=None):
    """Pilot-shaped main pipeline: 4 active stages + won/lost (mirrors prod ws1)."""
    statuses = stages or [
        CrmPipelineStatus(stage_id="86476602", name="Birlamchi", sort=20, kind="active"),
        CrmPipelineStatus(stage_id="86476606", name="Muzokara", sort=30, kind="active"),
        CrmPipelineStatus(stage_id="86476610", name="Qaror", sort=40, kind="active"),
        CrmPipelineStatus(stage_id="86476614", name="Shartnoma", sort=50, kind="active"),
        CrmPipelineStatus(stage_id="142", name="Won", sort=10000, kind="won"),
        CrmPipelineStatus(stage_id="143", name="Lost", sort=11000, kind="lost"),
    ]
    return CrmAccountSchema(pipelines=[CrmPipeline(
        pipeline_id="11001426", name="Voronka", is_main=True, statuses=statuses)])


_PILOT_FLAT = {
    "webhook": {"id": "47860038", "events": ["status_lead"]},
    "pipeline_id": "11001426",
    "stage_map": {
        "new": {"sort": 20, "stage_id": "86476602"},
        "negotiation": {"sort": 30, "stage_id": "86476606"},
        "qualified": {"sort": 40, "stage_id": "86476610"},
        "won": {"sort": 10000, "stage_id": "142"},
        "lost": {"sort": 11000, "stage_id": "143"},
    },
    "pipeline_snapshot": [
        {"kind": "active", "name": "Birlamchi", "sort": 20, "stage_id": "86476602"},
        {"kind": "active", "name": "Muzokara", "sort": 30, "stage_id": "86476606"},
        {"kind": "active", "name": "Qaror", "sort": 40, "stage_id": "86476610"},
        {"kind": "active", "name": "Shartnoma", "sort": 50, "stage_id": "86476614"},
        {"kind": "won", "name": "Won", "sort": 10000, "stage_id": "142"},
        {"kind": "lost", "name": "Lost", "sort": 11000, "stage_id": "143"},
    ],
}


async def test_rediscover_legacy_flat_preserves_existing_role_map(db_session, workspace):
    """CRITICAL regression guard: upgrading the live pilot's flat config must NOT
    re-derive a different role->stage map (negotiation/qualified must stay put)."""
    conn = await _conn(db_session, workspace, dict(_PILOT_FLAT))
    changed = await rediscover_connection(db_session, conn, _Provider(_pilot_schema()))
    assert changed is True
    rm = conn.pipeline_config["mapping"]["pipelines"]["11001426"]["role_map"]
    assert rm["new"]["stage_id"] == "86476602"
    assert rm["negotiation"]["stage_id"] == "86476606"   # preserved, NOT re-derived to 86476610
    assert rm["qualified"]["stage_id"] == "86476610"     # preserved, NOT re-derived to 86476614
    assert conn.pipeline_config["webhook"]["id"] == "47860038"
    snap_ids = {s["stage_id"] for s in conn.pipeline_config["snapshot"]["pipelines"][0]["statuses"]}
    assert "86476614" in snap_ids                        # snapshot still refreshed


async def test_rediscover_empty_discovery_does_not_wipe_config(db_session, workspace):
    """A transient empty/partial discovery must be a no-op: never overwrite a good
    config, never fire a false drift card."""
    conn = await _conn(db_session, workspace, dict(_PILOT_FLAT))
    before = dict(conn.pipeline_config)
    assert await rediscover_connection(db_session, conn, _Provider(CrmAccountSchema(pipelines=[]))) is False
    assert conn.pipeline_config == before
    assert await _cards(db_session, workspace.id) == []


async def test_rediscover_drift_from_legacy_flat_cards_owner(db_session, workspace, customer):
    """H4 guard: drift is detected even when the OLD config is the legacy FLAT shape
    (no 'snapshot' key) — the only shape that exists in prod today."""
    conn = await _conn(db_session, workspace, dict(_PILOT_FLAT))
    link = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id, conversation_id=1, customer_id=customer.id,
        pipeline_id="11001426", last_synced_stage_id="86476606")
    db_session.add(link)
    await db_session.flush()
    # 86476606 (the stage the live lead is pinned to) is REMOVED in the new schema
    shrunk = _pilot_schema(stages=[
        CrmPipelineStatus(stage_id="86476602", name="Birlamchi", sort=20, kind="active"),
        CrmPipelineStatus(stage_id="86476610", name="Qaror", sort=40, kind="active"),
        CrmPipelineStatus(stage_id="142", name="Won", sort=10000, kind="won"),
        CrmPipelineStatus(stage_id="143", name="Lost", sort=11000, kind="lost"),
    ])
    await rediscover_connection(db_session, conn, _Provider(shrunk))
    assert len(await _cards(db_session, workspace.id)) == 1


async def test_rediscover_renamed_stage_does_not_card(db_session, workspace, customer):
    """A renamed/re-sorted stage (id unchanged) refreshes the snapshot but is NOT
    drift -> no owner card (spec §6)."""
    conn = await _conn(db_session, workspace, dict(_PILOT_FLAT))
    link = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id, conversation_id=1, customer_id=customer.id,
        pipeline_id="11001426", last_synced_stage_id="86476606")
    db_session.add(link)
    await db_session.flush()
    renamed = _pilot_schema(stages=[
        CrmPipelineStatus(stage_id="86476602", name="Birlamchi NEW", sort=20, kind="active"),
        CrmPipelineStatus(stage_id="86476606", name="Muzokara RENAMED", sort=35, kind="active"),
        CrmPipelineStatus(stage_id="86476610", name="Qaror", sort=40, kind="active"),
        CrmPipelineStatus(stage_id="86476614", name="Shartnoma", sort=50, kind="active"),
        CrmPipelineStatus(stage_id="142", name="Won", sort=10000, kind="won"),
        CrmPipelineStatus(stage_id="143", name="Lost", sort=11000, kind="lost"),
    ])
    changed = await rediscover_connection(db_session, conn, _Provider(renamed))
    assert changed is True                               # snapshot refreshed (name/sort changed)
    assert await _cards(db_session, workspace.id) == []  # but no drift card


async def test_rediscover_card_failure_keeps_config_and_session_usable(
    db_session, workspace, customer, monkeypatch
):
    """A drift owner-card failure must NOT undo the committed config refresh, must be
    swallowed, and must leave the session usable (rolled back, not poisoned)."""
    import app.modules.crm_connector.rediscovery as rd

    conn = await _conn(db_session, workspace, dict(_PILOT_FLAT))
    link = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id, conversation_id=1, customer_id=customer.id,
        pipeline_id="11001426", last_synced_stage_id="86476606")
    db_session.add(link)
    await db_session.flush()

    async def _boom(*a, **k):
        raise RuntimeError("card backend down")

    monkeypatch.setattr(rd, "queue_crm_owner_notification", _boom)
    shrunk = _pilot_schema(stages=[                       # 86476606 removed -> drift -> card attempted
        CrmPipelineStatus(stage_id="86476602", name="A", sort=20, kind="active"),
        CrmPipelineStatus(stage_id="86476610", name="B", sort=40, kind="active"),
        CrmPipelineStatus(stage_id="142", name="Won", sort=10000, kind="won"),
        CrmPipelineStatus(stage_id="143", name="Lost", sort=11000, kind="lost"),
    ])
    changed = await rediscover_connection(db_session, conn, _Provider(shrunk))  # must NOT raise
    assert changed is True
    stage_ids = {s["stage_id"] for s in conn.pipeline_config["snapshot"]["pipelines"][0]["statuses"]}
    assert "86476606" not in stage_ids                    # config refresh landed despite card failure
    # session usable after the card rollback: a follow-up query succeeds + no card persisted
    assert await _cards(db_session, workspace.id) == []
