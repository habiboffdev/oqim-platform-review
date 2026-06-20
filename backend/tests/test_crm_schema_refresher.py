"""CrmSchemaRefresher polls active connections; one failure never blocks others."""
from __future__ import annotations

import pytest

from app.models.crm_connection import CrmConnection
from app.modules.crm_connector.schema_refresher import CrmSchemaRefresher

pytestmark = pytest.mark.asyncio


class _Provider:
    def __init__(self, fail=False):
        self.fail = fail
    async def discover_schema(self, conn):
        if self.fail:
            raise RuntimeError("amocrm down")
        from app.modules.crm_connector.contracts import (
            CrmAccountSchema,
            CrmPipeline,
            CrmPipelineStatus,
        )
        return CrmAccountSchema(pipelines=[CrmPipeline(
            pipeline_id="111", name="Main", is_main=True,
            statuses=[CrmPipelineStatus(stage_id="201", name="First", sort=10, kind="active")])])


async def _conn(db_session, workspace, *, fail_token):
    # provider_account_ref must be unique per active connection (the partial unique
    # index), so derive it from the unique fail_token.
    conn = CrmConnection(
        workspace_id=workspace.id, provider="amocrm", status="active",
        provider_account_ref=fail_token, webhook_token=fail_token, pipeline_config={})
    db_session.add(conn)
    await db_session.flush()
    return conn


async def test_refresh_due_good_refreshes(db_session, workspace):
    await _conn(db_session, workspace, fail_token="t1")
    worker = CrmSchemaRefresher(db_factory=None, provider_factory=lambda _n: _Provider(fail=False))
    assert await worker.refresh_due(db_session) == 1


async def test_refresh_due_swallows_provider_failure(db_session, workspace):
    await _conn(db_session, workspace, fail_token="t2")
    worker = CrmSchemaRefresher(db_factory=None, provider_factory=lambda _n: _Provider(fail=True))
    assert await worker.refresh_due(db_session) == 0   # no raise, skipped


class _RoutingProvider:
    """Fails discovery for a set of connection ids, succeeds for the rest — to
    prove a failing connection never BLOCKS a later one in the same tick."""
    def __init__(self, fail_conn_ids):
        self._fail = set(fail_conn_ids)
    async def discover_schema(self, conn):
        if conn.id in self._fail:
            raise RuntimeError("amocrm down")
        from app.modules.crm_connector.contracts import (
            CrmAccountSchema,
            CrmPipeline,
            CrmPipelineStatus,
        )
        return CrmAccountSchema(pipelines=[CrmPipeline(
            pipeline_id="111", name="Main", is_main=True,
            statuses=[CrmPipelineStatus(stage_id="201", name="First", sort=10, kind="active")])])


async def test_refresh_due_isolates_a_failing_connection(db_session, workspace, workspace_b):
    # conn A (lower id, processed first) FAILS; conn B must still refresh.
    a = await _conn(db_session, workspace, fail_token="t-bad")
    b = await _conn(db_session, workspace_b, fail_token="t-good")
    routing = _RoutingProvider(fail_conn_ids={a.id})
    worker = CrmSchemaRefresher(db_factory=None, provider_factory=lambda _n: routing)
    changed = await worker.refresh_due(db_session)
    await db_session.refresh(b)
    assert changed == 1                              # only B refreshed
    assert "snapshot" in (b.pipeline_config or {})   # B processed AFTER A failed
    assert a.pipeline_config == {}                    # A untouched
