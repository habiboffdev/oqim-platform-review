"""OwnerBindToken + OwnerBindEvent models (#451)."""

import pytest
from sqlalchemy import select

from app.db.base import utc_now
from app.models.owner_bind_token import OwnerBindEvent, OwnerBindToken

pytestmark = pytest.mark.asyncio


async def test_owner_bind_token_persists(db_session, workspace):
    tok = OwnerBindToken(workspace_id=workspace.id, token="abc123", expires_at=utc_now())
    db_session.add(tok)
    await db_session.flush()
    row = (
        await db_session.execute(
            select(OwnerBindToken).where(OwnerBindToken.token == "abc123")
        )
    ).scalar_one()
    assert row.workspace_id == workspace.id
    assert row.used_at is None and row.bound_chat_id is None


async def test_owner_bind_event_persists(db_session, workspace):
    ev = OwnerBindEvent(workspace_id=workspace.id, event_type="mint")
    db_session.add(ev)
    await db_session.flush()
    row = (await db_session.execute(select(OwnerBindEvent))).scalars().first()
    assert row.event_type == "mint"
