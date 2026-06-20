"""Owner-bot binding web endpoints (#451): bind-link, manual token, unbind."""

import pytest

pytestmark = pytest.mark.asyncio

BASE = "/api/agent-control/owner-bot"


async def test_bind_link_409_without_bot(client, auth_headers):
    r = await client.post(f"{BASE}/bind-link", headers=auth_headers)
    assert r.status_code == 409


async def test_bind_link_mints_when_provisioned(client, auth_headers, workspace, db_session):
    workspace.control_bot_username = "oqim_ws_bot"
    workspace.control_bot_token = "111:AAA"
    await db_session.flush()
    r = await client.post(f"{BASE}/bind-link", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["deep_link"].startswith("https://t.me/oqim_ws_bot?start=")


async def test_bind_link_requires_auth(client):
    r = await client.post(f"{BASE}/bind-link")
    assert r.status_code in (401, 403)


async def test_unbind_clears(client, auth_headers, workspace, db_session):
    workspace.owner_control_chat_id = 555
    await db_session.flush()
    r = await client.post(f"{BASE}/unbind", headers=auth_headers)
    assert r.status_code == 200
    await db_session.refresh(workspace)
    assert workspace.owner_control_chat_id is None


async def test_manual_token_rejects_duplicate(client, auth_headers, workspace, workspace_b, db_session, monkeypatch):
    # another workspace already holds the token -> 409
    workspace_b.control_bot_token = "222:BBB"
    await db_session.flush()

    async def fake_get_me(token):
        return {"id": 222, "username": "dup_bot"}

    monkeypatch.setattr("app.api.routes.agent_control._bot_get_me", fake_get_me)
    r = await client.post(f"{BASE}/token", headers=auth_headers, json={"token": "222:BBB"})
    assert r.status_code == 409


async def test_manual_token_stores_on_valid(client, auth_headers, workspace, db_session, monkeypatch):
    async def fake_get_me(token):
        return {"id": 333, "username": "fresh_bot"}

    monkeypatch.setattr("app.api.routes.agent_control._bot_get_me", fake_get_me)
    r = await client.post(f"{BASE}/token", headers=auth_headers, json={"token": "333:CCC"})
    assert r.status_code == 200
    await db_session.refresh(workspace)
    assert workspace.control_bot_username == "fresh_bot"
    assert workspace.control_bot_user_id == 333
