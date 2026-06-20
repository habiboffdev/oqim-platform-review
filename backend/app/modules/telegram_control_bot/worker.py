"""Owner control-bot worker: poll updates, flush notifications, push cards.

Closes the live dead-end found in the pilot lead flow (2026-06-09): the agent
recorded `owner.notify` projections and approval-gated tasks, but nothing
delivered them — the owner was never told to call the customer. This worker:

- long-polls the Bot API (`get_updates`) and routes callback queries to the
  existing Approve/Reject handler and plain messages to owner binding;
- flushes `owner_notification` projections (`state.status=queued`) to the
  bound owner chat and marks them delivered;
- pushes an Approve/Reject card once per approval-gated `proposed` proposal
  (idempotent via an `owner_approval_card:{proposal_id}` projection).

Tokens are per-workspace (`workspaces.control_bot_token`, self-provisioned by
OQIM through the BotFather conversation — see provisioner.py), with the env
``TELEGRAM_CONTROL_BOT_TOKEN`` client as a global fallback for workspaces
without their own bot. Transport is polling (founder decision 2026-06-10):
no public HTTPS endpoint on the pilot VM.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.commerce_catalog import (
    CatalogMissingFieldRecord,
    CatalogProductRecord,
)
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.models.workspace import Workspace
from app.modules.commercial_spine.contracts import BusinessBrainProjection
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.telegram_control_bot.service import (
    TelegramControlBotClient,
    TelegramControlBotService,
)

logger = logging.getLogger(__name__)

def _esc(value: str) -> str:
    """HTML-escape for Telegram text content — keep Uzbek apostrophes literal."""
    return html.escape(value, quote=False)


_GLOBAL_KEY = "__global__"


class OwnerControlBotWorker:
    def __init__(
        self,
        *,
        db_factory: Callable[[], AsyncSession],
        client: TelegramControlBotClient | None = None,
        client_factory: Callable[[str], TelegramControlBotClient] | None = None,
        poll_timeout_seconds: int = 2,
        idle_sleep_seconds: float = 1.0,
        flush_batch_size: int = 20,
    ) -> None:
        self._db_factory = db_factory
        self._global_client = client
        self._client_factory = client_factory
        self._clients_by_token: dict[str, TelegramControlBotClient] = {}
        self._lane_workspace: dict[str, int] = {}
        self._poll_timeout = poll_timeout_seconds
        self._idle_sleep = idle_sleep_seconds
        self._flush_batch = flush_batch_size
        self._offsets: dict[str, int | None] = {}
        self._stopping = False
        self._beat: Callable[[], None] = lambda: None

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._beat = callback or (lambda: None)

    async def start(self) -> None:
        self._stopping = False
        while not self._stopping:
            try:
                handled = await self._tick()
                self._beat()
                if not handled:
                    await asyncio.sleep(self._idle_sleep)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("owner_control_bot.tick_failed", exc_info=exc)
                await asyncio.sleep(2.0)

    async def stop(self) -> None:
        self._stopping = True

    async def _tick(self) -> int:
        workspace_clients = await self._workspace_clients()
        handled = 0
        for offset_key, bound_workspace_id, lane_client in self._drain_lanes(workspace_clients):
            handled += await self._drain_updates(
                lane_client, offset_key=offset_key, bound_workspace_id=bound_workspace_id
            )
        handled += await self._flush_notifications(workspace_clients)
        handled += await self._push_approval_cards(workspace_clients)
        handled += await self._flush_missing_field_cards(workspace_clients)
        return handled

    async def _workspace_clients(self) -> dict[int, TelegramControlBotClient]:
        """Map workspace -> bot client (own token first, global fallback)."""
        clients: dict[int, TelegramControlBotClient] = {}
        async with self._db_factory() as session:
            rows = (
                await session.execute(
                    select(Workspace.id, Workspace.control_bot_token).where(
                        Workspace.is_active.is_(True)
                    )
                )
            ).all()
        for workspace_id, token in rows:
            if token and self._client_factory is not None:
                cached = self._clients_by_token.get(token)
                if cached is None:
                    cached = self._client_factory(token)
                    self._clients_by_token[token] = cached
                self._lane_workspace[token] = int(workspace_id)
                clients[int(workspace_id)] = cached
            elif self._global_client is not None:
                clients[int(workspace_id)] = self._global_client
        return clients

    def _drain_lanes(
        self, workspace_clients: dict[int, TelegramControlBotClient]
    ) -> list[tuple[str, int | None, TelegramControlBotClient]]:
        # Each lane carries the workspace it proves: a dedicated-token lane -> its
        # workspace_id; the shared/global lane -> None (never binds/routes).
        lanes: list[tuple[str, int | None, TelegramControlBotClient]] = []
        for token, lane_client in self._clients_by_token.items():
            if lane_client in workspace_clients.values():
                lanes.append(
                    (f"token:{token[:16]}", self._lane_workspace.get(token), lane_client)
                )
        if self._global_client is not None:
            lanes.append((_GLOBAL_KEY, None, self._global_client))
        return lanes

    # ── inbound: owner messages + approve/reject callbacks ──

    async def _drain_updates(
        self,
        lane_client: TelegramControlBotClient,
        *,
        offset_key: str = _GLOBAL_KEY,
        bound_workspace_id: int | None = None,
    ) -> int:
        updates = await lane_client.get_updates(
            offset=self._offsets.get(offset_key),
            timeout_seconds=self._poll_timeout,
        )
        handled = 0
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offsets[offset_key] = update_id + 1
            try:
                async with self._db_factory() as session:
                    service = TelegramControlBotService(
                        session=session,
                        client=lane_client,
                        bound_workspace_id=bound_workspace_id,
                    )
                    if isinstance(update.get("callback_query"), dict):
                        await service.handle_update(update)
                    elif isinstance(update.get("message"), dict):
                        await service.handle_owner_message(update)
                    await session.commit()
                handled += 1
            except Exception as exc:  # one bad update must not stall the queue
                logger.error(
                    "owner_control_bot.update_failed",
                    exc_info=exc,
                    extra={"update_id": update_id},
                )
        return handled

    # ── outbound: queued owner notifications ──

    async def _flush_notifications(
        self, workspace_clients: dict[int, TelegramControlBotClient]
    ) -> int:
        async with self._db_factory() as session:
            rows = (
                await session.execute(
                    select(BusinessBrainProjectionRecord)
                    .where(
                        BusinessBrainProjectionRecord.projection_type == "owner_notification",
                        BusinessBrainProjectionRecord.state["status"].as_string() == "queued",
                    )
                    .order_by(BusinessBrainProjectionRecord.id.asc())
                    .limit(self._flush_batch)
                )
            ).scalars().all()
            if not rows:
                return 0
            chat_ids = await self._owner_chat_ids(
                session, {int(row.workspace_id) for row in rows}
            )
            delivered = 0
            for row in rows:
                chat_id = chat_ids.get(int(row.workspace_id))
                lane_client = workspace_clients.get(int(row.workspace_id))
                if chat_id is None or lane_client is None:
                    continue  # stays queued until the owner binds / bot exists
                payload = dict(row.state.get("bot_payload") or {})
                try:
                    await lane_client.send_message(
                        chat_id=chat_id,
                        text=_notification_text(payload),
                        parse_mode="HTML",
                    )
                except Exception as exc:  # stays queued; retried next tick
                    logger.error(
                        "owner_control_bot.notify_failed",
                        exc_info=exc,
                        extra={"projection_ref": row.projection_ref},
                    )
                    continue
                row.state = {
                    **dict(row.state),
                    "status": "delivered",
                    "delivered_at": utc_now().isoformat(),
                    "delivered_chat_id": int(chat_id),
                }
                row.updated_at = utc_now()
                delivered += 1
            await session.commit()
            return delivered

    # ── outbound: Approve/Reject cards for approval-gated proposals ──

    async def _push_approval_cards(
        self, workspace_clients: dict[int, TelegramControlBotClient]
    ) -> int:
        async with self._db_factory() as session:
            # Fresh proposals only: pushing the full historical backlog
            # spammed 6 stale cards the moment the pilot bot went live.
            freshness_cutoff = utc_now() - timedelta(hours=48)
            rows = (
                await session.execute(
                    select(CommercialActionProposalRecord)
                    .where(
                        CommercialActionProposalRecord.lifecycle_state == "proposed",
                        CommercialActionProposalRecord.requires_approval.is_(True),
                        CommercialActionProposalRecord.created_at >= freshness_cutoff,
                    )
                    .order_by(CommercialActionProposalRecord.id.asc())
                    .limit(self._flush_batch)
                )
            ).scalars().all()
            if not rows:
                return 0
            chat_ids = await self._owner_chat_ids(
                session, {int(row.workspace_id) for row in rows}
            )
            repository = CommercialSpineRepository(session)
            pushed = 0
            for row in rows:
                chat_id = chat_ids.get(int(row.workspace_id))
                lane_client = workspace_clients.get(int(row.workspace_id))
                if chat_id is None or lane_client is None:
                    continue
                card_ref = f"owner_approval_card:{row.proposal_id}"
                already_sent = await repository.get_projection(
                    workspace_id=int(row.workspace_id),
                    projection_ref=card_ref,
                )
                if already_sent is not None:
                    continue
                proposal = await repository.get_action_proposal(
                    workspace_id=int(row.workspace_id),
                    proposal_id=str(row.proposal_id),
                )
                if proposal is None:
                    continue
                service = TelegramControlBotService(session=session, client=lane_client)
                try:
                    await service.send_approval_card(chat_id=chat_id, proposal=proposal)
                except Exception as exc:  # not marked sent; retried next tick
                    logger.error(
                        "owner_control_bot.card_failed",
                        exc_info=exc,
                        extra={"proposal_id": row.proposal_id},
                    )
                    continue
                await repository.upsert_projection(
                    BusinessBrainProjection(
                        projection_ref=card_ref,
                        workspace_id=int(row.workspace_id),
                        projection_type="owner_approval_card",
                        entity_ref=f"action_proposal:{row.proposal_id}",
                        state={
                            "status": "sent",
                            "chat_id": int(chat_id),
                            "sent_at": utc_now().isoformat(),
                        },
                        source_refs=[f"action_proposal:{row.proposal_id}"],
                    )
                )
                pushed += 1
            await session.commit()
            return pushed

    # ── outbound: "agent is blind here" cards for missing catalog fields ──

    async def _flush_missing_field_cards(
        self, workspace_clients: dict[int, TelegramControlBotClient]
    ) -> int:
        """One card per product + field-set: the owner learns exactly which
        business facts the agent cannot answer (price, venue, ...). Deduped by
        an `owner_missing_field_card:{product_ref}:{fields_hash}` projection so
        the owner is re-notified only when the gap set actually changes."""
        if not workspace_clients:
            return 0
        async with self._db_factory() as session:
            # Intentional asymmetry: the row stays `candidate` until the owner
            # fills the gap via the source-approval flow — the projection alone
            # gates dedup here.
            rows = (
                await session.execute(
                    select(CatalogMissingFieldRecord)
                    .where(
                        CatalogMissingFieldRecord.authority_state == "candidate",
                        CatalogMissingFieldRecord.workspace_id.in_(
                            list(workspace_clients.keys())
                        ),
                    )
                    .order_by(CatalogMissingFieldRecord.id.asc())
                    .limit(self._flush_batch)
                )
            ).scalars().all()
            if not rows:
                return 0
            grouped: dict[tuple[int, str], list[str]] = {}
            for row in rows:
                key = (int(row.workspace_id), str(row.product_ref))
                grouped.setdefault(key, []).append(str(row.field))
            chat_ids = await self._owner_chat_ids(
                session, {workspace_id for workspace_id, _ in grouped}
            )
            repository = CommercialSpineRepository(session)
            sent = 0
            for (workspace_id, product_ref), fields in grouped.items():
                chat_id = chat_ids.get(workspace_id)
                lane_client = workspace_clients.get(workspace_id)
                if chat_id is None or lane_client is None:
                    continue
                fields_sorted = sorted(set(fields))
                fields_hash = hashlib.sha256(
                    ",".join(fields_sorted).encode("utf-8")
                ).hexdigest()[:12]
                card_ref = f"owner_missing_field_card:{product_ref}:{fields_hash}"
                already_sent = await repository.get_projection(
                    workspace_id=workspace_id, projection_ref=card_ref
                )
                if already_sent is not None:
                    continue
                product = await session.scalar(
                    select(CatalogProductRecord).where(
                        CatalogProductRecord.workspace_id == workspace_id,
                        CatalogProductRecord.product_ref == product_ref,
                    )
                )
                product_name = product.name if product is not None else product_ref
                try:
                    await lane_client.send_message(
                        chat_id=chat_id,
                        text=_missing_field_text(
                            product_name=product_name, fields=fields_sorted
                        ),
                        parse_mode="HTML",
                    )
                except Exception as exc:  # not marked sent; retried next tick
                    logger.error(
                        "owner_control_bot.missing_field_card_failed",
                        exc_info=exc,
                        extra={"product_ref": product_ref},
                    )
                    continue
                await repository.upsert_projection(
                    BusinessBrainProjection(
                        projection_ref=card_ref,
                        workspace_id=workspace_id,
                        projection_type="owner_missing_field_card",
                        entity_ref=f"catalog_product:{product_ref}",
                        state={"status": "sent", "fields": fields_sorted},
                        source_refs=[f"catalog_product:{product_ref}"],
                    )
                )
                sent += 1
            await session.commit()
            return sent

    async def _owner_chat_ids(
        self, session: AsyncSession, workspace_ids: set[int]
    ) -> dict[int, int]:
        if not workspace_ids:
            return {}
        rows = (
            await session.execute(
                select(Workspace.id, Workspace.owner_control_chat_id).where(
                    Workspace.id.in_(workspace_ids),
                    Workspace.owner_control_chat_id.is_not(None),
                )
            )
        ).all()
        return {int(workspace_id): int(chat_id) for workspace_id, chat_id in rows}


def _notification_text(payload: dict[str, Any]) -> str:
    """HTML notification text — bold header + 👤/📝/➡️ skimmable structure.

    Sent with parse_mode=HTML; every dynamic value is escaped.
    """
    from app.modules.agent_business_actions.service import handoff_kind_from_refs
    from app.modules.telegram_control_bot.service import HANDOFF_HEADERS

    title = str(payload.get("title") or "Yangi xabar").strip()
    summary = str(payload.get("summary") or "").strip()
    recommended = str(payload.get("recommended_action") or "").strip()
    customer_label = str(payload.get("customer_label") or "").strip()
    chat_summary = _ellipsize(str(payload.get("chat_summary") or "").strip(), 220)
    handoff = handoff_kind_from_refs(payload.get("source_refs"))
    handoff_header = HANDOFF_HEADERS.get(handoff) if handoff is not None else None
    if handoff_header is not None:
        # kind-tagged handoff: the kind emoji (and label, unless the title
        # already says it) replaces the generic bell. A kind without a header
        # entry degrades to the generic bell instead of crashing.
        emoji, label = handoff_header.split(" ", 1)
        first = (
            f"{emoji} {_esc(title)}"
            if label.lower() in title.lower()
            else f"{emoji} {_esc(label)}: {_esc(title)}"
        )
        lines = [f"<b>{first}</b>"]
    else:
        lines = [f"<b>\N{BELL} {_esc(title)}</b>"]
    # context block: who + what the chat was about — decision-ready at a glance
    context_lines = []
    if customer_label:
        name, sep, rest = customer_label.partition(" (")
        tail = f" ({_esc(rest)}" if sep else ""
        context_lines.append(f"\U0001f464 <b>{_esc(name)}</b>{tail}")
    if chat_summary:
        context_lines.append(f"\U0001f4dd Suhbat: <i>{_esc(chat_summary)}</i>")
    if context_lines:
        lines.append("")
        lines.extend(context_lines)
    if summary and summary != chat_summary:
        lines.append("")
        lines.append(_esc(summary))
    if recommended:
        lines.append("")
        lines.append(f"➡️ <b>Keyingi qadam:</b> {_esc(recommended)}")
    return "\n".join(lines)


_MISSING_FIELD_LABELS = {
    "price": "narx",
    "exact_venue": "aniq manzil",
    "seat_count": "o'rinlar soni",
    "payment_details": "to'lov rekvizitlari",
    "stock": "ombor holati",
}


def _missing_field_text(*, product_name: str, fields: list[str]) -> str:
    rendered = ", ".join(_MISSING_FIELD_LABELS.get(field, field) for field in fields)
    return (
        "<b>⚠️ Agent bilmaydi</b>\n\n"
        f"<b>{_esc(product_name)}</b> bo'yicha quyidagilar tasdiqlanmagan: "
        f"<b>{_esc(rendered)}</b>.\n\n"
        "Mijozlar so'raganda agent aniq javob bera olmayapti. "
        "Bu ma'lumotlarni workbenchda manba sifatida kiritsangiz, "
        "tasdiqlangach agent darhol ishlata boshlaydi."
    )


def _ellipsize(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
