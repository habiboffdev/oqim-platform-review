"""CrmSyncService — DB-only desired-state hooks.

NO I/O. The deterministic callers (the inbound persist consumer on first contact;
the post-commit records pass on committed turn facts) call these inside
transactions OQIM already commits. They write *desired* CRM state into
``crm_lead_links``; the supervised ``CrmSyncWorker`` reconciles desired -> actual
against the CRM. One lead per conversation is enforced by the unique constraint +
``ON CONFLICT DO NOTHING`` (not code discipline), so racing burst-turn hooks can't
double-create.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import utc_now
from app.models.agent_conversation_state import AgentConversationStateSnapshot
from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.modules.crm_connector.contracts import (
    crm_role_label,
    role_index,
    target_role_for_facts,
)
from app.modules.crm_connector.lead_context_note import (
    compose_handoff_task_text,
    compose_lead_context_note,
    latest_numeric_price,
)
from app.modules.crm_connector.lead_links import active_lead_link, rearm_lead_link
from app.modules.crm_connector.stage_map import default_pipeline_id, snapshot_pipeline_ids

logger = get_logger("crm.sync_service")

_DM_CHANNELS = ("telegram_dm", "instagram_dm")
_HANDOFF_TASK_SLA_HOURS = 24

# Sentinel so a caller can inject ``state_packet=None`` distinctly from "not
# provided" (which falls back to the latest set_state snapshot).
_UNSET = object()


def _note(conversation_id: int, role: str, text: str) -> dict:
    # Stable key per (conversation, role) — the monotonic ladder hits each role
    # at most once, so this also de-dupes a re-fired hook.
    return {"key": f"{conversation_id}:{role}", "text": text[:500]}


class CrmSyncService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_lead_link(self, *, workspace: Any, conversation: Any, customer: Any) -> None:
        """First-contact hook: create the lead link if a CRM is connected and
        this is a real customer DM. Idempotent."""
        if customer.contact_type != "customer":
            return
        if conversation.channel not in _DM_CHANNELS:
            return
        conn = await self._active_connection(workspace.id)
        if conn is None:
            return
        seed_role = await self._seed_role_for_conversation(conversation.id)
        note = _note(conversation.id, seed_role,
                     f"Birinchi aloqada qo'lga olindi (OQIM, {crm_role_label(seed_role)})")
        stmt = (
            insert(CrmLeadLink)
            .values(
                workspace_id=workspace.id,
                connection_id=conn.id,
                conversation_id=conversation.id,
                customer_id=customer.id,
                pipeline_id=default_pipeline_id(conn.pipeline_config),
                desired_stage_role=seed_role,
                stage_authority="oqim",
                sync_state="pending",
                attempts=0,
                next_attempt_at=utc_now(),
                pending_notes=[note],
            )
            .on_conflict_do_nothing(constraint="uq_crm_lead_links_connection_conversation")
        )
        await self._session.execute(stmt)

    async def on_turn_facts(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
        facts: dict,
        intelligence: list[dict] | None = None,
        state_packet: Any = _UNSET,
    ) -> None:
        """Turn-facts hook: advance the desired stage role (monotonic) AND enrich the
        lead with a rich context note when the stage advances OR a handoff is recorded
        (#428).

        The monotonic guard gates only the ``desired_stage_role`` *mutation* — never
        the whole method — so a handoff that does not advance the stage (the lead is
        already qualified) still produces a note.

        ``state_packet`` is the set_state-shaped packet (``selected_items`` /
        ``shown_prices`` / ``next_best_action``) the note composers + deal_value read
        read. The records pass injects a packet adapted from its ``conversation.record``
        payload; any other caller (or an explicit omission) falls back to the latest
        ``set_state`` snapshot. The ``_UNSET`` sentinel lets a caller inject ``None``
        distinctly from "not provided"."""
        link = await active_lead_link(
            self._session, workspace_id=workspace_id, conversation_id=conversation_id
        )
        if link is None:
            return
        if state_packet is _UNSET:
            state_packet = await self._latest_state_packet(conversation_id)

        # Deal value: write conversation.deal_value from the latest shown price on
        # ANY turn (spec §5.2 "push at any stage") — independent of stage/handoff, so
        # a price revision on a steady-state lead still reaches amoCRM. The worker
        # pushes it to amoCRM (and the OQIM UI shows it).
        value = latest_numeric_price(state_packet)
        value_changed = False
        if value is not None:
            conversation = await self._session.get(Conversation, conversation_id)
            if conversation is not None and conversation.deal_value != value:
                conversation.deal_value = value
                value_changed = True

        target = target_role_for_facts(facts)
        advanced = role_index(target) > role_index(link.desired_stage_role)
        handoff = bool(facts.get("handoff_recorded"))
        if not advanced and not handoff:
            # No stage advance and no handoff. If only the price moved, re-arm so the
            # worker pushes the new value; otherwise nothing to do (the old no-op path).
            if value_changed:
                rearm_lead_link(link)
                await self._session.flush()
            return

        notes = list(link.pending_notes)
        if advanced:
            link.desired_stage_role = target
            notes.append(_note(conversation_id, target, f"{crm_role_label(target)} bosqichiga o'tdi (OQIM)"))

        note_stage = target if advanced else link.desired_stage_role
        context_text = compose_lead_context_note(
            stage=note_stage,
            state_packet=state_packet,
            intelligence=intelligence[-1] if intelligence else None,
        )
        ctx_key = f"{conversation_id}:ctx:{note_stage}"
        # Dedup the context note for this stage within the undrained batch: the
        # worker posts every pending note (it does not dedup by key), so a re-fired
        # hook at the same stage must not double-post.
        notes = [n for n in notes if (n or {}).get("key") != ctx_key]
        notes.append({"key": ctx_key, "text": context_text[:500]})
        link.pending_notes = notes

        # Task-on-handoff: one open "call this lead" task per conversation (deduped).
        if handoff:
            tasks = list(link.pending_tasks)
            task_key = f"{conversation_id}:task:handoff"
            if not any((t or {}).get("key") == task_key for t in tasks):
                tasks.append({
                    "key": task_key,
                    "text": compose_handoff_task_text(
                        state_packet, intelligence[-1] if intelligence else None
                    ),
                    "due_at": (utc_now() + timedelta(hours=_HANDOFF_TASK_SLA_HOURS)).isoformat(),
                })
                link.pending_tasks = tasks

        # re-arm reconciliation (also recovers a degraded link) — must run on the
        # handoff-only path too so the worker drains the new note.
        rearm_lead_link(link)
        # flush the desired-state write into the open transaction (the caller
        # commits); ensure_lead_link's Core insert is already immediate.
        await self._session.flush()

    async def route_lead(
        self, *, workspace_id: int, conversation_id: int, target_pipeline_id: str
    ) -> str:
        """Route-once: re-home a lead from the DEFAULT pipeline to
        ``target_pipeline_id`` in the desired-state plane (the worker pushes the move
        via the provider's pipeline+status PATCH). Returns a status:
        ``moved`` | ``noop`` (target is default / empty) | ``vanished`` (target not in
        the snapshot) | ``latched`` (human owns it) | ``terminal`` (won/lost) |
        ``routed`` (already off default — route-once spent) | ``no_link``.

        ``desired_stage_role`` is pipeline-agnostic, so changing ``pipeline_id`` +
        re-arming is enough: the worker resolves the role in the TARGET pipeline's
        role_map. Guarded so a refresh never overrides a human or thrashes a lead."""
        conn = await self._active_connection(workspace_id)
        if conn is None:
            return "no_link"
        default_id = str(default_pipeline_id(conn.pipeline_config) or "")
        target = str(target_pipeline_id or "")
        if not target or target == default_id:
            return "noop"
        if target not in snapshot_pipeline_ids(conn.pipeline_config):
            return "vanished"
        link = await active_lead_link(
            self._session, workspace_id=workspace_id, conversation_id=conversation_id
        )
        if link is None:
            return "no_link"
        if link.stage_authority == "human":
            return "latched"
        if link.desired_stage_role in ("won", "lost"):
            return "terminal"
        if str(link.pipeline_id or "") != default_id:
            return "routed"  # route-once: only move while still on the default pipeline
        link.pipeline_id = target
        rearm_lead_link(link)
        await self._session.flush()
        return "moved"

    async def queue_field_ops(
        self, *, workspace_id: int, conversation_id: int, ops: list[dict]
    ) -> str:
        """Append field/tag ops to the link's ``pending_field_ops`` (deduped by
        field_id/tag name within the undrained batch) and re-arm. Returns the
        status. The worker drains them (gated on the human-touch latch)."""
        link = await active_lead_link(
            self._session, workspace_id=workspace_id, conversation_id=conversation_id
        )
        if link is None:
            return "no_link"
        existing = list(link.pending_field_ops or [])
        # custom_field and dnc ops both target a field_id (dedup together); tags by
        # name. (S4b: "dnc" is a latch-exempt custom-field write.)
        seen_fields = {
            o.get("field_id") for o in existing if o.get("kind") in ("custom_field", "dnc")
        }
        seen_tags = {o.get("name") for o in existing if o.get("kind") == "tag"}
        for op in ops:
            if op.get("kind") in ("custom_field", "dnc") and op.get("field_id") not in seen_fields:
                existing.append(op)
                seen_fields.add(op.get("field_id"))
            elif op.get("kind") == "tag" and op.get("name") not in seen_tags:
                existing.append(op)
                seen_tags.add(op.get("name"))
        link.pending_field_ops = existing
        rearm_lead_link(link)
        await self._session.flush()
        return "queued"

    async def _latest_state_packet(self, conversation_id: int) -> dict | None:
        """The latest ``set_state`` snapshot's ``state`` (a commercial packet with NO
        ``"facts"`` key) for note read-back — product/price accumulate across earlier
        turns, so the latest packet captures them. Complementary key filter to the
        facts read in ``_seed_role_for_conversation``. (#428)"""
        row = await self._session.execute(
            select(AgentConversationStateSnapshot.state)
            .where(
                AgentConversationStateSnapshot.conversation_id == conversation_id,
                ~AgentConversationStateSnapshot.state.has_key("facts"),
            )
            .order_by(
                AgentConversationStateSnapshot.created_at.desc(),
                AgentConversationStateSnapshot.id.desc(),
            )
            .limit(1)
        )
        return row.scalar_one_or_none()

    async def _seed_role_for_conversation(self, conversation_id: int) -> str:
        """Backfill the desired stage from the latest conversation-state facts so
        a pre-existing, already-advanced conversation doesn't re-enter at 'new'.

        Filter to facts-carrying rows: a newer ``conversation.set_state`` packet
        has no ``facts`` key, and a naive "newest row" read would let it hide the
        accrued facts and re-seed the lead at 'new'. (#423)"""
        row = await self._session.execute(
            select(AgentConversationStateSnapshot.state)
            .where(
                AgentConversationStateSnapshot.conversation_id == conversation_id,
                AgentConversationStateSnapshot.state.has_key("facts"),
            )
            .order_by(
                AgentConversationStateSnapshot.created_at.desc(),
                AgentConversationStateSnapshot.id.desc(),
            )
            .limit(1)
        )
        state = row.scalar_one_or_none() or {}
        facts = (state or {}).get("facts", {}) or {}
        target = target_role_for_facts(facts)
        return target if role_index(target) > role_index("new") else "new"

    async def _active_connection(self, workspace_id: int) -> CrmConnection | None:
        return (
            await self._session.execute(
                select(CrmConnection).where(
                    CrmConnection.workspace_id == workspace_id,
                    CrmConnection.status == "active",
                ).limit(1)
            )
        ).scalars().first()
