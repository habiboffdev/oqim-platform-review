"""CrmSyncWorker — the supervised reconciler. ALL CRM HTTP lives here.

Level-triggered desired-state reconciliation: the deterministic hooks only ever
write *desired* state into ``crm_lead_links``; this worker drains it to the CRM.
It is safe to run repeatedly against a moving external system because every
externally-visible step is idempotent and commits the resulting id BEFORE the
next call (crash-window ordering: a created contact is never re-created).

Invariants enforced here:
- monotonic, FORWARD-ONLY stage advance (never moves a lead backwards);
- a permanent human-touch latch: if a human moved the lead in the CRM since our
  last write, OQIM stops pushing stages for that lead forever (read-before-write);
- phone dedup (reuse an existing contact) + later phone enrichment;
- bounded retries with exponential backoff, then ``degraded`` + an idempotent
  owner card — failures surface, never silently and never on the reply path;
- a 401 triggers a single row-locked token refresh + one retry.

The ``CrmProvider`` is reached through ``provider_factory`` so tests inject a
fake and no HTTP leaves them. ``run_once(session)`` is the public seam tests and
the supervisor loop both drive.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.models.customer import Customer
from app.modules.crm_connector.contracts import (
    CrmContactInput,
    CrmLeadInput,
    CrmUnauthorizedError,
    role_index,
)
from app.modules.crm_connector.factory import provider_for
from app.modules.crm_connector.owner_cards import queue_crm_owner_notification
from app.modules.crm_connector.provider import CrmProvider
from app.modules.crm_connector.stage_map import pipeline_id_for_stage, resolve_pipeline_view
from app.modules.crm_connector.token_refresh import refresh_connection_locked

logger = get_logger("crm.sync_worker")

_TICK_INTERVAL_SECONDS = 15.0
_SCAN_LIMIT = 25
_MIN_INTERVAL_SECONDS = 0.15  # ~7 req/s/account amoCRM ceiling -> serialize per connection
_MAX_ATTEMPTS = 8
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600
_NONRETRYABLE_STATUSES = {400, 422}


def _is_validation_error(exc: Exception) -> bool:
    """A deterministic provider validation rejection (bad field value/shape).
    Retrying re-sends the identical payload, so fail fast instead of storming."""
    resp = getattr(exc, "response", None)
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and resp is not None
        and getattr(resp, "status_code", None) in _NONRETRYABLE_STATUSES
    )
_WEBHOOK_EVENTS: tuple[str, ...] = (
    "status_lead", "responsible_lead", "note_lead", "update_lead", "update_contact",
)


def _dnc_on(card_value: object, on_value: object) -> bool:
    """Normalize do-not-contact equality: amoCRM returns a checkbox as a JSON bool,
    while the config on_value may be bool/str/int. Both are coerced to bool so a
    string config ("true") still matches a boolean card value. A missing card value
    (None) is never a match."""
    def norm(v: object) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on")
        if isinstance(v, (int, float)):
            return bool(v)
        return False
    return card_value is not None and norm(card_value) == norm(on_value)


def _parse_due_at(due_at: str | None) -> datetime:
    """ISO-8601 due_at -> datetime (the adapter converts to its own deadline
    encoding). Falls back to 24h from now if missing/unparseable so a task is
    never created without a deadline."""
    if due_at:
        try:
            return datetime.fromisoformat(due_at)
        except (ValueError, TypeError):
            pass
    return datetime.now(UTC) + timedelta(hours=24)


class CrmSyncWorker:
    def __init__(
        self,
        *,
        db_factory: Callable[[], AsyncSession] | None,
        provider_factory: Callable[[str], CrmProvider] | None = None,
        interval_seconds: float = _TICK_INTERVAL_SECONDS,
        min_interval_seconds: float = _MIN_INTERVAL_SECONDS,
    ) -> None:
        self._db_factory = db_factory
        self._provider_factory = provider_factory or provider_for
        self._interval = interval_seconds
        self._min_interval = min_interval_seconds
        self._last_call: dict[int, float] = {}
        self._stopping = False
        self._beat: Callable[[], None] = lambda: None

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._beat = callback or (lambda: None)

    async def start(self) -> None:
        assert self._db_factory is not None, "db_factory required to run the loop"
        self._stopping = False
        while not self._stopping:
            try:
                async with self._db_factory() as session:
                    processed = await self.run_once(session)
                self._beat()
                if processed:
                    logger.info("crm_sync_worker.reconciled", extra={"count": processed})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("crm_sync_worker.tick_failed", exc_info=exc)
            slept = 0.0
            while not self._stopping and slept < self._interval:
                await asyncio.sleep(min(5.0, self._interval - slept))
                slept += 5.0
                self._beat()

    async def stop(self) -> None:
        self._stopping = True

    # ------------------------------------------------------------------ #
    async def ensure_webhooks_registered(self, session: AsyncSession) -> int:
        """Register the inbound webhook per active connection (idempotent). Also
        RE-registers when the stored event set differs from ``_WEBHOOK_EVENTS`` —
        so a connection registered with an older set (e.g. before S4b added
        update_lead/update_contact) picks up the new events without a reconnect.
        State lives in pipeline_config['webhook'] (no migration); a registration
        failure (e.g. plan tier) is skipped this pass, never blocking links."""
        public_base = get_settings().amocrm_redirect_uri.rsplit("/api/", 1)[0]
        conns = (
            await session.execute(
                select(CrmConnection).where(CrmConnection.status == "active")
            )
        ).scalars().all()
        registered = 0
        for conn in conns:
            config = dict(conn.pipeline_config or {})
            hook = config.get("webhook")
            # Skip only when already registered AND the stored event set matches the
            # desired set; a stale/different set falls through to (re-)registration.
            if hook and list(hook.get("events") or []) == list(_WEBHOOK_EVENTS):
                continue
            provider = self._provider_factory(conn.provider)
            destination = f"{public_base}/api/webhook/amocrm/{conn.webhook_token}"
            try:
                await self._throttle(conn.id)
                hook_id = await provider.register_webhook(
                    conn, destination=destination, events=list(_WEBHOOK_EVENTS)
                )
            except Exception:
                logger.warning("amocrm webhook registration failed (conn %s)", conn.id, exc_info=True)
                continue
            if not hook_id:
                # A 200 with no webhook id: do NOT store an empty-id marker (it would
                # read as "registered" forever and poison the idempotency gate). Retry
                # next tick.
                logger.warning("amocrm webhook registration returned no id (conn %s); will retry", conn.id)
                continue
            config["webhook"] = {"id": hook_id, "events": list(_WEBHOOK_EVENTS)}
            conn.pipeline_config = config  # reassign so SQLAlchemy marks JSONB dirty
            await session.commit()
            registered += 1
        return registered

    # ------------------------------------------------------------------ #
    async def run_once(self, session: AsyncSession) -> int:
        """Scan due pending links on active connections and reconcile each."""
        now = datetime.now(UTC)
        await self.ensure_webhooks_registered(session)
        rows = (
            await session.execute(
                select(CrmLeadLink, CrmConnection)
                .join(CrmConnection, CrmLeadLink.connection_id == CrmConnection.id)
                .where(
                    CrmLeadLink.sync_state == "pending",
                    CrmLeadLink.next_attempt_at <= now,
                    CrmConnection.status == "active",
                )
                .order_by(CrmLeadLink.next_attempt_at.asc())
                .limit(_SCAN_LIMIT)
            )
        ).all()

        processed = 0
        for link, conn in rows:
            self._beat()
            provider = self._provider_factory(conn.provider)
            customer = await session.get(Customer, link.customer_id)
            conversation = await session.get(Conversation, link.conversation_id)
            try:
                await self._reconcile_link(session, link, conn, customer, conversation, provider)
            except CrmUnauthorizedError:
                # One row-locked refresh (single-use token safe) + one retry.
                try:
                    await refresh_connection_locked(
                        session, connection_id=conn.id, provider=provider
                    )
                    await self._reconcile_link(
                        session, link, conn, customer, conversation, provider
                    )
                except Exception as exc:
                    await self._handle_failure(session, link, exc)
            except Exception as exc:
                await self._handle_failure(session, link, exc)
            processed += 1
        return processed

    # ------------------------------------------------------------------ #
    async def _reconcile_link(  # noqa: C901 (one ordered idempotent-phase sequence; the crash-window ordering invariant must stay in one body)
        self,
        session: AsyncSession,
        link: CrmLeadLink,
        conn: CrmConnection,
        customer: Any,
        conversation: Any,
        provider: CrmProvider,
    ) -> None:
        # Resolve the LEAD's pipeline view (flat<->nested shim): a lead pinned to a
        # non-default pipeline uses that pipeline's role_map + id. A legacy flat
        # config resolves identically to the old read, so the pilot is unchanged.
        view = resolve_pipeline_view(conn.pipeline_config, link.pipeline_id)
        stage_map: dict = view["stage_map"]
        pipeline_id = view["pipeline_id"]
        phone = ((getattr(customer, "phone_number", None) or "").strip()) or None
        name = (getattr(customer, "display_name", None) or "").strip() or (
            phone or f"Lead {link.conversation_id}"
        )
        channel = getattr(conversation, "channel", "") if conversation is not None else ""
        channel_label = f"{channel}:{name}" if channel else name

        # --- Phase A: contact (dedup by phone) -------------------------- #
        if link.provider_contact_id is None:
            contact_id: str | None = None
            if phone:
                await self._throttle(conn.id)
                contact_id = await provider.find_contact_by_phone(conn, phone)
            if contact_id is None:
                await self._throttle(conn.id)
                contact_id = await provider.create_contact(
                    conn, CrmContactInput(name=name, phone=phone, channel_label=channel_label)
                )
            link.provider_contact_id = contact_id
            if phone:  # the contact already carries this phone (found or created with it)
                link.synced_phone = phone
            await session.commit()  # crash-window: a created contact must survive a crash

        # --- Phase B: lead at the "new" stage --------------------------- #
        if link.provider_lead_id is None:
            new_stage = stage_map.get("new") or {}
            new_stage_id = str(new_stage.get("stage_id") or "")
            await self._throttle(conn.id)
            lead_id = await provider.create_lead(
                conn,
                CrmLeadInput(
                    name=name,
                    pipeline_id=pipeline_id,
                    stage_id=new_stage_id,
                    contact_id=str(link.provider_contact_id),
                ),
            )
            link.provider_lead_id = lead_id
            link.synced_stage_role = "new"
            link.last_synced_stage_id = new_stage_id
            await session.commit()

        # --- Phase B2: pipeline re-home (route-once move, advance-independent) - #
        # A lead pinned (by S3 routing) to a pipeline DIFFERENT from the one it was
        # last synced into is PATCHed to the new pipeline at its CURRENT role's stage
        # — independent of any forward stage advance (Phase C only fires on an
        # advance, so the move would otherwise never reach amoCRM). The human-touch
        # latch wins; the move carries pipeline_id+status_id in one PATCH.
        synced_pipeline = pipeline_id_for_stage(conn.pipeline_config, link.last_synced_stage_id)
        if (
            link.provider_lead_id is not None
            and link.stage_authority == "oqim"
            and synced_pipeline is not None
            and str(link.pipeline_id or "") != str(synced_pipeline)
        ):
            await self._throttle(conn.id)
            snapshot = await provider.fetch_lead(conn, str(link.provider_lead_id))
            if (
                link.last_synced_stage_id is not None
                and snapshot.stage_id != link.last_synced_stage_id
            ):
                # A human moved the lead since our last write — latch permanently.
                link.stage_authority = "human"
                link.last_observed_stage_id = snapshot.stage_id
                await session.commit()
            else:
                role = link.synced_stage_role or "new"
                await self._throttle(conn.id)
                pushed = await provider.update_lead_stage(
                    conn, str(link.provider_lead_id), role=role, config=view
                )
                if pushed:
                    link.last_synced_stage_id = pushed
                    await session.commit()

        # --- Phase C: forward-only stage advance + human-touch latch ---- #
        # Ordering invariant for the whole reconcile: every provider call PRECEDES
        # any uncommitted link mutation, so a failed link never holds partial
        # uncommitted state and the failure path needs no rollback.
        synced_role = link.synced_stage_role or "new"
        if (
            link.stage_authority == "oqim"
            and role_index(link.desired_stage_role) > role_index(synced_role)
        ):
            await self._throttle(conn.id)
            snapshot = await provider.fetch_lead(conn, str(link.provider_lead_id))
            if (
                link.last_synced_stage_id is not None
                and snapshot.stage_id != link.last_synced_stage_id
            ):
                # A human moved the lead since our last write — latch permanently.
                link.stage_authority = "human"
                link.last_observed_stage_id = snapshot.stage_id
                await session.commit()
            else:
                target = stage_map.get(link.desired_stage_role) or {}
                current = stage_map.get(synced_role) or {}
                if int(target.get("sort", 0)) > int(current.get("sort", 0)):
                    await self._throttle(conn.id)
                    pushed = await provider.update_lead_stage(
                        conn,
                        str(link.provider_lead_id),
                        role=link.desired_stage_role,
                        config=view,
                    )
                    # Record OUR own stage write durably before anything else, so a
                    # later fetch_lead can't mistake it for a human touch.
                    link.last_synced_stage_id = pushed or str(target.get("stage_id") or "")
                    link.synced_stage_role = link.desired_stage_role
                    await session.commit()
                else:
                    # Clamped target at/below the current stage: nothing to push,
                    # the desired role is already reconciled.
                    link.synced_stage_role = link.desired_stage_role
                    await session.commit()

        # --- Phase C2: deal value (push when known & changed, ANY stage) --- #
        if link.provider_lead_id is not None:
            value = getattr(conversation, "deal_value", None) if conversation else None
            if value is not None and value != link.synced_value:
                await self._throttle(conn.id)
                await provider.set_lead_value(
                    conn, str(link.provider_lead_id), amount=value, currency="UZS"
                )
                link.synced_value = value
                await session.commit()

        # --- Phase D: phone enrichment (phone appeared after creation) --- #
        if phone and link.synced_phone is None and link.provider_contact_id is not None:
            await self._throttle(conn.id)
            await provider.update_contact_phone(conn, str(link.provider_contact_id), phone)
            link.synced_phone = phone
            await session.commit()

        # --- Phase E: drain pending notes in order ---------------------- #
        if link.pending_notes and link.provider_lead_id is not None:
            for note in list(link.pending_notes):
                await self._throttle(conn.id)
                await provider.add_note(
                    conn, str(link.provider_lead_id), str((note or {}).get("text", ""))
                )
            link.pending_notes = []
            await session.commit()

        # --- Phase E2: drain pending tasks (+ needs-human tag) ----------- #
        # create_task has NO idempotency key on amoCRM, so the task's removal from
        # pending_tasks must commit IMMEDIATELY after creation — a later tag-write
        # failure must never recreate the task on the retry tick. The additive
        # `oqim:operator-kerak` tag is applied AFTER all tasks are durably recorded,
        # best-effort + idempotent (amoCRM dedupes tags_to_add by name), so a tag
        # failure cannot re-enter task creation.
        if link.pending_tasks and link.provider_lead_id is not None:
            tasks_drained = False
            remaining = list(link.pending_tasks)
            for task in list(remaining):
                await self._throttle(conn.id)
                await provider.create_followup_task(
                    conn, str(link.provider_lead_id),
                    text=str((task or {}).get("text", "")),
                    due_at=_parse_due_at((task or {}).get("due_at")),
                )
                # Record this task durably BEFORE the next call so a retry can't
                # recreate it (commit-per-task also minimizes the crash-window).
                remaining = [t for t in remaining if t.get("key") != task.get("key")]
                link.pending_tasks = remaining
                await session.commit()
                tasks_drained = True
            if tasks_drained:
                # Best-effort: a tag-write failure must NOT fail the reconcile (which
                # would re-enter task creation on retry). amoCRM dedupes tags_to_add
                # by name, so a later manual retry stays idempotent.
                try:
                    await self._throttle(conn.id)
                    await provider.add_tags(
                        conn, str(link.provider_lead_id), ["oqim:operator-kerak"]
                    )
                except Exception as tag_exc:
                    logger.warning(
                        "crm_sync_worker.needs_human_tag_failed link=%s error=%s",
                        link.id,
                        type(tag_exc).__name__,
                    )

        # --- Phase E3: drain S4 field/tag ops (entity-routed, latch-gated) -- #
        # DNC ops (kind="dnc") are compliance-sensitive: written even when latched.
        # Other custom_field/tag ops are latch-gated (never overwrite a human's edits
        # — cleared without writing when latched). dnc_recheck markers are NOT drained
        # here; Phase E4 consumes them. custom_field/dnc ops route by entity
        # ("lead"->provider_lead_id, "contact"->provider_contact_id) (S4b).
        ops = list(link.pending_field_ops or [])
        drainable = [o for o in ops if o.get("kind") in ("custom_field", "dnc", "tag")]
        if drainable and link.provider_lead_id is not None:
            latched = link.stage_authority != "oqim"
            by_entity: dict[str, dict] = {}
            tag_names: list[str] = []
            for o in drainable:
                if o.get("kind") == "dnc" or (o.get("kind") == "custom_field" and not latched):
                    ent = o.get("entity") or "lead"
                    by_entity.setdefault(ent, {})[o["field_id"]] = {
                        "value": o["value"],
                        "type": o.get("type"),
                    }
                elif o.get("kind") == "tag" and not latched:
                    tag_names.append(o["name"])
            for ent, fields in by_entity.items():
                entity_id = link.provider_contact_id if ent == "contact" else link.provider_lead_id
                if entity_id and fields:
                    await self._throttle(conn.id)
                    await provider.set_custom_fields(
                        conn, str(entity_id), fields,
                        entity=("contacts" if ent == "contact" else "leads"),
                    )
            if tag_names:
                await self._throttle(conn.id)
                await provider.add_tags(conn, str(link.provider_lead_id), tag_names)
            link.pending_field_ops = [o for o in ops if o.get("kind") == "dnc_recheck"]
            await session.commit()

        # --- Phase E4: DNC inbound (marker-gated; reads the mapped DNC field) - #
        # Runs ONLY when a card edit re-armed via a dnc_recheck marker (bounds the
        # fetch to actual edits). READS the human's DNC value and opts out — never
        # writes the card, so it is exempt from the latch back-off. The DNC field
        # lives on the configured entity (default "contact"). apply_promoter_turn_facts
        # re-arms once to drain the opt-out note; the next reconcile no-ops via the
        # not-opted-out guard, so the loop terminates.
        markers = [o for o in (link.pending_field_ops or []) if o.get("kind") == "dnc_recheck"]
        if markers and customer is not None and not getattr(customer, "opted_out", False):
            from app.models.agent import Agent
            from app.models.agent_session import AgentSession
            from app.modules.agent_runtime_v2.config_loader import resolve_crm_dnc

            agent_id = await session.scalar(
                select(AgentSession.agent_id)
                .where(AgentSession.conversation_id == link.conversation_id)
                .limit(1)
            )
            agent = await session.get(Agent, agent_id) if agent_id else None
            dnc = resolve_crm_dnc(getattr(agent, "channel_config", None) or {}) if agent else None
            if dnc:
                ent = dnc.get("entity") or "contact"
                values: dict = {}
                if ent == "contact" and link.provider_contact_id is not None:
                    await self._throttle(conn.id)
                    values = await provider.fetch_contact_custom_fields(
                        conn, str(link.provider_contact_id)
                    )
                elif ent == "lead" and link.provider_lead_id is not None:
                    await self._throttle(conn.id)
                    values = (await provider.fetch_lead(conn, str(link.provider_lead_id))).custom_fields
                if _dnc_on(values.get(str(dnc["field_id"])), dnc.get("on_value", True)):
                    from types import SimpleNamespace

                    from app.modules.bi_promoter.fact_hooks import apply_promoter_turn_facts

                    await apply_promoter_turn_facts(
                        session,
                        SimpleNamespace(state={"facts": {"opted_out": True}}),
                        workspace_id=link.workspace_id,
                        conversation_id=link.conversation_id,
                        customer_id=link.customer_id,
                    )
            link.pending_field_ops = [
                o for o in (link.pending_field_ops or []) if o.get("kind") != "dnc_recheck"
            ]
            await session.commit()

        # --- Phase F: settle -------------------------------------------- #
        link.sync_state = "synced"
        link.attempts = 0
        await session.commit()

    # ------------------------------------------------------------------ #
    async def _handle_failure(
        self, session: AsyncSession, link: CrmLeadLink, exc: Exception
    ) -> None:
        """Backoff a failed link; degrade + surface an owner card at the ceiling.

        No rollback: the reconcile's ordering invariant (every provider call
        precedes any uncommitted link mutation) guarantees a failed link carries
        no partial uncommitted state — committed crash-window ids from earlier
        phases persist by design.
        """
        nonretryable = _is_validation_error(exc)
        if nonretryable:
            link.attempts = _MAX_ATTEMPTS  # jump to the ceiling -> degraded below
            link.next_attempt_at = datetime.now(UTC)
        else:
            link.attempts = (link.attempts or 0) + 1
            delay = min(
                _BACKOFF_BASE_SECONDS * (2 ** (link.attempts - 1)), _BACKOFF_CAP_SECONDS
            )
            link.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
        logger.warning(
            "crm_sync_worker.link_failed link=%s attempts=%s nonretryable=%s error=%s",
            link.id,
            link.attempts,
            nonretryable,
            type(exc).__name__,
        )
        if link.attempts >= _MAX_ATTEMPTS:
            link.sync_state = "degraded"
            try:
                await queue_crm_owner_notification(
                    session,
                    workspace_id=link.workspace_id,
                    title="amoCRM sinxronizatsiya xatosi",
                    summary="Lead amoCRM ga yuborilmadi — ulanishni tekshiring.",
                    recommended_action="Integratsiyalar > amoCRM bo'limida ulanishni qayta tekshiring.",
                    idempotency_key=(
                        f"crm_sync_degraded:{link.workspace_id}:"
                        f"{datetime.now(UTC).strftime('%Y%m%d')}"
                    ),
                    conversation_id=link.conversation_id,
                )
            except Exception as card_exc:  # a card failure must not abort the loop
                logger.error(
                    "crm_sync_worker.owner_card_failed link=%s error=%s",
                    link.id,
                    type(card_exc).__name__,
                )
        await session.commit()

    # ------------------------------------------------------------------ #
    async def _throttle(self, connection_id: int) -> None:
        """Serialize calls to one CRM account with a min inter-call interval."""
        if self._min_interval <= 0:
            return
        last = self._last_call.get(connection_id)
        now = time.monotonic()
        if last is not None:
            wait = self._min_interval - (now - last)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_call[connection_id] = time.monotonic()
