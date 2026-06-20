"""Owner control-bot delivery: binding, notification flush, approval cards.

Live motivation (2026-06-09 pilot): the agent recorded `owner.notify` and an
approval-gated call task for a hot lead, but nothing delivered them — the
owner was never told to call the customer. These tests pin the worker that
closes that dead-end (plan: 2026-06-10-owner-control-bot-delivery.md).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import select

from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.modules.commercial_spine.contracts import (
    BusinessBrainProjection,
    CommercialActionProposal,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.telegram_control_bot.service import TelegramControlBotService
from app.modules.telegram_control_bot.worker import OwnerControlBotWorker

pytestmark = pytest.mark.asyncio


class _FakeBotClient:
    def __init__(self, updates: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.cards: list[dict[str, Any]] = []
        self._updates = list(updates or [])

    async def send_message(self, *, chat_id, text, reply_markup=None, parse_mode=None):
        entry = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            self.cards.append(entry)
        self.sent.append(entry)
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    async def get_updates(self, *, offset=None, timeout_seconds=2):
        updates, self._updates = self._updates, []
        return updates

    async def edit_message_reply_markup(self, **_):
        return {"ok": True, "result": True}

    async def answer_callback_query(self, **_):
        return {"ok": True, "result": True}


def _worker(db_session, client: _FakeBotClient) -> OwnerControlBotWorker:
    def factory():
        @asynccontextmanager
        async def _cm():
            yield db_session

        return _cm()

    return OwnerControlBotWorker(db_factory=factory, client=client)


async def _queue_notification(db_session, workspace, *, ref="owner_notification:test:1"):
    await CommercialSpineRepository(db_session).upsert_projection(
        BusinessBrainProjection(
            projection_ref=ref,
            workspace_id=workspace.id,
            projection_type="owner_notification",
            entity_ref="agent_session:1",
            state={
                "status": "queued",
                "channel": "owner_bot",
                "bot_payload": {
                    "title": "Yangi lead: Jasur",
                    "summary": "Mijoz raqam qoldirdi: +998901635207",
                    "recommended_action": "Qo'ng'iroq qilib ro'yxatdan o'tkazish",
                },
            },
            source_refs=["agent_session:1"],
        )
    )
    await db_session.flush()


async def test_phone_message_no_longer_binds(db_session, workspace):
    client = _FakeBotClient()
    service = TelegramControlBotService(session=db_session, client=client)

    result = await service.handle_owner_message(
        {"message": {"chat": {"id": 777}, "text": "+998000000000"}}
    )

    assert result["ok"] is False
    await db_session.refresh(workspace)
    assert workspace.owner_control_chat_id is None


async def test_flush_delivers_queued_notification_once(db_session, workspace):
    workspace.owner_control_chat_id = 555
    await db_session.flush()
    await _queue_notification(db_session, workspace)
    client = _FakeBotClient()
    worker = _worker(db_session, client)

    clients = {workspace.id: client}
    assert await worker._flush_notifications(clients) == 1
    assert "Yangi lead: Jasur" in client.sent[0]["text"]
    assert "Keyingi qadam" in client.sent[0]["text"]
    row = (
        await db_session.execute(
            select(BusinessBrainProjectionRecord).where(
                BusinessBrainProjectionRecord.projection_ref == "owner_notification:test:1"
            )
        )
    ).scalar_one()
    assert row.state["status"] == "delivered"
    assert row.state["delivered_chat_id"] == 555

    # already delivered -> nothing flushed again
    assert await worker._flush_notifications(clients) == 0
    assert len(client.sent) == 1


async def test_flush_keeps_notification_queued_until_owner_binds(db_session, workspace):
    await _queue_notification(db_session, workspace)
    client = _FakeBotClient()
    worker = _worker(db_session, client)

    assert await worker._flush_notifications({workspace.id: client}) == 0
    assert client.sent == []
    row = (
        await db_session.execute(
            select(BusinessBrainProjectionRecord).where(
                BusinessBrainProjectionRecord.projection_ref == "owner_notification:test:1"
            )
        )
    ).scalar_one()
    assert row.state["status"] == "queued"


async def test_approval_card_pushed_exactly_once(db_session, workspace):
    workspace.owner_control_chat_id = 555
    await db_session.flush()
    await CommercialSpineRepository(db_session).persist_action_proposal(
        CommercialActionProposal(
            proposal_id="agent_control:test-task",
            workspace_id=workspace.id,
            conversation_id=0,
            customer_id=0,
            action_type="create_business_task",
            lifecycle_state="proposed",
            execution_mode="suggest_only",
            risk_level="low",
            requires_approval=True,
            priority="medium",
            confidence=0.9,
            reason_code="agent_created_owner_task",
            source_refs=["agent_session:1"],
            payload={"owner_task": {"title": "Kursga qiziqqan mijoz: Jasur"}},
            idempotency_key="test-task-key",
        )
    )
    await db_session.flush()
    client = _FakeBotClient()
    worker = _worker(db_session, client)

    clients = {workspace.id: client}
    assert await worker._push_approval_cards(clients) == 1
    assert len(client.cards) == 1
    keyboard = client.cards[0]["reply_markup"]["inline_keyboard"][0]
    assert [button["text"] for button in keyboard] == ["\u2705 Tasdiqlash", "\u274c Rad etish"]
    # the owner reads human text, never internal refs
    card_text = client.cards[0]["text"]
    assert "Kursga qiziqqan mijoz: Jasur" in card_text
    assert "Tasdiqlash kerak" in card_text
    for forbidden in ("hermes_run", "agent_session", "conversation:", "Evidence"):
        assert forbidden not in card_text

    # idempotent: card is not re-sent on the next tick
    assert await worker._push_approval_cards(clients) == 0
    assert len(client.cards) == 1


async def test_approve_callback_executes_for_business_task(db_session, workspace):
    workspace.owner_control_chat_id = 555
    await db_session.flush()
    await CommercialSpineRepository(db_session).persist_action_proposal(
        CommercialActionProposal(
            proposal_id="agent_control:approve-me",
            workspace_id=workspace.id,
            conversation_id=0,
            customer_id=0,
            action_type="create_business_task",
            lifecycle_state="proposed",
            execution_mode="suggest_only",
            risk_level="low",
            requires_approval=True,
            priority="medium",
            confidence=0.9,
            reason_code="agent_created_owner_task",
            source_refs=["agent_session:1"],
            payload={"owner_task": {"title": "Call the lead"}},
            idempotency_key="approve-me-key",
        )
    )
    await db_session.flush()
    client = _FakeBotClient()
    service = TelegramControlBotService(session=db_session, client=client)

    result = await service.handle_update(
        {
            "callback_query": {
                "id": "cb1",
                "from": {"id": 555},
                "data": f"oqim:a:{workspace.id}:agent_control:approve-me",
                "message": {"chat": {"id": 555}, "message_id": 7},
            }
        }
    )

    assert result.ok is True
    assert result.action == "approve"
    assert result.action_kind == "create_business_task"


async def test_drain_updates_binds_via_token_on_dedicated_lane(db_session, workspace):
    from app.modules.telegram_control_bot.bind_token_service import BindTokenService

    token = await BindTokenService(db_session).mint(workspace_id=workspace.id)
    await db_session.flush()
    client = _FakeBotClient(
        updates=[
            {
                "update_id": 41,
                "message": {"chat": {"id": 999}, "text": f"/start {token}"},
            }
        ]
    )
    worker = _worker(db_session, client)

    assert await worker._drain_updates(client, bound_workspace_id=workspace.id) == 1
    assert worker._offsets["__global__"] == 42
    await db_session.refresh(workspace)
    assert workspace.owner_control_chat_id == 999


async def test_cards_and_notifications_render_handoff_kind(db_session, workspace):
    from app.modules.telegram_control_bot.service import _owner_card_text
    from app.modules.telegram_control_bot.worker import _notification_text

    proposal = CommercialActionProposal(
        proposal_id="agent_control:complaint-1",
        workspace_id=workspace.id,
        conversation_id=0,
        customer_id=0,
        action_type="create_business_task",
        lifecycle_state="proposed",
        execution_mode="suggest_only",
        risk_level="low",
        requires_approval=True,
        priority="urgent",
        confidence=0.9,
        reason_code="agent_created_owner_task",
        source_refs=["handoff:complaint"],
        payload={"owner_task": {"title": "Shikoyat: muddati o'tdi", "task_kind": "follow_up"}},
        idempotency_key="complaint-key",
    )
    card = _owner_card_text(action_kind="create_business_task", proposal=proposal)
    assert "Shikoyat" in card  # red header for complaints
    assert card.splitlines()[0].startswith("<b>\U0001f534")  # bold red header

    text = _notification_text(
        {"title": "Yangi lid: Jasur", "summary": "...", "recommended_action": "...",
         "source_refs": ["handoff:lead"]}
    )
    assert text.splitlines()[0].startswith("<b>\U0001f7e0")  # bold orange lead header


async def test_notification_text_renders_customer_and_chat_summary():
    from app.modules.telegram_control_bot.worker import _notification_text

    text = _notification_text(
        {
            "title": "Yangi lid: Alisher",
            "summary": "Raqam: +998912345678",
            "recommended_action": "Qo'ng'iroq qiling",
            "source_refs": ["handoff:lead"],
            "customer_label": "Alisher Valiev (+998912345678)",
            "chat_summary": "Mijoz kursga qiziqdi, narxni so'radi, raqam qoldirdi.",
        }
    )
    lines = text.splitlines()
    assert lines[0].startswith("<b>\U0001f7e0")
    assert any(line.startswith("\U0001f464 <b>Alisher Valiev") for line in lines)
    assert any("Suhbat:" in line and "raqam qoldirdi" in line for line in lines)
    assert any("Keyingi qadam:</b> Qo'ng'iroq qiling" in line for line in lines)


async def test_handoff_card_is_one_skimmable_message(db_session, workspace):
    """The card absorbs the notification: header, customer, chat summary,
    detail, next step — bold + emoji structured for 2-second skimming."""
    from app.modules.telegram_control_bot.service import _owner_card_text

    proposal = CommercialActionProposal(
        proposal_id="owner_task:lead-merge-1",
        workspace_id=workspace.id,
        conversation_id=3,
        customer_id=4,
        action_type="create_business_task",
        lifecycle_state="proposed",
        execution_mode="suggest_only",
        risk_level="low",
        requires_approval=True,
        priority="high",
        confidence=1.0,
        reason_code="agent_created_owner_task",
        source_refs=["handoff:lead"],
        payload={
            "owner_task": {
                "title": "Mijoz qaytib keldi: Jasur <kutib qoldi>",
                "detail": "Hech kim qo'ng'iroq qilmagan, mijoz kutmoqda.",
                "task_kind": "call",
                "context": {
                    "customer_label": "Jasur (+998901635207)",
                    "chat_summary": "Kursga qiziqdi, raqam qoldirdi, kutib qoldi.",
                    "recommended_action": "Mijozga qo'ng'iroq qilib, keyingi qadamni kelishib oling.",
                },
            }
        },
        idempotency_key="lead-merge-key",
    )
    card = _owner_card_text(action_kind="create_business_task", proposal=proposal)
    lines = card.splitlines()
    assert lines[0].startswith("<b>\U0001f7e0")  # bold kind header
    assert "&lt;kutib qoldi&gt;" in card  # user content is HTML-escaped
    assert any(line.startswith("\U0001f464 <b>Jasur") for line in lines)
    assert any("\U0001f4dd" in line and "raqam qoldirdi" in line for line in lines)
    assert any("Keyingi qadam:</b>" in line for line in lines)


async def test_flush_skips_merged_notifications(db_session, workspace):
    """Merged notifications stay as audit rows — never sent as messages."""
    workspace.owner_control_chat_id = 555
    await db_session.flush()
    await _queue_notification(db_session, workspace, ref="owner_notification:merged:1")
    repo = CommercialSpineRepository(db_session)
    merged = await repo.get_projection(
        workspace_id=workspace.id, projection_ref="owner_notification:merged:1"
    )
    await repo.upsert_projection(
        merged.model_copy(update={"state": {**merged.state, "status": "merged_into_card"}})
    )
    await db_session.flush()

    client = _FakeBotClient()
    sent = await _worker(db_session, client)._flush_notifications({workspace.id: client})
    assert sent == 0
    assert client.sent == []


async def test_handoff_card_links_telegram_profile_and_copyable_phone(db_session, workspace):
    from app.modules.telegram_control_bot.service import _owner_card_text

    proposal = CommercialActionProposal(
        proposal_id="owner_task:link-1",
        workspace_id=workspace.id,
        conversation_id=3,
        customer_id=4,
        action_type="create_business_task",
        lifecycle_state="proposed",
        execution_mode="suggest_only",
        risk_level="low",
        requires_approval=True,
        priority="high",
        confidence=1.0,
        reason_code="agent_created_owner_task",
        source_refs=["handoff:lead"],
        payload={
            "owner_task": {
                "title": "Yangi lid: Jasur",
                "detail": "Mijoz kutmoqda.",
                "task_kind": "call",
                "context": {
                    "customer_label": "SATStation",
                    "customer_phone": "+998901635207",
                    "telegram_link": "tg://user?id=6598527321",
                    "recommended_action": "Qo'ng'iroq qiling.",
                },
            }
        },
        idempotency_key="link-key",
    )
    card = _owner_card_text(action_kind="create_business_task", proposal=proposal)
    assert '<a href="tg://user?id=6598527321">SATStation</a>' in card
    assert "<code>+998901635207</code>" in card


async def _seed_missing_field(db_session, workspace, *, product_ref, fields):
    from app.models.commerce_catalog import (
        CatalogMissingFieldRecord,
        CatalogProductRecord,
    )

    db_session.add(
        CatalogProductRecord(
            workspace_id=workspace.id,
            product_ref=product_ref,
            name="Biznesni tizimlashtirish kursi",
            authority_state="approved",
        )
    )
    for field in fields:
        db_session.add(
            CatalogMissingFieldRecord(
                workspace_id=workspace.id,
                product_ref=product_ref,
                field=field,
                authority_state="candidate",
            )
        )
    await db_session.flush()


async def test_flushes_missing_field_card_once_per_product(db_session, workspace):
    workspace.owner_control_chat_id = 777
    await _seed_missing_field(
        db_session,
        workspace,
        product_ref="product:test-course",
        fields=["price", "exact_venue"],
    )
    client = _FakeBotClient()
    worker = _worker(db_session, client)

    sent = await worker._flush_missing_field_cards({workspace.id: client})
    assert sent == 1
    text = client.sent[0]["text"]
    assert "Biznesni tizimlashtirish kursi" in text
    assert "narx" in text  # human label, not the raw field name

    # Second tick: deduped by product + field-set projection.
    sent_again = await worker._flush_missing_field_cards({workspace.id: client})
    assert sent_again == 0
    assert len(client.sent) == 1


async def test_missing_field_card_dedup_is_workspace_scoped(
    db_session, workspace, workspace_b
):
    """Same product_ref + field-set in two workspaces -> each owner gets a
    card; one workspace's sent projection must not suppress the other's."""
    workspace.owner_control_chat_id = 777
    workspace_b.owner_control_chat_id = 888
    for ws in (workspace, workspace_b):
        await _seed_missing_field(
            db_session,
            ws,
            product_ref="product:test-course",
            fields=["price", "exact_venue"],
        )
    client_a = _FakeBotClient()
    client_b = _FakeBotClient()
    worker = _worker(db_session, client_a)
    clients = {workspace.id: client_a, workspace_b.id: client_b}

    assert await worker._flush_missing_field_cards(clients) == 2
    assert len(client_a.sent) == 1
    assert len(client_b.sent) == 1
    assert client_a.sent[0]["chat_id"] == 777
    assert client_b.sent[0]["chat_id"] == 888

    # Second tick: both deduped independently.
    assert await worker._flush_missing_field_cards(clients) == 0
    assert len(client_a.sent) == 1
    assert len(client_b.sent) == 1


async def test_missing_field_flush_skips_workspaces_without_bot(db_session, workspace):
    """Rows from workspaces with no bot client never enter the flush query."""
    await _seed_missing_field(
        db_session, workspace, product_ref="product:test-course", fields=["price"]
    )
    worker = _worker(db_session, _FakeBotClient())

    assert await worker._flush_missing_field_cards({}) == 0


async def test_handoff_card_uses_honest_action_labels(db_session, workspace):
    from app.modules.telegram_control_bot.service import TelegramControlBotService

    proposal = CommercialActionProposal(
        proposal_id="owner_task:handoff-label-test",
        workspace_id=workspace.id,
        conversation_id=5,
        customer_id=1,
        action_type="create_business_task",
        lifecycle_state="proposed",
        execution_mode="suggest_only",
        risk_level="low",
        requires_approval=True,
        executor_runtime="owner_task",
        priority="high",
        confidence=1.0,
        reason_code="agent_created_owner_task",
        source_refs=["handoff:lead"],
        payload={"owner_task": {"title": "Yangi lid", "detail": "d", "reason": "d"}},
        idempotency_key="test:handoff-label",
        correlation_id="test",
    )
    service = TelegramControlBotService(session=db_session, client=_FakeBotClient())
    card = await service.approval_card(proposal)
    buttons = card.reply_markup["inline_keyboard"][0]
    assert buttons[0]["text"] == "✅ Men shug'ullanaman"
    assert buttons[1]["text"] == "❌ Keraksiz"


async def _campaign_with_targets(db_session, workspace, *, status="running"):
    from app.models.crm_connection import CrmConnection
    from app.models.outreach import OutreachCampaign, OutreachTarget

    conn = CrmConnection(workspace_id=workspace.id, provider="amocrm", status="active",
                         provider_account_ref="mybiz", webhook_token=f"wh-cmd-{status}",
                         pipeline_config={})
    db_session.add(conn)
    await db_session.flush()
    campaign = OutreachCampaign(workspace_id=workspace.id, connection_id=conn.id,
                                name="Iyun intake", goal="reactivate", segment_spec={},
                                base_message="Salom", caps={}, status=status)
    db_session.add(campaign)
    await db_session.flush()
    db_session.add(OutreachTarget(
        campaign_id=campaign.id, workspace_id=workspace.id, provider_contact_id="5",
        phone="+998901112233", display_name="Ali", tier="warm", state="sent",
        idempotency_key=f"cmd-{status}-1"))
    await db_session.flush()
    return campaign


def _text_update(chat_id: int, text: str) -> dict:
    return {"message": {"chat": {"id": chat_id}, "text": text}}


async def test_campaign_status_lists_counts(db_session, workspace):
    workspace.owner_control_chat_id = 9001
    await db_session.flush()
    await _campaign_with_targets(db_session, workspace)
    client = _FakeBotClient()
    service = TelegramControlBotService(
        session=db_session, client=client, bound_workspace_id=workspace.id
    )

    result = await service.handle_owner_message(_text_update(9001, "/campaign"))

    assert result["ok"] is True and result["action"] == "campaign_status"
    assert "Iyun intake" in client.sent[-1]["text"]
    assert "1 yuborildi" in client.sent[-1]["text"]


async def test_campaign_pause_and_resume_roundtrip(db_session, workspace):
    workspace.owner_control_chat_id = 9002
    await db_session.flush()
    campaign = await _campaign_with_targets(db_session, workspace)
    client = _FakeBotClient()
    service = TelegramControlBotService(
        session=db_session, client=client, bound_workspace_id=workspace.id
    )

    paused = await service.handle_owner_message(_text_update(9002, "/campaign pause"))
    assert paused["ok"] is True
    await db_session.refresh(campaign)
    assert campaign.status == "paused"

    resumed = await service.handle_owner_message(_text_update(9002, "/campaign resume"))
    assert resumed["ok"] is True
    await db_session.refresh(campaign)
    assert campaign.status == "running"


async def test_campaign_command_from_unbound_chat_is_refused(db_session, workspace):
    client = _FakeBotClient()
    service = TelegramControlBotService(
        session=db_session, client=client, bound_workspace_id=workspace.id
    )
    result = await service.handle_owner_message(_text_update(424242, "/campaign pause"))
    assert result["ok"] is False
    assert result["reason"] == "workspace_not_bound"


async def test_campaign_resume_on_running_is_not_found(db_session, workspace):
    workspace.owner_control_chat_id = 9003
    await db_session.flush()
    await _campaign_with_targets(db_session, workspace, status="running")
    client = _FakeBotClient()
    service = TelegramControlBotService(
        session=db_session, client=client, bound_workspace_id=workspace.id
    )

    result = await service.handle_owner_message(_text_update(9003, "/campaign resume"))

    assert result["ok"] is False and result["reason"] == "campaign_not_found"


async def test_drain_lanes_carry_bound_workspace_id(db_session, workspace):
    """A dedicated-token lane carries its workspace_id; the global lane carries None."""
    import app.modules.telegram_control_bot.worker as worker_mod

    workspace.control_bot_token = "111:DEDICATED"
    await db_session.flush()

    def factory():
        @asynccontextmanager
        async def _cm():
            yield db_session

        return _cm()

    made: dict[str, _FakeBotClient] = {}

    def client_factory(token):
        made[token] = _FakeBotClient()
        return made[token]

    worker = worker_mod.OwnerControlBotWorker(
        db_factory=factory, client=_FakeBotClient(), client_factory=client_factory
    )
    clients = await worker._workspace_clients()
    lanes = worker._drain_lanes(clients)

    dedicated = [lane for lane in lanes if lane[1] == workspace.id]
    assert len(dedicated) == 1
    assert dedicated[0][2] is made["111:DEDICATED"]
    global_lanes = [lane for lane in lanes if lane[0] == worker_mod._GLOBAL_KEY]
    assert len(global_lanes) == 1 and global_lanes[0][1] is None
