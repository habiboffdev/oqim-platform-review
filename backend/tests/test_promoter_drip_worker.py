"""PromoterDripWorker — warm drip: send, skip, pace, back off. Real DB; the
delivery service, CRM provider, and personalizer are injected fakes."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection
from app.models.customer import Customer
from app.models.message import Message
from app.models.outreach import OutreachCampaign, OutreachTarget
from app.modules.bi_promoter.drip_worker import PromoterDripWorker
from app.services.delivery import DeliveryResult

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 6, 15, 7, 0, tzinfo=UTC)  # 12:00 Tashkent — inside [9, 19)


class _FakeDelivery:
    def __init__(self, results=None):
        self.calls = []
        self._results = list(results or [])

    async def deliver_message(self, conversation_id, text, *, db, workspace_id=None,
                              client_idempotency_key=None, message_id=None, **kw):
        self.calls.append({
            "conversation_id": conversation_id, "text": text,
            "idempotency_key": client_idempotency_key, "message_id": message_id,
        })
        if self._results:
            return self._results.pop(0)
        return DeliveryResult(success=True, external_message_id="ext-1", state="confirmed")


class _FakeProvider:
    def __init__(self, note="HR kursi bilan qiziqqan"):
        self.note = note
        self.notes_added = []

    async def fetch_last_contact_note(self, conn, *, contact_id):
        return self.note

    async def add_contact_note(self, conn, *, contact_id, text):
        self.notes_added.append((contact_id, text))


async def _fake_personalize(*, workspace_id, base_message, contact_name, crm_context):
    return f"Salom {contact_name}! {base_message} [{crm_context}]"


def _worker(delivery, provider, *, now=_NOW):
    rng = random.Random()
    rng.uniform = lambda lo, hi: float(lo)  # deterministic: jitter gate = lower bound
    return PromoterDripWorker(
        db_factory=None,
        delivery=delivery,
        provider_factory=lambda name: provider,
        personalize=_fake_personalize,
        rng=rng,
        now_fn=lambda: now,
    )


async def _conn(db_session, workspace, token="wh-drip"):
    c = CrmConnection(workspace_id=workspace.id, provider="amocrm", status="active",
                      provider_account_ref="mybiz", webhook_token=token, pipeline_config={})
    db_session.add(c)
    await db_session.flush()
    return c


async def _campaign(db_session, workspace, conn, *, status="running", caps=None):
    c = OutreachCampaign(workspace_id=workspace.id, connection_id=conn.id, name="Iyun",
                         goal="reactivate", segment_spec={}, base_message="Yangi intake",
                         caps=caps or {}, status=status)
    db_session.add(c)
    await db_session.flush()
    return c


async def _warm_person(db_session, workspace, phone, *, chat_id=777, last_message_at=None,
                       opted_out=False):
    cust = Customer(workspace_id=workspace.id, display_name="Ali", phone_number=phone,
                    opted_out=opted_out)
    db_session.add(cust)
    await db_session.flush()
    conv = Conversation(workspace_id=workspace.id, customer_id=cust.id,
                        channel="telegram_dm", pipeline_stage="new",
                        telegram_chat_id=chat_id,
                        last_message_at=last_message_at or (_NOW - timedelta(days=30)))
    db_session.add(conv)
    await db_session.flush()
    return cust, conv


async def _target(db_session, campaign, phone, *, tier="warm", state="pending", **over):
    t = OutreachTarget(campaign_id=campaign.id, workspace_id=campaign.workspace_id,
                       provider_contact_id="5", phone=phone, display_name="Ali",
                       tier=tier, state=state,
                       idempotency_key=over.pop("idempotency_key", f"idem-{campaign.id}-{phone}"),
                       next_attempt_at=over.pop("next_attempt_at", _NOW - timedelta(minutes=5)),
                       **over)
    db_session.add(t)
    await db_session.flush()
    return t


async def test_warm_send_happy_path(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    cust, conv = await _warm_person(db_session, workspace, "+998901112233")
    target = await _target(db_session, campaign, "+998901112233")
    delivery, provider = _FakeDelivery(), _FakeProvider()

    processed = await _worker(delivery, provider).run_once(db_session)

    assert processed == 1
    await db_session.refresh(target)
    assert target.state == "sent"
    assert target.customer_id == cust.id
    assert target.conversation_id == conv.id
    assert target.sent_at is not None
    assert delivery.calls[0]["idempotency_key"] == target.idempotency_key
    msg = (await db_session.execute(
        select(Message).where(Message.client_message_uuid == target.idempotency_key)
    )).scalar_one()
    assert msg.sender_type == "seller"
    assert "Salom Ali" in msg.content and "HR kursi" in msg.content  # personalized + CRM context
    assert provider.notes_added and provider.notes_added[0][0] == "5"  # contact note write-back


async def test_opted_out_customer_is_skipped(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _warm_person(db_session, workspace, "+998901112233", opted_out=True)
    target = await _target(db_session, campaign, "+998901112233")
    delivery = _FakeDelivery()

    await _worker(delivery, _FakeProvider()).run_once(db_session)

    await db_session.refresh(target)
    assert target.state == "skipped"
    assert target.last_error == "opted_out"
    assert delivery.calls == []


async def test_active_conversation_is_skipped(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _warm_person(db_session, workspace, "+998901112233",
                       last_message_at=_NOW - timedelta(hours=1))
    target = await _target(db_session, campaign, "+998901112233")
    delivery = _FakeDelivery()

    await _worker(delivery, _FakeProvider()).run_once(db_session)

    await db_session.refresh(target)
    assert target.state == "skipped"
    assert target.last_error == "active_conversation"
    assert delivery.calls == []


async def test_unsendable_warm_target_demoted_to_cold(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    target = await _target(db_session, campaign, "+998900000000")  # no customer exists
    delivery = _FakeDelivery()

    await _worker(delivery, _FakeProvider()).run_once(db_session)

    await db_session.refresh(target)
    assert target.tier == "cold"        # left for Slice C resolution
    assert target.state == "pending"
    assert delivery.calls == []


async def test_paused_campaign_is_not_drained(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn, status="paused")
    await _warm_person(db_session, workspace, "+998901112233")
    target = await _target(db_session, campaign, "+998901112233")
    delivery = _FakeDelivery()

    processed = await _worker(delivery, _FakeProvider()).run_once(db_session)

    assert processed == 0
    await db_session.refresh(target)
    assert target.state == "pending"
    assert delivery.calls == []


async def test_retry_after_crash_reuses_placeholder_no_duplicate_bubble(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _warm_person(db_session, workspace, "+998901112233")
    target = await _target(db_session, campaign, "+998901112233")
    provider = _FakeProvider()

    failing = _FakeDelivery(results=[DeliveryResult(success=False, error="boom", state="unknown")])
    await _worker(failing, provider).run_once(db_session)
    await db_session.refresh(target)
    assert target.state == "sending"  # claimed; backoff scheduled

    later = _NOW + timedelta(hours=1)
    ok = _FakeDelivery()
    await _worker(ok, provider, now=later).run_once(db_session)

    await db_session.refresh(target)
    assert target.state == "sent"
    msgs = (await db_session.execute(
        select(Message).where(Message.client_message_uuid == target.idempotency_key)
    )).scalars().all()
    assert len(msgs) == 1  # the placeholder was reused — one bubble, ever
    assert ok.calls[0]["text"] == msgs[0].content


async def test_flood_wait_throttles_then_sends_on_retry(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _warm_person(db_session, workspace, "+998901112233")
    target = await _target(db_session, campaign, "+998901112233")

    throttled = _FakeDelivery(results=[
        DeliveryResult(success=False, error="rate_limited", state="failed",
                       retry_after_seconds=30.0)])
    await _worker(throttled, _FakeProvider()).run_once(db_session)
    await db_session.refresh(target)
    assert target.state == "sending"          # claimed + throttled, never re-enrolled
    assert target.attempts == 0               # FloodWait is not a failure attempt
    assert target.last_error.startswith("rate_limited")

    later = _NOW + timedelta(hours=1)
    ok = _FakeDelivery()
    await _worker(ok, _FakeProvider(), now=later).run_once(db_session)
    await db_session.refresh(target)
    assert target.state == "sent"             # retried successfully, NOT skipped
    assert len(ok.calls) == 1


async def test_other_workspace_dialog_is_not_a_warm_destination(
    db_session, workspace, workspace_b
):
    # A customer/conversation with the SAME phone exists only in workspace_b.
    # The workspace target must not borrow it — with no in-workspace dialog it
    # demotes to cold (Slice C resolves cold).
    await _warm_person(db_session, workspace_b, "+998901112233")
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    target = await _target(db_session, campaign, "+998901112233")
    delivery = _FakeDelivery()

    await _worker(delivery, _FakeProvider()).run_once(db_session)

    await db_session.refresh(target)
    assert target.tier == "cold"
    assert target.state == "pending"
    assert delivery.calls == []


async def test_outside_working_hours_holds_everything(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _warm_person(db_session, workspace, "+998901112233")
    target = await _target(db_session, campaign, "+998901112233")
    delivery = _FakeDelivery()
    night = datetime(2026, 6, 14, 21, 0, tzinfo=UTC)  # 02:00 Tashkent

    processed = await _worker(delivery, _FakeProvider(), now=night).run_once(db_session)

    assert processed == 0
    await db_session.refresh(target)
    assert target.state == "pending"
    assert delivery.calls == []


async def test_jitter_gate_paces_consecutive_sends(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _warm_person(db_session, workspace, "+998901112233", chat_id=701)
    await _warm_person(db_session, workspace, "+998905556677", chat_id=702)
    await _target(db_session, campaign, "+998901112233", state="sent",
                  sent_at=_NOW - timedelta(seconds=60), idempotency_key="idem-sent-1")
    fresh = await _target(db_session, campaign, "+998905556677")
    delivery = _FakeDelivery()

    # 60s since last send < jitter lo (180s, deterministic rng) -> gate closed
    await _worker(delivery, _FakeProvider()).run_once(db_session)
    await db_session.refresh(fresh)
    assert fresh.state == "pending" and delivery.calls == []

    # 200s since last send >= 180s -> gate open
    later = _NOW + timedelta(seconds=140)
    await _worker(delivery, _FakeProvider(), now=later).run_once(db_session)
    await db_session.refresh(fresh)
    assert fresh.state == "sent"


async def test_rate_limited_send_backs_off_without_consuming_attempt(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _warm_person(db_session, workspace, "+998901112233")
    target = await _target(db_session, campaign, "+998901112233")
    delivery = _FakeDelivery(results=[
        DeliveryResult(success=False, error="rate_limited retry_after=30.00s",
                       state="failed", retry_after_seconds=30.0)
    ])

    await _worker(delivery, _FakeProvider()).run_once(db_session)

    await db_session.refresh(target)
    assert target.state == "sending"          # claimed + throttled, NOT re-enrolled
    assert target.attempts == 0               # FloodWait never consumes an attempt
    assert target.last_error.startswith("rate_limited")
    assert target.next_attempt_at == _NOW + timedelta(seconds=30 + 180)  # retry_after + jitter_lo


async def test_failure_ladder_ends_in_failed_state(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _warm_person(db_session, workspace, "+998901112233")
    target = await _target(db_session, campaign, "+998901112233", attempts=5)
    delivery = _FakeDelivery(results=[
        DeliveryResult(success=False, error="boom", state="failed")
    ])

    await _worker(delivery, _FakeProvider()).run_once(db_session)

    await db_session.refresh(target)
    assert target.attempts == 6
    assert target.state == "failed"
    assert "delivery_failed" in target.last_error


async def test_first_failure_backs_off_exponentially(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _warm_person(db_session, workspace, "+998901112233")
    target = await _target(db_session, campaign, "+998901112233")
    delivery = _FakeDelivery(results=[
        DeliveryResult(success=False, error="boom", state="failed")
    ])

    await _worker(delivery, _FakeProvider()).run_once(db_session)

    await db_session.refresh(target)
    assert target.attempts == 1
    assert target.state == "sending"  # reclaimable
    assert target.next_attempt_at == _NOW + timedelta(seconds=120)


async def test_daily_digest_queued_exactly_once_per_day(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _target(db_session, campaign, "+998901112233", state="sent",
                  sent_at=_NOW - timedelta(hours=2))
    await _target(db_session, campaign, "+998905556677", state="replied")
    worker = _worker(_FakeDelivery(), _FakeProvider())

    await worker.run_once(db_session)
    await worker.run_once(db_session)  # same day -> deduped by idempotency key

    rows = (await db_session.execute(
        select(BusinessBrainProjectionRecord).where(
            BusinessBrainProjectionRecord.projection_type == "owner_notification")
    )).scalars().all()
    assert len(rows) == 1
    payload = rows[0].state["bot_payload"]
    assert "Iyun" in payload["summary"]
    assert "1 yuborildi" in payload["summary"]
    assert "1 javob" in payload["summary"]


async def test_fully_drained_campaign_completes(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _target(db_session, campaign, "+998901112233", state="sent",
                  sent_at=_NOW - timedelta(hours=2))
    await _target(db_session, campaign, "+998905556677", state="replied")

    await _worker(_FakeDelivery(), _FakeProvider()).run_once(db_session)

    await db_session.refresh(campaign)
    assert campaign.status == "completed"


async def test_campaign_with_cold_backlog_stays_running(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    await _target(db_session, campaign, "+998901112233", state="sent",
                  sent_at=_NOW - timedelta(hours=2))
    await _target(db_session, campaign, "+998905556677", tier="cold")  # pending for Slice C

    await _worker(_FakeDelivery(), _FakeProvider()).run_once(db_session)

    await db_session.refresh(campaign)
    assert campaign.status == "running"


async def test_reclaimed_sending_target_with_no_dialog_resets_to_pending_cold(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    # a target previously claimed 'sending' but no warm dialog exists for its phone
    target = await _target(db_session, campaign, "+998900000000", state="sending")
    delivery = _FakeDelivery()

    await _worker(delivery, _FakeProvider()).run_once(db_session)

    await db_session.refresh(target)
    assert target.tier == "cold"
    assert target.state == "pending"   # not wedged as cold+sending
    assert delivery.calls == []
