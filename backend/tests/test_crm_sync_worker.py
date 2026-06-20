"""CrmSyncWorker — the reconciler that drains desired CRM state to amoCRM.

Real DB; the CrmProvider is mocked at its seam (a fake injected via
``provider_factory``) so no HTTP ever leaves the test. These cover the worker's
hard invariants: idempotent convergence, phone dedup, forward-only/monotonic
stage advance, the permanent human-touch latch, deal-value push, phone
enrichment, backoff + degraded + idempotent owner card, the 401 refresh+retry
path, and crash-window ordering (a committed contact id is never re-created).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select

from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.models.customer import Customer
from app.modules.crm_connector.contracts import CrmLeadSnapshot, CrmUnauthorizedError
from app.modules.crm_connector.sync_worker import _MAX_ATTEMPTS, CrmSyncWorker

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Fake provider — records every call; scriptable returns + failures.
# --------------------------------------------------------------------------- #
class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.contact_id = "C100"
        self.lead_id = "L200"
        self.found_contact_id: str | None = None  # find_contact_by_phone result
        self.fetch_stage_id = ""  # fetch_lead's reported status id
        self.fetch_custom_fields: dict = {}  # fetch_lead's reported custom field values
        self.always_fail: dict[str, Exception] = {}
        self.fail_once: dict[str, Exception] = {}

    def _raise(self, method: str) -> None:
        if method in self.always_fail:
            raise self.always_fail[method]
        if method in self.fail_once:
            raise self.fail_once.pop(method)

    def count(self, method: str) -> int:
        return sum(1 for c in self.calls if c[0] == method)

    async def find_contact_by_phone(self, conn, phone):
        self.calls.append(("find_contact_by_phone", phone))
        self._raise("find_contact_by_phone")
        return self.found_contact_id

    async def create_contact(self, conn, contact):
        self.calls.append(("create_contact", contact))
        self._raise("create_contact")
        return self.contact_id

    async def update_contact_phone(self, conn, contact_id, phone):
        self.calls.append(("update_contact_phone", contact_id, phone))
        self._raise("update_contact_phone")

    async def create_lead(self, conn, lead):
        self.calls.append(("create_lead", lead))
        self._raise("create_lead")
        return self.lead_id

    async def update_lead_stage(self, conn, lead_id, *, role, config):
        self.calls.append(("update_lead_stage", lead_id, role))
        self._raise("update_lead_stage")

    async def set_lead_value(self, conn, lead_id, *, amount, currency="UZS"):
        self.calls.append(("set_lead_value", lead_id, int(amount)))
        self._raise("set_lead_value")

    async def add_note(self, conn, lead_id, text):
        self.calls.append(("add_note", lead_id, text))
        self._raise("add_note")

    async def create_followup_task(self, conn, lead_id, *, text, due_at,
                                   owner_ref=None):
        self.calls.append(("create_followup_task", lead_id, text, due_at))
        self._raise("create_followup_task")
        return "T900"

    async def add_tags(self, conn, lead_id, names):
        self.calls.append(("add_tags", lead_id, tuple(names)))
        self._raise("add_tags")

    async def set_custom_fields(self, conn, entity_id, fields, *, entity="leads"):
        self.calls.append(("set_custom_fields", entity_id, dict(fields), entity))
        self._raise("set_custom_fields")
        return "ok"

    async def fetch_contact_custom_fields(self, conn, contact_id):
        self.calls.append(("fetch_contact_custom_fields", contact_id))
        self._raise("fetch_contact_custom_fields")
        return dict(getattr(self, "fetch_contact_fields", {}))

    async def register_webhook(self, conn, *, destination, events):
        self.calls.append(("register_webhook", destination, tuple(events)))
        self._raise("register_webhook")
        return getattr(self, "webhook_id", "WH1")

    async def fetch_lead(self, conn, lead_id):
        self.calls.append(("fetch_lead", lead_id))
        self._raise("fetch_lead")
        return CrmLeadSnapshot(
            lead_id=str(lead_id), stage_id=self.fetch_stage_id, value=None, notes=[],
            custom_fields=dict(self.fetch_custom_fields),
        )


def _worker(fake: _FakeProvider) -> CrmSyncWorker:
    # min_interval 0: no real rate-limit sleeps in tests.
    return CrmSyncWorker(
        db_factory=None,
        provider_factory=lambda _provider: fake,
        min_interval_seconds=0.0,
    )


def _pipeline_config(*, clamped: bool = False) -> dict:
    if clamped:  # a <3-stage pipeline: negotiation/qualified clamp onto "new".
        stage_map = {
            "new": {"stage_id": "1001", "sort": 10},
            "negotiation": {"stage_id": "1001", "sort": 10},
            "qualified": {"stage_id": "1001", "sort": 10},
        }
    else:
        stage_map = {
            "new": {"stage_id": "1001", "sort": 10},
            "negotiation": {"stage_id": "1002", "sort": 20},
            "qualified": {"stage_id": "1003", "sort": 30},
            "won": {"stage_id": "142", "sort": 1000},
            "lost": {"stage_id": "143", "sort": 2000},
        }
    return {"pipeline_id": "777", "stage_map": stage_map, "pipeline_snapshot": []}


def _nested_pipeline_config() -> dict:
    return {
        "schema_version": 2,
        "snapshot": {"pipelines": [
            {"id": "111", "name": "A", "statuses": [
                {"stage_id": "201", "name": "Yangi", "sort": 10, "kind": "active"}]},
            {"id": "222", "name": "B", "statuses": [
                {"stage_id": "301", "name": "Boshlash", "sort": 10, "kind": "active"},
                {"stage_id": "303", "name": "Yakun", "sort": 30, "kind": "active"}]},
        ]},
        "mapping": {
            "default_pipeline_id": "111",
            "pipelines": {
                "111": {"name": "A", "role_map": {"new": {"stage_id": "201", "sort": 10}}},
                "222": {"name": "B", "role_map": {
                    "new": {"stage_id": "301", "sort": 10},
                    "qualified": {"stage_id": "303", "sort": 30}}},
            },
        },
    }


async def test_lead_pinned_to_non_default_pipeline_uses_that_pipelines_stages(
    db_session, workspace, customer
):
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace, pipeline_config=_nested_pipeline_config())
    await _link(
        db_session, workspace, conn, customer, conv,
        pipeline_id="222",                 # pinned to the NON-default pipeline
        desired_stage_role="new", pending_notes=[],
    )
    fake = _FakeProvider()
    await _worker(fake).run_once(db_session)
    created = next(c for c in fake.calls if c[0] == "create_lead")
    lead = created[1]
    assert lead.pipeline_id == "222"       # the lead's pinned pipeline, not the default 111
    assert lead.stage_id == "301"          # pipeline 222's "new" stage, resolved via the shim


# --------------------------------------------------------------------------- #
# DB builders (mirror test_crm_sync_service.py helpers).
# --------------------------------------------------------------------------- #
async def _conn(db_session, workspace, *, pipeline_config=None, status="active"):
    conn = CrmConnection(
        workspace_id=workspace.id,
        provider="amocrm",
        status=status,
        provider_account_ref="mybiz",
        access_token="tok-access",
        refresh_token="tok-refresh",
        token_expires_at=datetime.now(UTC) + timedelta(days=1),
        webhook_token="wh-1",
        pipeline_config=pipeline_config if pipeline_config is not None else _pipeline_config(),
    )
    db_session.add(conn)
    await db_session.flush()
    return conn


async def _customer(db_session, workspace, *, phone=None):
    cust = Customer(
        workspace_id=workspace.id,
        display_name="Ali",
        contact_type="customer",
        phone_number=phone,
    )
    db_session.add(cust)
    await db_session.flush()
    return cust


async def _conversation(db_session, workspace, customer, *, channel="telegram_dm", deal_value=None):
    conv = Conversation(
        workspace_id=workspace.id,
        customer_id=customer.id,
        channel=channel,
        pipeline_stage="new",
        deal_value=deal_value,
    )
    db_session.add(conv)
    await db_session.flush()
    return conv


async def _link(db_session, workspace, conn, customer, conversation, **over):
    over.setdefault("next_attempt_at", datetime.now(UTC) - timedelta(minutes=1))
    over.setdefault("pending_notes", [{"key": "k1", "text": "Lead captured on first contact (OQIM)"}])
    link = CrmLeadLink(
        workspace_id=workspace.id,
        connection_id=conn.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        **over,
    )
    db_session.add(link)
    await db_session.flush()
    return link


async def _owner_cards(db_session, workspace_id):
    return (
        await db_session.execute(
            select(func.count())
            .select_from(BusinessBrainProjectionRecord)
            .where(
                BusinessBrainProjectionRecord.workspace_id == workspace_id,
                BusinessBrainProjectionRecord.projection_type == "owner_notification",
            )
        )
    ).scalar_one()


# --------------------------------------------------------------------------- #
# 1. Convergence
# --------------------------------------------------------------------------- #
async def test_convergence_creates_contact_then_lead_and_drains_notes(
    db_session, workspace
):
    customer = await _customer(db_session, workspace)  # no phone -> no dedup probe
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(db_session, workspace, conn, customer, conv)

    fake = _FakeProvider()
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)

    methods = [c[0] for c in fake.calls]
    assert "find_contact_by_phone" not in methods  # no phone -> no dedup probe
    assert methods.index("create_contact") < methods.index("create_lead")
    assert ("add_note", "L200", "Lead captured on first contact (OQIM)") in fake.calls

    assert link.provider_contact_id == "C100"
    assert link.provider_lead_id == "L200"
    assert link.synced_stage_role == "new"
    assert link.last_synced_stage_id == "1001"
    assert link.pending_notes == []
    assert link.sync_state == "synced"


async def test_context_note_drains_to_amocrm(db_session, workspace, customer):
    """#428 e2e: a rich lead-context note in pending_notes drains via add_note to
    the (mocked) amoCRM lead with its composed text."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    note_text = "OQIM (qualified):\nMahsulot: HR kursi\nNarx: 4 900 000 so'm"
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        synced_stage_role="qualified", last_synced_stage_id="1003",
        desired_stage_role="qualified",
        pending_notes=[{"key": f"{conv.id}:ctx:qualified", "text": note_text}],
    )

    fake = _FakeProvider()
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)

    assert ("add_note", "L200", note_text) in fake.calls
    assert link.pending_notes == []


# --------------------------------------------------------------------------- #
# 2. Phone dedup
# --------------------------------------------------------------------------- #
async def test_phone_dedup_reuses_existing_contact(db_session, workspace):
    customer = await _customer(db_session, workspace, phone="+998901112233")
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(db_session, workspace, conn, customer, conv)

    fake = _FakeProvider()
    fake.found_contact_id = "C-EXISTING"
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)

    assert fake.count("find_contact_by_phone") == 1
    assert fake.count("create_contact") == 0
    assert link.provider_contact_id == "C-EXISTING"
    assert link.synced_phone == "+998901112233"


# --------------------------------------------------------------------------- #
# 3. Stage advance forward-only
# --------------------------------------------------------------------------- #
async def test_stage_advance_patches_forward(db_session, workspace, customer):
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        synced_stage_role="new", last_synced_stage_id="1001",
        desired_stage_role="qualified", pending_notes=[],
    )

    fake = _FakeProvider()
    fake.fetch_stage_id = "1001"  # untouched since last sync
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)

    assert ("update_lead_stage", "L200", "qualified") in fake.calls
    assert link.synced_stage_role == "qualified"
    assert link.last_synced_stage_id == "1003"
    assert link.sync_state == "synced"


async def test_stage_advance_no_patch_when_target_not_higher(db_session, workspace, customer):
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace, pipeline_config=_pipeline_config(clamped=True))
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        synced_stage_role="new", last_synced_stage_id="1001",
        desired_stage_role="qualified", pending_notes=[],
    )

    fake = _FakeProvider()
    fake.fetch_stage_id = "1001"
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)

    assert fake.count("update_lead_stage") == 0
    assert link.synced_stage_role == "qualified"  # settled, nothing to push
    assert link.sync_state == "synced"


# --------------------------------------------------------------------------- #
# 4. Human-touch latch (permanent)
# --------------------------------------------------------------------------- #
async def test_human_touch_latches_and_stops_stage_pushes(db_session, workspace, customer):
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        synced_stage_role="new", last_synced_stage_id="1001",
        desired_stage_role="qualified",
        pending_notes=[{"key": "k1", "text": "Advanced to qualified (OQIM)"}],
    )

    fake = _FakeProvider()
    fake.fetch_stage_id = "9999"  # a human moved the lead in amoCRM
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)

    assert fake.count("update_lead_stage") == 0
    assert link.stage_authority == "human"
    assert link.last_observed_stage_id == "9999"
    assert ("add_note", "L200", "Advanced to qualified (OQIM)") in fake.calls  # notes still flow

    # Re-arm with a desired advance: the latch is permanent — no more stage I/O.
    link.sync_state = "pending"
    link.next_attempt_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)

    assert fake.count("fetch_lead") == 1  # not consulted again
    assert fake.count("update_lead_stage") == 0


# --------------------------------------------------------------------------- #
# 5. deal_value push on advance
# --------------------------------------------------------------------------- #
async def test_deal_value_pushed_on_advance(db_session, workspace, customer):
    conv = await _conversation(db_session, workspace, customer, deal_value=4900000)
    conn = await _conn(db_session, workspace)
    await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        synced_stage_role="new", last_synced_stage_id="1001",
        desired_stage_role="qualified", pending_notes=[],
    )

    fake = _FakeProvider()
    fake.fetch_stage_id = "1001"
    await _worker(fake).run_once(db_session)

    assert ("set_lead_value", "L200", 4900000) in fake.calls


# --------------------------------------------------------------------------- #
# 6. Phone enrichment (phone appears after lead creation)
# --------------------------------------------------------------------------- #
async def test_phone_enrichment_runs_once(db_session, workspace):
    customer = await _customer(db_session, workspace, phone="+998905556677")
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        synced_stage_role="new", last_synced_stage_id="1001",
        desired_stage_role="new", synced_phone=None, pending_notes=[],
    )

    fake = _FakeProvider()
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)

    assert ("update_contact_phone", "C100", "+998905556677") in fake.calls
    assert link.synced_phone == "+998905556677"

    # Re-arm: phone already synced -> not pushed again.
    link.sync_state = "pending"
    link.next_attempt_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()
    await _worker(fake).run_once(db_session)

    assert fake.count("update_contact_phone") == 1


# --------------------------------------------------------------------------- #
# 7. Backoff + degraded + owner card
# --------------------------------------------------------------------------- #
async def test_backoff_increments_and_gates_rescan(db_session, workspace, customer):
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(db_session, workspace, conn, customer, conv)

    fake = _FakeProvider()
    fake.always_fail["create_contact"] = RuntimeError("amocrm down")
    worker = _worker(fake)

    await worker.run_once(db_session)
    await db_session.refresh(link)
    assert link.attempts == 1
    assert link.next_attempt_at > datetime.now(UTC)
    assert link.provider_contact_id is None  # untouched otherwise
    assert link.sync_state == "pending"

    # Immediate re-run: next_attempt_at is in the future -> not rescanned.
    await worker.run_once(db_session)
    await db_session.refresh(link)
    assert link.attempts == 1


async def test_degraded_after_max_attempts_queues_one_owner_card(
    db_session, workspace, customer
):
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(
        db_session, workspace, conn, customer, conv,
        attempts=7, next_attempt_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    fake = _FakeProvider()
    fake.always_fail["create_contact"] = RuntimeError("amocrm down")
    worker = _worker(fake)

    await worker.run_once(db_session)
    await db_session.refresh(link)
    assert link.attempts == 8
    assert link.sync_state == "degraded"
    assert await _owner_cards(db_session, workspace.id) == 1

    # Degraded links are not rescanned; a later tick adds no second card.
    await worker.run_once(db_session)
    assert await _owner_cards(db_session, workspace.id) == 1


# --------------------------------------------------------------------------- #
# 8. 401 -> refresh + retry once
# --------------------------------------------------------------------------- #
async def test_unauthorized_refreshes_then_retries(
    db_session, workspace, customer, monkeypatch
):
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(db_session, workspace, conn, customer, conv)

    refresh_mock = AsyncMock()
    monkeypatch.setattr(
        "app.modules.crm_connector.sync_worker.refresh_connection_locked", refresh_mock
    )

    fake = _FakeProvider()
    fake.fail_once["create_contact"] = CrmUnauthorizedError("amocrm api 401")
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)

    assert refresh_mock.await_count == 1
    assert refresh_mock.await_args.kwargs["connection_id"] == conn.id
    assert fake.count("create_contact") == 2  # failed once, retried once
    assert link.provider_contact_id == "C100"
    assert link.sync_state == "synced"


# --------------------------------------------------------------------------- #
# 9. Crash-window ordering — a committed contact id is never re-created.
# --------------------------------------------------------------------------- #
async def test_crash_window_persists_contact_before_lead(db_session, workspace, customer):
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(db_session, workspace, conn, customer, conv)

    fake = _FakeProvider()
    fake.fail_once["create_lead"] = RuntimeError("amocrm 500 mid-create")
    worker = _worker(fake)

    await worker.run_once(db_session)
    await db_session.refresh(link)
    assert link.provider_contact_id == "C100"  # committed before the lead call
    assert link.provider_lead_id is None
    assert link.attempts == 1

    # Retry: the contact id is already persisted -> no second create_contact.
    link.next_attempt_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()
    await worker.run_once(db_session)
    await db_session.refresh(link)

    assert fake.count("create_contact") == 1
    assert link.provider_lead_id == "L200"
    assert link.sync_state == "synced"


# --------------------------------------------------------------------------- #
# Scan scoping: an inactive connection's pending link is never reconciled.
# --------------------------------------------------------------------------- #
async def test_inactive_connection_links_are_not_scanned(db_session, workspace, customer):
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace, status="disconnected")
    await _link(db_session, workspace, conn, customer, conv)

    fake = _FakeProvider()
    processed = await _worker(fake).run_once(db_session)

    assert processed == 0
    assert fake.calls == []


# --------------------------------------------------------------------------- #
# 10. deal_value pushed at any stage (decoupled phase) + handoff task drain.
# --------------------------------------------------------------------------- #
async def test_deal_value_pushed_when_changed_without_stage_advance(
    db_session, workspace, customer
):
    conv = await _conversation(db_session, workspace, customer, deal_value=Decimal("4900000"))
    conn = await _conn(db_session, workspace)
    # already-synced lead, no pending advance -> exercises the decoupled value phase
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        synced_stage_role="qualified", last_synced_stage_id="1003",
        desired_stage_role="qualified", pending_notes=[],
    )
    fake = _FakeProvider()
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)

    assert ("set_lead_value", "L200", 4900000) in fake.calls
    assert link.synced_value == Decimal("4900000")

    # second pass: value unchanged -> not pushed again
    fake2 = _FakeProvider()
    rearm = link
    rearm.sync_state = "pending"
    rearm.next_attempt_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()
    await _worker(fake2).run_once(db_session)
    assert fake2.count("set_lead_value") == 0


async def test_handoff_task_drains_with_tag(db_session, workspace, customer):
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        synced_stage_role="qualified", last_synced_stage_id="1003",
        desired_stage_role="qualified", pending_notes=[],
        pending_tasks=[{"key": f"{conv.id}:task:handoff",
                        "text": "Mijoz bilan bog'laning (OQIM).",
                        "due_at": "2026-06-15T00:00:00+00:00"}],
    )
    fake = _FakeProvider()
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)

    methods = [c[0] for c in fake.calls]
    assert "create_followup_task" in methods
    assert ("add_tags", "L200", ("oqim:operator-kerak",)) in fake.calls
    assert link.pending_tasks == []


async def test_tag_failure_does_not_recreate_task_on_retry(db_session, workspace, customer):
    """A tag-write failure AFTER create_task must NOT recreate the task on retry.

    create_task has no idempotency key on amoCRM, so the task removal commits
    immediately on creation; the additive needs-human tag is best-effort after,
    so a tag failure neither fails the reconcile nor re-enters task creation.
    """
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        synced_stage_role="qualified", last_synced_stage_id="1003",
        desired_stage_role="qualified", pending_notes=[],
        pending_tasks=[{"key": f"{conv.id}:task:handoff",
                        "text": "Mijoz bilan bog'laning (OQIM).",
                        "due_at": "2026-06-15T00:00:00+00:00"}],
    )
    fake = _FakeProvider()
    fake.fail_once["add_tags"] = RuntimeError("amocrm tag write down")
    worker = _worker(fake)

    # First tick: create_task succeeds, the tag-write raises but is swallowed
    # best-effort -> the task is durably recorded and the link still settles.
    await worker.run_once(db_session)
    await db_session.refresh(link)
    assert fake.count("create_followup_task") == 1
    assert ("add_tags", "L200", ("oqim:operator-kerak",)) in fake.calls  # attempted
    assert link.pending_tasks == []  # task creation durably recorded
    assert link.sync_state == "synced"

    # Rearm and re-scan (as a fresh desired-state change would): the task must
    # NOT be recreated, because pending_tasks was committed empty.
    link.sync_state = "pending"
    link.next_attempt_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()
    await worker.run_once(db_session)
    await db_session.refresh(link)

    assert fake.count("create_followup_task") == 1  # EXACTLY once across failure + retry
    assert link.pending_tasks == []
    assert link.sync_state == "synced"


# --------------------------------------------------------------------------- #
# 11. Idempotent webhook registration (Slice B).
# --------------------------------------------------------------------------- #
async def test_ensure_webhooks_registered_is_idempotent(db_session, workspace):
    conn = await _conn(db_session, workspace)  # pipeline_config has no "webhook"
    fake = _FakeProvider()
    await _worker(fake).ensure_webhooks_registered(db_session)
    await db_session.refresh(conn)
    assert conn.pipeline_config.get("webhook", {}).get("id") == "WH1"
    assert fake.count("register_webhook") == 1
    args = next(c for c in fake.calls if c[0] == "register_webhook")
    assert "wh-1" in args[1]  # destination embeds the connection's webhook_token
    assert args[2] == ("status_lead", "responsible_lead", "note_lead", "update_lead", "update_contact")

    # second pass: already registered -> no-op
    fake2 = _FakeProvider()
    await _worker(fake2).ensure_webhooks_registered(db_session)
    assert fake2.count("register_webhook") == 0


async def test_empty_webhook_id_not_stored_and_retried(db_session, workspace):
    """A 200 with no webhook id must NOT poison the idempotency gate; retry next tick."""
    conn = await _conn(db_session, workspace)
    fake = _FakeProvider()
    fake.webhook_id = ""
    await _worker(fake).ensure_webhooks_registered(db_session)
    await db_session.refresh(conn)
    assert "webhook" not in (conn.pipeline_config or {})
    assert fake.count("register_webhook") == 1

    fake2 = _FakeProvider()
    fake2.webhook_id = "WH9"
    await _worker(fake2).ensure_webhooks_registered(db_session)
    await db_session.refresh(conn)
    assert conn.pipeline_config["webhook"]["id"] == "WH9"


async def test_route_once_rehome_patches_pipeline_without_stage_advance(
    db_session, workspace, customer
):
    """S3 review CRITICAL: a routed lead (link.pipeline_id changed to 222 while last
    synced into 111) must be re-homed in amoCRM by the worker EVEN WITHOUT a stage
    advance — Phase C alone would never push it."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace, pipeline_config=_nested_pipeline_config())
    await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        pipeline_id="222",                      # routed target (lead physically still in 111)
        desired_stage_role="new", synced_stage_role="new",   # NO advance
        last_synced_stage_id="201",             # 111's "new" stage -> synced pipeline 111 != 222
        sync_state="pending", attempts=0, pending_notes=[],
    )
    fake = _FakeProvider()
    fake.fetch_stage_id = "201"                 # == last_synced -> not a human touch
    await _worker(fake).run_once(db_session)
    assert ("update_lead_stage", "L200", "new") in fake.calls  # the re-home reached amoCRM


async def test_route_once_rehome_skips_when_human_touched(db_session, workspace, customer):
    """A human moving the lead (fetch reports a different stage than last_synced) latches
    and blocks the re-home."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace, pipeline_config=_nested_pipeline_config())
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        pipeline_id="222", desired_stage_role="new", synced_stage_role="new",
        last_synced_stage_id="201", sync_state="pending", attempts=0, pending_notes=[],
    )
    fake = _FakeProvider()
    fake.fetch_stage_id = "999"                 # != last_synced -> a human touched it
    await _worker(fake).run_once(db_session)
    assert fake.count("update_lead_stage") == 0
    await db_session.refresh(link)
    assert link.stage_authority == "human"


# --------------------------------------------------------------------------- #
# S4 Phase E3: drain pending_field_ops (custom fields + tags), latch-gated.
# --------------------------------------------------------------------------- #
async def test_drain_field_ops_calls_set_custom_fields_and_add_tags(
    db_session, workspace, customer
):
    """An oqim-owned link with queued custom_field + tag ops drains them via
    set_custom_fields/add_tags (resolved ids), then clears pending_field_ops."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        desired_stage_role="new", synced_stage_role="new",
        last_synced_stage_id="1001", stage_authority="oqim",
        sync_state="pending", attempts=0, pending_notes=[],
        pending_field_ops=[
            {"kind": "custom_field", "field_id": "600123", "value": 5000000},
            {"kind": "tag", "name": "oqim:vip"},
        ],
    )
    fake = _FakeProvider()
    await _worker(fake).run_once(db_session)
    assert (
        "set_custom_fields", "L200",
        {"600123": {"value": 5000000, "type": None}}, "leads",
    ) in fake.calls
    assert ("add_tags", "L200", ("oqim:vip",)) in fake.calls
    await db_session.refresh(link)
    assert link.pending_field_ops == []         # cleared on success


async def test_drain_routes_contact_field_to_contact_entity(
    db_session, workspace, customer
):
    """A contact-entity custom_field op is written to the contact (provider_contact_id)
    via set_custom_fields(entity='contacts'), not the lead (S4b)."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        desired_stage_role="new", synced_stage_role="new",
        last_synced_stage_id="1001", stage_authority="oqim",
        sync_state="pending", attempts=0, pending_notes=[],
        pending_field_ops=[
            {"kind": "custom_field", "entity": "contact", "field_id": "740937", "value": "5 mln"},
        ],
    )
    fake = _FakeProvider()
    await _worker(fake).run_once(db_session)
    assert (
        "set_custom_fields", "C100",
        {"740937": {"value": "5 mln", "type": None}}, "contacts",
    ) in fake.calls


async def test_drain_field_ops_skipped_and_cleared_when_human_latched(
    db_session, workspace, customer
):
    """A human owns the lead (stage_authority='human') — OQIM must NOT overwrite their
    field edits; the queued ops are cleared without writing (OQIM backs off)."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        desired_stage_role="new", synced_stage_role="new",
        last_synced_stage_id="1001", stage_authority="human",
        sync_state="pending", attempts=0, pending_notes=[],
        pending_field_ops=[
            {"kind": "custom_field", "field_id": "600123", "value": 5000000},
        ],
    )
    fake = _FakeProvider()
    await _worker(fake).run_once(db_session)
    assert fake.count("set_custom_fields") == 0  # never overwrite a human
    await db_session.refresh(link)
    assert link.pending_field_ops == []          # cleared (OQIM backed off)


# --------------------------------------------------------------------------- #
# S4 Phase E4: DNC inbound — read the mapped do-not-contact field -> opt-out.
# --------------------------------------------------------------------------- #
async def _agent_with_dnc(db_session, workspace, conversation, customer, *, on_value=True):
    """An agent whose channel_config maps a do-not-contact field, plus the
    per-conversation AgentSession the worker walks (conversation -> session -> agent)."""
    from app.models.agent import Agent
    from app.modules.agent_sessions.service import AgentSessionService

    ag = Agent(
        workspace_id=workspace.id,
        name="Sotuvchi",
        trust_mode="disabled",
        auto_send_threshold=0.85,
        channel_config={"crm": {"do_not_contact": {"field_id": "600126", "on_value": on_value}}},
    )
    db_session.add(ag)
    await db_session.flush()
    await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=ag.id,
        channel=conversation.channel,
    )
    return ag


async def test_dnc_inbound_opts_out_customer(db_session, workspace, customer):
    """The worker's DNC inbound phase reads the mapped do-not-contact field from the
    lead snapshot; when it equals on_value and the customer isn't already opted out,
    it flips Customer.opted_out (the inbound partner of the outbound DNC write)."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        desired_stage_role="new", synced_stage_role="new",
        last_synced_stage_id="1001", stage_authority="human",  # a human edited the card
        sync_state="pending", attempts=0, pending_notes=[],
        pending_field_ops=[{"kind": "dnc_recheck", "entity": "contact"}],  # S4b: contact edit re-armed
    )
    await _agent_with_dnc(db_session, workspace, conv, customer)
    assert customer.opted_out is False
    fake = _FakeProvider()
    fake.fetch_contact_fields = {"600126": True}  # the human toggled do-not-contact on the contact
    fake.fetch_stage_id = "1001"  # echoes the synced stage so Phase C doesn't advance
    await _worker(fake).run_once(db_session)
    await db_session.refresh(customer)
    assert customer.opted_out is True


async def test_dnc_inbound_noop_when_field_off(db_session, workspace, customer):
    """The DNC field present but NOT equal to on_value -> the customer stays opted in."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        desired_stage_role="new", synced_stage_role="new",
        last_synced_stage_id="1001", stage_authority="human",
        sync_state="pending", attempts=0, pending_notes=[],
        pending_field_ops=[{"kind": "dnc_recheck", "entity": "contact"}],
    )
    await _agent_with_dnc(db_session, workspace, conv, customer)
    fake = _FakeProvider()
    fake.fetch_contact_fields = {"600126": False}  # field present but OFF
    fake.fetch_stage_id = "1001"
    await _worker(fake).run_once(db_session)
    await db_session.refresh(customer)
    assert customer.opted_out is False


async def test_dnc_inbound_noop_when_no_dnc_config(db_session, workspace, customer):
    """No do-not-contact mapping on the agent -> no fetch consult for DNC, no opt-out."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        desired_stage_role="new", synced_stage_role="new",
        last_synced_stage_id="1001", stage_authority="human",
        sync_state="pending", attempts=0, pending_notes=[],
    )
    fake = _FakeProvider()
    fake.fetch_custom_fields = {"600126": True}
    fake.fetch_stage_id = "1001"
    await _worker(fake).run_once(db_session)
    await db_session.refresh(customer)
    assert customer.opted_out is False


async def test_drain_select_field_passes_type_to_adapter(
    db_session, workspace, customer
):
    """A select custom_field op carries type='select' to set_custom_fields so the
    adapter can use the enum_id slot (the real AmoCrmProvider encodes it)."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        desired_stage_role="new", synced_stage_role="new",
        last_synced_stage_id="1001", stage_authority="oqim",
        sync_state="pending", attempts=0, pending_notes=[],
        pending_field_ops=[
            {"kind": "custom_field", "entity": "contact",
             "field_id": "740941", "value": 1308885, "type": "select"},
        ],
    )
    fake = _FakeProvider()
    await _worker(fake).run_once(db_session)
    assert (
        "set_custom_fields", "C100",
        {"740941": {"value": 1308885, "type": "select"}}, "contacts",
    ) in fake.calls


async def test_validation_400_degrades_immediately(db_session, workspace, customer):
    """A deterministic 400 (amoCRM validation) must fail fast to degraded, not
    retry 8 times re-sending the same bad payload."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        desired_stage_role="new", synced_stage_role="new",
        last_synced_stage_id="1001", stage_authority="oqim",
        sync_state="pending", attempts=0, pending_notes=[],
        pending_field_ops=[
            {"kind": "custom_field", "entity": "contact",
             "field_id": "740941", "value": 1308885, "type": "select"},
        ],
    )
    fake = _FakeProvider()
    req = httpx.Request("PATCH", "https://x/api/v4/contacts/C100")
    fake.always_fail["set_custom_fields"] = httpx.HTTPStatusError(
        "400 Bad Request", request=req, response=httpx.Response(400, request=req)
    )
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)
    assert link.sync_state == "degraded"     # fail-fast
    assert link.attempts >= _MAX_ATTEMPTS     # jumped to ceiling, not 1


async def test_transient_500_increments_attempts_not_degraded(db_session, workspace, customer):
    """A 500 is transient — keep the existing backoff (attempts increments), not degraded."""
    conv = await _conversation(db_session, workspace, customer)
    conn = await _conn(db_session, workspace)
    link = await _link(
        db_session, workspace, conn, customer, conv,
        provider_contact_id="C100", provider_lead_id="L200",
        desired_stage_role="new", synced_stage_role="new",
        last_synced_stage_id="1001", stage_authority="oqim",
        sync_state="pending", attempts=0, pending_notes=[],
        pending_field_ops=[
            {"kind": "custom_field", "entity": "contact",
             "field_id": "740941", "value": 1308885, "type": "select"},
        ],
    )
    fake = _FakeProvider()
    req = httpx.Request("PATCH", "https://x/api/v4/contacts/C100")
    fake.always_fail["set_custom_fields"] = httpx.HTTPStatusError(
        "500", request=req, response=httpx.Response(500, request=req)
    )
    await _worker(fake).run_once(db_session)
    await db_session.refresh(link)
    assert link.attempts == 1
    assert link.sync_state != "degraded"
