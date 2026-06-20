"""Owner control bot — owner-config card text + free-text routing (spike #439)."""

from typing import Any

from app.modules.commercial_spine.contracts import CommercialActionProposal
from app.modules.telegram_control_bot.service import (
    TelegramControlBotService,
    _owner_card_text,
)


class _FakeBotClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, *, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append({"chat_id": chat_id, "text": text})
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    async def edit_message_reply_markup(self, **_):
        return {"ok": True, "result": True}

    async def answer_callback_query(self, **_):
        return {"ok": True, "result": True}


def _owner_config_proposal(section_key: str = "role_mission") -> CommercialActionProposal:
    return CommercialActionProposal(
        proposal_id="owner_config:card-1",
        workspace_id=1,
        conversation_id=0,
        customer_id=0,
        action_type="agent.update_owner_config",
        lifecycle_state="proposed",
        execution_mode="suggest_only",
        risk_level="medium",
        requires_approval=True,
        priority="medium",
        confidence=1.0,
        reason_code="owner_config_edit",
        source_refs=["owner:setup_agent:1"],
        idempotency_key="idem:card-1",
        payload={
            "op": "edit_doc",
            "agent_id": 1,
            "section_key": section_key,
            "body": "Yangi matn",
        },
    )


def test_owner_config_card_text():
    text = _owner_card_text(
        action_kind="agent.update_owner_config", proposal=_owner_config_proposal()
    )
    # readable Uzbek card that names the section being changed
    assert "Tasdiqlash" in text
    assert "Bo'lim" in text
    # no internal debris leaked to the owner
    assert "hermes_run" not in text
    assert "agent_session" not in text
    # did not fall through to the generic "Target:/Risk:" fallback
    assert "Target:" not in text


async def test_free_text_routes_to_owner_turn(db_session, workspace, agent, monkeypatch):
    from app.modules.telegram_control_bot.bind_token_service import BindTokenService

    agent.agent_type = "setup"  # owner turns require a setup agent
    await db_session.flush()
    client = _FakeBotClient()
    service = TelegramControlBotService(
        session=db_session, client=client, bound_workspace_id=workspace.id
    )

    # bare /start returns binding instructions
    started = await service.handle_owner_message(
        {"message": {"chat": {"id": 555}, "text": "/start"}}
    )
    assert started["action"] == "start_instructions"

    # a one-time deep-link token binds the workspace on its dedicated lane
    token = await BindTokenService(db_session).mint(workspace_id=workspace.id)
    await db_session.flush()
    bound = await service.handle_owner_message(
        {"message": {"chat": {"id": 555}, "text": f"/start {token}"}}
    )
    assert bound["action"] == "owner_bound"

    spy: dict[str, Any] = {}

    async def fake_dispatch(
        *, db, workspace_id, agent_id, owner_chat_id, message_text, delivery
    ):
        spy.update(
            workspace_id=workspace_id,
            agent_id=agent_id,
            owner_chat_id=owner_chat_id,
            message_text=message_text,
        )
        return True

    monkeypatch.setattr(
        "app.modules.telegram_control_bot.service.dispatch_owner_turn", fake_dispatch
    )

    free_text = "Operator kutayotganlarga intro videolarni yubor, lekin oferta avval"
    result = await service.handle_owner_message(
        {"message": {"chat": {"id": 555}, "text": free_text}}
    )

    assert result["ok"] is True
    assert result["action"] == "owner_turn"
    assert result["workspace_id"] == workspace.id
    assert spy["message_text"] == free_text
    assert spy["owner_chat_id"] == 555
    assert spy["workspace_id"] == workspace.id
    assert spy["agent_id"] == agent.id


async def test_unbound_free_text_is_still_ignored(db_session, workspace, monkeypatch):
    """Loop-guard: an UNBOUND chat's free text must never route (conv 4, 2026-06-10)."""
    client = _FakeBotClient()
    service = TelegramControlBotService(session=db_session, client=client)

    called = {"hit": False}

    async def fake_dispatch(**_):
        called["hit"] = True
        return True

    monkeypatch.setattr(
        "app.modules.telegram_control_bot.service.dispatch_owner_turn", fake_dispatch
    )

    result = await service.handle_owner_message(
        {"message": {"chat": {"id": 888}, "text": "tasodifiy xabar"}}
    )
    assert result["reason"] == "not_a_binding_message"
    assert called["hit"] is False


async def test_bound_owner_without_owner_agent_autocreates_one_never_seller(
    db_session, workspace, agent, monkeypatch
):
    """A bound owner whose workspace has only the default seller agent gets an
    Owner Agent auto-created (Phase 0a, #455) and the turn routes to IT — never
    to the selling agent. This preserves the #439 no-silent-seller-fallback
    safety property: `_resolve_owner_agent` only ever picks owner/setup types."""
    agent.agent_type = "customer"  # the default/seller agent — NOT an owner agent
    workspace.owner_control_chat_id = 555  # already token-bound (bind flow tested elsewhere)
    await db_session.flush()
    client = _FakeBotClient()
    service = TelegramControlBotService(
        session=db_session, client=client, bound_workspace_id=workspace.id
    )

    spy: dict[str, Any] = {}

    async def fake_dispatch(
        *, db, workspace_id, agent_id, owner_chat_id, message_text, delivery
    ):
        spy.update(agent_id=agent_id)
        return True

    monkeypatch.setattr(
        "app.modules.telegram_control_bot.service.dispatch_owner_turn", fake_dispatch
    )

    result = await service.handle_owner_message(
        {"message": {"chat": {"id": 555}, "text": "AGENT.md ni yangila iltimos"}}
    )
    assert result["action"] == "owner_turn"
    # routed to an auto-created OWNER agent, never the seller/customer agent
    assert spy["agent_id"] != agent.id
    owner_agent = await service._resolve_owner_agent(workspace.id)
    assert owner_agent is not None
    assert owner_agent.agent_type == "owner"
    assert spy["agent_id"] == owner_agent.id


async def test_setup_agent_preferred_when_resolving_owner_agent(
    db_session, workspace, agent
):
    """_resolve_owner_agent picks the setup agent, never the default seller."""
    from app.models.agent import Agent as AgentModel

    # `agent` fixture is the default (is_default=True, agent_type=customer).
    setup_agent = AgentModel(
        workspace_id=workspace.id,
        name="Setup",
        is_default=False,
        agent_type="setup",
        persona={},
        instructions="setup",
        trust_mode="disabled",
        channel_config={},
    )
    db_session.add(setup_agent)
    await db_session.flush()

    service = TelegramControlBotService(session=db_session, client=_FakeBotClient())
    resolved = await service._resolve_owner_agent(workspace.id)
    assert resolved is not None
    assert resolved.id == setup_agent.id
    assert resolved.agent_type == "setup"
