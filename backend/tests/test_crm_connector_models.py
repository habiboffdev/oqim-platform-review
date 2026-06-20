"""crm_connections + crm_lead_links schema invariants."""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.crm_connection import CrmConnection, CrmLeadLink

pytestmark = pytest.mark.asyncio


async def _connection(db_session, workspace, **over):
    conn = CrmConnection(
        workspace_id=workspace.id,
        provider="amocrm",
        status=over.pop("status", "active"),
        provider_account_ref="mybiz",
        webhook_token=over.pop("webhook_token", "tok-1"),
        pipeline_config={},
        **over,
    )
    db_session.add(conn)
    await db_session.flush()
    return conn


async def test_one_active_connection_per_workspace(db_session, workspace):
    await _connection(db_session, workspace)
    with pytest.raises(IntegrityError):
        await _connection(db_session, workspace, webhook_token="tok-2")


async def test_disconnected_rows_do_not_block_reconnect(db_session, workspace):
    await _connection(db_session, workspace, status="disconnected")
    await _connection(db_session, workspace, webhook_token="tok-2")  # no raise


async def test_one_lead_link_per_conversation_per_connection(db_session, workspace):
    conn = await _connection(db_session, workspace)
    db_session.add(
        CrmLeadLink(
            workspace_id=workspace.id,
            connection_id=conn.id,
            conversation_id=7,
            customer_id=3,
        )
    )
    await db_session.flush()
    with pytest.raises(IntegrityError):
        db_session.add(
            CrmLeadLink(
                workspace_id=workspace.id,
                connection_id=conn.id,
                conversation_id=7,
                customer_id=3,
            )
        )
        await db_session.flush()


async def test_token_columns_hold_long_jwts(db_session, workspace):
    conn = await _connection(
        db_session, workspace, access_token="x" * 2000, refresh_token="y" * 2000
    )
    assert len(conn.access_token) == 2000


async def test_lead_link_has_pending_field_ops_default_empty(db_session, workspace):
    conn = await _connection(db_session, workspace)
    link = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id,
        conversation_id=1, customer_id=1,
    )
    db_session.add(link)
    await db_session.flush()
    assert link.pending_field_ops == []


async def test_lead_link_pipeline_id_optional_and_settable(db_session, workspace):
    conn = await _connection(db_session, workspace)
    link = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id,
        conversation_id=11, customer_id=4, pipeline_id="222",
    )
    db_session.add(link)
    await db_session.flush()
    assert link.pipeline_id == "222"
    # legacy links carry no pipeline (defaults None) -> the read shim resolves the default.
    link2 = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id,
        conversation_id=12, customer_id=4,
    )
    db_session.add(link2)
    await db_session.flush()
    assert link2.pipeline_id is None
