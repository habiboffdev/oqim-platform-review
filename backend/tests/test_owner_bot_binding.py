"""Owner-bot token binding + phone-branch removal + lane-gated routing (#451)."""

import pytest

from app.models.workspace import Workspace
from app.modules.telegram_control_bot.bind_token_service import BindTokenService
from app.modules.telegram_control_bot.service import TelegramControlBotService

pytestmark = pytest.mark.asyncio


class _FakeBotClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, *, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append({"chat_id": chat_id, "text": text})
        return {"ok": True, "result": {"message_id": len(self.sent)}}


def _msg(text, chat_id=555):
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def _svc(db_session, *, bound_workspace_id):
    return TelegramControlBotService(
        session=db_session, client=_FakeBotClient(), bound_workspace_id=bound_workspace_id
    )


async def test_start_token_binds_on_dedicated_lane(db_session, workspace):
    token = await BindTokenService(db_session).mint(workspace_id=workspace.id)
    await db_session.flush()
    res = await _svc(db_session, bound_workspace_id=workspace.id).handle_owner_message(
        _msg(f"/start {token}", chat_id=555)
    )
    assert res["ok"] is True and res["action"] == "owner_bound"
    ws = await db_session.get(Workspace, workspace.id)
    assert ws.owner_control_chat_id == 555


async def test_unknown_token_does_not_bind(db_session, workspace):
    res = await _svc(db_session, bound_workspace_id=workspace.id).handle_owner_message(
        _msg("/start not-a-real-token", chat_id=555)
    )
    assert res["ok"] is False and res["reason"] == "bind_failed"
    ws = await db_session.get(Workspace, workspace.id)
    assert ws.owner_control_chat_id != 555


async def test_phone_number_no_longer_binds(db_session, workspace):
    res = await _svc(db_session, bound_workspace_id=workspace.id).handle_owner_message(
        _msg(workspace.phone_number, chat_id=42)
    )
    ws = await db_session.get(Workspace, workspace.id)
    assert ws.owner_control_chat_id != 42  # phone path deleted
    assert res["reason"] == "not_a_binding_message"


async def test_bare_start_gives_instructions_no_bind(db_session, workspace):
    res = await _svc(db_session, bound_workspace_id=workspace.id).handle_owner_message(
        _msg("/start", chat_id=7)
    )
    assert res["action"] == "start_instructions"
    ws = await db_session.get(Workspace, workspace.id)
    assert ws.owner_control_chat_id != 7


async def test_global_lane_never_routes_free_text(db_session, workspace):
    workspace.owner_control_chat_id = 555
    await db_session.flush()
    res = await _svc(db_session, bound_workspace_id=None).handle_owner_message(
        _msg("salom", chat_id=555)
    )
    assert res["reason"] == "not_a_binding_message"


async def test_bound_owner_free_text_routes_on_dedicated_lane(
    db_session, workspace, agent, monkeypatch
):
    agent.agent_type = "setup"
    workspace.owner_control_chat_id = 555
    await db_session.flush()
    spy = {}

    async def fake_dispatch(*, db, workspace_id, agent_id, owner_chat_id, message_text, delivery):
        spy.update(workspace_id=workspace_id, owner_chat_id=owner_chat_id, message_text=message_text)
        return True

    monkeypatch.setattr(
        "app.modules.telegram_control_bot.service.dispatch_owner_turn", fake_dispatch
    )
    res = await _svc(db_session, bound_workspace_id=workspace.id).handle_owner_message(
        _msg("intro videolarni yubor", chat_id=555)
    )
    assert res["ok"] is True and res["action"] == "owner_turn"
    assert spy["workspace_id"] == workspace.id and spy["owner_chat_id"] == 555


async def test_campaign_keys_off_lane_not_chat_id(db_session, workspace, workspace_b):
    """One chat bound to two workspaces: /campaign on workspace_b's lane never
    acts on workspace A (the old chat-id lookup did)."""
    workspace.owner_control_chat_id = 555
    workspace_b.owner_control_chat_id = 555
    await db_session.flush()
    res = await _svc(db_session, bound_workspace_id=workspace_b.id).handle_owner_message(
        _msg("/campaign", chat_id=555)
    )
    assert res["ok"] is True
    assert res["workspace_id"] == workspace_b.id
    assert res["workspace_id"] != workspace.id
