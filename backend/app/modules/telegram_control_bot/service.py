from __future__ import annotations

import html
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.outreach import OutreachCampaign, OutreachTarget
from app.models.workspace import Workspace
from app.modules.action_runtime.service import ActionRuntimeService
from app.modules.agent_business_actions.service import handoff_kind_from_refs
from app.modules.agent_control.service import AgentControlService
from app.modules.agent_runtime_v2.owner_turn import dispatch_owner_turn
from app.modules.commercial_spine.contracts import CommercialActionProposal
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.knowledge_mcp.service import KnowledgeMCPService
from app.modules.telegram_control_bot.bind_token_service import BindTokenService
from app.modules.telegram_control_bot.contracts import (
    TelegramControlBotCallbackResult,
    TelegramControlBotCard,
)


def _esc(value: str) -> str:
    """HTML-escape for Telegram text content — keep Uzbek apostrophes literal."""
    return html.escape(value, quote=False)


class TelegramControlBotClient(Protocol):
    async def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout_seconds: int = 2,
    ) -> list[dict[str, Any]]: ...

    async def edit_message_reply_markup(
        self,
        *,
        chat_id: int | str,
        message_id: int,
        reply_markup: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str,
        show_alert: bool = False,
    ) -> dict[str, Any]: ...


class HermesTelegramBotGatewayClient:
    """Thin OQIM client over Hermes' built-in Telegram bot gateway adapter.

    OQIM owns approval policy/audit. Hermes owns Telegram Bot API SDK loading
    and gateway-specific Telegram objects.
    """

    def __init__(self, *, token: str, timeout_seconds: float = 8.0) -> None:
        self._token = token
        self._timeout_seconds = timeout_seconds

    async def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        hermes_telegram = _hermes_telegram()
        bot = hermes_telegram.Bot(self._token)
        message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=(
                _inline_keyboard_markup(hermes_telegram, reply_markup)
                if reply_markup
                else None
            ),
        )
        return {"ok": True, "result": _message_result(message)}

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout_seconds: int = 2,
    ) -> list[dict[str, Any]]:
        hermes_telegram = _hermes_telegram()
        bot = hermes_telegram.Bot(self._token)
        updates = await bot.get_updates(
            offset=offset,
            timeout=timeout_seconds,
            allowed_updates=["message", "callback_query"],
        )
        return [update.to_dict() for update in updates]

    async def edit_message_reply_markup(
        self,
        *,
        chat_id: int | str,
        message_id: int,
        reply_markup: dict[str, Any],
    ) -> dict[str, Any]:
        hermes_telegram = _hermes_telegram()
        bot = hermes_telegram.Bot(self._token)
        result = await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=_inline_keyboard_markup(hermes_telegram, reply_markup),
        )
        return {"ok": True, "result": _message_result(result)}

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        hermes_telegram = _hermes_telegram()
        bot = hermes_telegram.Bot(self._token)
        result = await bot.answer_callback_query(
            callback_query_id=callback_query_id,
            text=text,
            show_alert=show_alert,
        )
        return {"ok": True, "result": result}


class DisabledTelegramControlBotClient:
    async def send_message(self, **_: Any) -> dict[str, Any]:
        return _disabled_control_bot_result()

    async def get_updates(self, **_: Any) -> list[dict[str, Any]]:
        return []

    async def edit_message_reply_markup(self, **_: Any) -> dict[str, Any]:
        return _disabled_control_bot_result()

    async def answer_callback_query(self, **_: Any) -> dict[str, Any]:
        return _disabled_control_bot_result()


def _disabled_control_bot_result() -> dict[str, Any]:
    return {"ok": False, "skipped": True, "reason": "telegram_control_bot_disabled"}


class _OwnerBotDelivery:
    """Adapts the owner bot client to the delivery interface dispatch_owner_turn
    expects: the owner reply is sent to the owner's chat via the control bot
    (not the seller's customer-facing DeliveryService)."""

    def __init__(self, client: TelegramControlBotClient) -> None:
        self._client = client

    async def deliver_message(self, conversation_id: int, text: str, **_: Any) -> None:
        await self._client.send_message(chat_id=conversation_id, text=text)
        return None


class TelegramControlBotService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        client: TelegramControlBotClient | None = None,
        bound_workspace_id: int | None = None,
    ) -> None:
        self._session = session
        self._bound_workspace_id = bound_workspace_id
        self._repository = CommercialSpineRepository(session)
        self._agent_control = AgentControlService(self._repository)
        self._action_runtime = ActionRuntimeService(self._repository)
        self._knowledge = KnowledgeMCPService(session)
        self._client = client or DisabledTelegramControlBotClient()

    async def send_approval_card(
        self,
        *,
        chat_id: int | str,
        proposal: CommercialActionProposal,
    ) -> dict[str, Any]:
        card = await self.approval_card(proposal)
        return await self._client.send_message(
            chat_id=chat_id,
            text=card.text,
            reply_markup=card.reply_markup,
            parse_mode="HTML",
        )

    async def approval_card(self, proposal: CommercialActionProposal) -> TelegramControlBotCard:
        # get_action validates against the AgentControlAction literal, which
        # does not cover every proposal type the runtime creates (e.g.
        # create_business_task) — fall back to the proposal's own action_type.
        try:
            action = await self._agent_control.get_action(
                workspace_id=proposal.workspace_id,
                action_id=proposal.proposal_id,
            )
        except Exception:
            action = None
        action_kind = action.action_kind if action is not None else proposal.action_type
        text = _owner_card_text(action_kind=action_kind, proposal=proposal)
        # A handoff card asks "will you handle this?", not "do you approve?".
        handoff = handoff_kind_from_refs(list(proposal.source_refs or []))
        approve_label = "\u2705 Men shug'ullanaman" if handoff else "\u2705 Tasdiqlash"
        reject_label = "\u274c Keraksiz" if handoff else "\u274c Rad etish"
        return TelegramControlBotCard(
            text=text,
            reply_markup={
                "inline_keyboard": [
                    [
                        {
                            "text": approve_label,
                            "callback_data": _callback_data(
                                action="approve",
                                workspace_id=proposal.workspace_id,
                                proposal_id=proposal.proposal_id,
                            ),
                        },
                        {
                            "text": reject_label,
                            "callback_data": _callback_data(
                                action="reject",
                                workspace_id=proposal.workspace_id,
                                proposal_id=proposal.proposal_id,
                            ),
                        },
                    ]
                ]
            },
        )

    async def handle_owner_message(self, update: dict[str, Any]) -> dict[str, Any]:
        """Bind a workspace owner to this bot chat (pilot-grade binding).

        ``/start`` answers with Uzbek instructions; a message containing a
        known workspace ``phone_number`` binds that workspace's
        ``owner_control_chat_id`` to the sender's chat. Phone-as-secret is
        pilot-grade; multi-tenant bind tokens are tracked under #405.
        """
        message = update.get("message")
        if not isinstance(message, dict):
            return {"ok": False, "reason": "message_missing"}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id = chat.get("id")
        if chat_id is None:
            return {"ok": False, "reason": "chat_missing"}
        text = str(message.get("text") or "").strip()

        if text.startswith("/start"):
            payload = text[len("/start"):].strip()
            if payload and self._bound_workspace_id is not None:
                bound = await BindTokenService(self._session).redeem(
                    token=payload,
                    bound_workspace_id=self._bound_workspace_id,
                    chat_id=int(chat_id),
                )
                if bound:
                    ws = await self._session.get(Workspace, self._bound_workspace_id)
                    agent = await self._resolve_owner_agent(self._bound_workspace_id)
                    tail = (
                        "Endi sozlash agentiga yozishingiz mumkin."
                        if agent is not None
                        else "Sozlash agenti hali yaratilmagan — uni "
                        "yaratganingizdan so'ng yozishingiz mumkin."
                    )
                    await self._client.send_message(
                        chat_id=chat_id,
                        text=f"✅ Ulandi: {ws.name}. {tail}",
                    )
                    return {
                        "ok": True,
                        "action": "owner_bound",
                        "workspace_id": int(self._bound_workspace_id),
                        "chat_id": int(chat_id),
                    }
                await self._client.send_message(
                    chat_id=chat_id,
                    text="Havola eskirgan yoki noto'g'ri. Yangi havola oling.",
                )
                return {"ok": False, "reason": "bind_failed", "chat_id": int(chat_id)}
            await self._client.send_message(
                chat_id=chat_id,
                text=(
                    "OQIM boshqaruv boti.\n\n"
                    "Ulanish uchun OQIM ilovasidagi “Telegramni ulash” "
                    "havolasini bosing."
                ),
            )
            return {"ok": True, "action": "start_instructions", "chat_id": int(chat_id)}

        if text == "/campaign" or text.startswith("/campaign "):
            return await self._handle_campaign_command(chat_id=int(chat_id), text=text)

        # Bound-owner free text (KEEP — re-gated on the lane, not typed phone). The
        # global/shared lane (bound_workspace_id is None) NEVER routes owner turns;
        # silently ignore everything else (the 2026-06-10 agent<->bot loop guard).
        if text and self._bound_workspace_id is not None:
            ws = await self._session.get(Workspace, self._bound_workspace_id)
            if ws is not None and ws.owner_control_chat_id == int(chat_id):
                return await self._route_owner_turn(
                    workspace=ws, chat_id=int(chat_id), text=text
                )
        return {"ok": False, "reason": "not_a_binding_message", "chat_id": int(chat_id)}

    async def _resolve_owner_agent(self, workspace_id: int) -> Agent | None:
        """The setup agent that fields owner turns, or None if none is configured.

        Deliberately does NOT fall back to the seller/default agent: running the
        owner's free text through the customer-selling profile (seller tools +
        selling persona, and no owner.edit_doc/media.store grants) is wrong
        (review #439). When no setup agent exists, the owner is told to create one.
        """
        return (
            await self._session.execute(
                select(Agent)
                .where(
                    Agent.workspace_id == workspace_id,
                    Agent.agent_type.in_(("owner", "setup", "setup_agent")),
                )
                .order_by(Agent.is_default.desc(), Agent.id)
            )
        ).scalars().first()

    async def _route_owner_turn(
        self, *, workspace: Workspace, chat_id: int, text: str
    ) -> dict[str, Any]:
        # Lazily ensure the Owner Agent exists — backfills workspaces (e.g. the
        # pilot) that provisioned their control bot before the creation hook landed.
        from app.modules.agent_runtime_v2.owner_agent import ensure_owner_agent

        await ensure_owner_agent(self._session, int(workspace.id))
        agent = await self._resolve_owner_agent(int(workspace.id))
        if agent is None:
            await self._client.send_message(
                chat_id=chat_id,
                text="Sozlash agenti hali ulanmagan. Avval sozlash agentini yarating.",
            )
            return {"ok": False, "reason": "no_owner_agent", "chat_id": chat_id}
        ok = await dispatch_owner_turn(
            db=self._session,
            workspace_id=int(workspace.id),
            agent_id=int(agent.id),
            owner_chat_id=chat_id,
            message_text=text,
            delivery=_OwnerBotDelivery(self._client),
        )
        return {
            "ok": bool(ok),
            "action": "owner_turn",
            "workspace_id": int(workspace.id),
            "chat_id": chat_id,
        }

    async def _handle_campaign_command(self, *, chat_id: int, text: str) -> dict[str, Any]:
        """`/campaign` -> status list; `/campaign pause|resume [id]` -> flip status.
        Resume IS the required human decision after any pause (incl. Slice C's
        PeerFlood auto-pause) — there is deliberately no auto-resume."""
        # Lane-scoped: the workspace is the bot that received this command, and
        # only the bound owner chat may run it (never a chat-id lookup — that
        # cross-acts when one chat is bound to >1 workspace).
        if self._bound_workspace_id is None:
            await self._client.send_message(
                chat_id=chat_id,
                text="Bu buyruq faqat shaxsiy boshqaruv botida ishlaydi.",
            )
            return {"ok": False, "reason": "no_lane_workspace", "chat_id": chat_id}
        workspace = await self._session.get(Workspace, self._bound_workspace_id)
        if workspace is None or workspace.owner_control_chat_id != chat_id:
            await self._client.send_message(
                chat_id=chat_id,
                text="Avval OQIM ilovasi orqali Telegramni ulang.",
            )
            return {"ok": False, "reason": "workspace_not_bound", "chat_id": chat_id}

        parts = text.split()
        action = parts[1].lower() if len(parts) > 1 else "status"
        campaign_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

        if action in ("pause", "resume"):
            campaign = await self._campaign_for_action(workspace.id, campaign_id, action)
            if campaign is None:
                await self._client.send_message(
                    chat_id=chat_id,
                    text=(
                        "Kampaniya topilmadi (yoki bir nechta mos keladi). "
                        "Holat: /campaign · Aniq: /campaign pause <id>"
                    ),
                )
                return {"ok": False, "reason": "campaign_not_found", "chat_id": chat_id}
            campaign.status = "running" if action == "resume" else "paused"
            await self._session.flush()
            label = "davom etmoqda" if action == "resume" else "pauzada"
            await self._client.send_message(
                chat_id=chat_id, text=f"Kampaniya '{campaign.name}' endi {label}."
            )
            return {
                "ok": True,
                "action": f"campaign_{action}",
                "campaign_id": int(campaign.id),
                "workspace_id": int(workspace.id),
                "chat_id": chat_id,
            }

        if action != "status":
            await self._client.send_message(
                chat_id=chat_id,
                text=("Noma'lum amal. Foydalanish: /campaign · "
                      "/campaign pause [id] · /campaign resume [id]"),
            )
            return {"ok": False, "reason": "unknown_action", "chat_id": chat_id}

        lines = await self._campaign_status_lines(workspace.id)
        await self._client.send_message(
            chat_id=chat_id,
            text="\n".join(lines) if lines else "Kampaniyalar yo'q.",
        )
        return {
            "ok": True,
            "action": "campaign_status",
            "workspace_id": int(workspace.id),
            "chat_id": chat_id,
        }

    async def _campaign_for_action(
        self, workspace_id: int, campaign_id: int | None, action: str
    ) -> OutreachCampaign | None:
        wanted_status = "paused" if action == "resume" else "running"
        stmt = select(OutreachCampaign).where(
            OutreachCampaign.workspace_id == workspace_id,
            OutreachCampaign.status == wanted_status,
        )
        if campaign_id is not None:
            stmt = stmt.where(OutreachCampaign.id == campaign_id)
        campaigns = (await self._session.execute(stmt)).scalars().all()
        return campaigns[0] if len(campaigns) == 1 else None

    async def _campaign_status_lines(self, workspace_id: int) -> list[str]:
        campaigns = (
            await self._session.execute(
                select(OutreachCampaign)
                .where(OutreachCampaign.workspace_id == workspace_id)
                .order_by(OutreachCampaign.id.desc())
                .limit(10)
            )
        ).scalars().all()
        lines: list[str] = []
        for campaign in campaigns:
            counts = dict(
                (
                    await self._session.execute(
                        select(OutreachTarget.state, func.count())
                        .where(OutreachTarget.campaign_id == campaign.id)
                        .group_by(OutreachTarget.state)
                    )
                ).all()
            )
            lines.append(
                f"#{campaign.id} {campaign.name} [{campaign.status}] — "
                f"{counts.get('sent', 0)} yuborildi · {counts.get('replied', 0)} javob · "
                f"{counts.get('pending', 0)} navbatda · {counts.get('skipped', 0)} o'tkazildi · "
                f"{counts.get('failed', 0)} xato"
            )
        return lines

    async def handle_update(self, update: dict[str, Any]) -> TelegramControlBotCallbackResult:
        callback = update.get("callback_query")
        if not isinstance(callback, dict):
            raise ValueError("callback_query_missing")
        parsed = _parse_callback_data(str(callback.get("data") or ""))
        actor_ref = _actor_ref(callback)
        proposal = await self._repository.get_action_proposal(
            workspace_id=parsed["workspace_id"],
            proposal_id=parsed["proposal_id"],
        )
        if proposal is None:
            await self._answer(callback, text="Action not found.", show_alert=True)
            raise ValueError("action_proposal_not_found")

        correlation_id = f"telegram-control:{callback.get('id') or proposal.proposal_id}"
        if parsed["action"] == "approve":
            status = await self._approve(
                proposal=proposal,
                actor_ref=actor_ref,
                correlation_id=correlation_id,
            )
            answer_text = "Approved."
        else:
            status = await self._reject(
                proposal=proposal,
                actor_ref=actor_ref,
                correlation_id=correlation_id,
            )
            answer_text = "Rejected."

        await self._answer(callback, text=answer_text)
        await self._clear_keyboard(callback)
        try:
            action = await self._agent_control.get_action(
                workspace_id=proposal.workspace_id,
                action_id=proposal.proposal_id,
            )
        except Exception:
            # Same literal gap as approval_card: proposal types outside the
            # AgentControlAction kinds (create_business_task) must still resolve.
            action = None
        action_kind = action.action_kind if action is not None else proposal.action_type
        return TelegramControlBotCallbackResult(
            ok=True,
            action=parsed["action"],  # type: ignore[arg-type]
            workspace_id=proposal.workspace_id,
            proposal_id=proposal.proposal_id,
            status=status,
            action_kind=action_kind,
            answer_text=answer_text,
        )

    async def _approve(
        self,
        *,
        proposal: CommercialActionProposal,
        actor_ref: str,
        correlation_id: str,
    ) -> str:
        if proposal.action_type == "knowledge.promote":
            result = await self._knowledge.approve_candidate_action(
                workspace_id=proposal.workspace_id,
                action_id=proposal.proposal_id,
                actor_ref=actor_ref,
                correlation_id=correlation_id,
            )
            return result.action.status
        approved = await self._action_runtime.approve(
            workspace_id=proposal.workspace_id,
            proposal_id=proposal.proposal_id,
            actor_ref=actor_ref,
            correlation_id=correlation_id,
        )
        return approved.lifecycle_state

    async def _reject(
        self,
        *,
        proposal: CommercialActionProposal,
        actor_ref: str,
        correlation_id: str,
    ) -> str:
        if proposal.action_type == "knowledge.promote":
            result = await self._knowledge.reject_candidate_action(
                workspace_id=proposal.workspace_id,
                action_id=proposal.proposal_id,
                actor_ref=actor_ref,
                correlation_id=correlation_id,
            )
            return result.action.status
        rejected = await self._action_runtime.reject(
            workspace_id=proposal.workspace_id,
            proposal_id=proposal.proposal_id,
            actor_ref=actor_ref,
            reason_code="telegram_control_rejected",
            correlation_id=correlation_id,
        )
        return rejected.lifecycle_state

    async def _answer(
        self,
        callback: dict[str, Any],
        *,
        text: str,
        show_alert: bool = False,
    ) -> None:
        callback_id = str(callback.get("id") or "")
        if callback_id:
            await self._client.answer_callback_query(
                callback_query_id=callback_id,
                text=text,
                show_alert=show_alert,
            )

    async def _clear_keyboard(self, callback: dict[str, Any]) -> None:
        message = callback.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict) or "id" not in chat or "message_id" not in message:
            return
        await self._client.edit_message_reply_markup(
            chat_id=chat["id"],
            message_id=int(message["message_id"]),
            reply_markup={"inline_keyboard": []},
        )


def _callback_data(*, action: str, workspace_id: int, proposal_id: str) -> str:
    action_code = "a" if action == "approve" else "r"
    return f"oqim:{action_code}:{workspace_id}:{proposal_id}"


def _parse_callback_data(data: str) -> dict[str, Any]:
    parts = data.split(":", 3)
    if len(parts) != 4 or parts[0] != "oqim" or parts[1] not in {"a", "r"}:
        raise ValueError("unsupported_callback_data")
    try:
        workspace_id = int(parts[2])
    except ValueError as exc:
        raise ValueError("invalid_workspace_id") from exc
    return {
        "action": "approve" if parts[1] == "a" else "reject",
        "workspace_id": workspace_id,
        "proposal_id": parts[3],
    }


def _actor_ref(callback: dict[str, Any]) -> str:
    sender = callback.get("from")
    if isinstance(sender, dict) and sender.get("id") is not None:
        return f"telegram_user:{sender['id']}"
    return "telegram_user:unknown"


# Handoff kind -> human header (spec 2026-06-10): the owner sees WHAT kind of
# human follow-up the agent recorded, not a generic bell.
HANDOFF_HEADERS = {
    "lead": "\U0001f7e0 Yangi lid",
    "support": "\U0001f535 Support so'rovi",
    "complaint": "\U0001f534 Shikoyat",
    "human_requested": "\U0001f7e1 Mijoz operator so'radi",
}


_OWNER_SECTION_LABELS = {
    "role_mission": "Rol va vazifa",
    "capabilities": "Imkoniyatlar",
    "behavior_rules": "Xulq-atvor qoidalari",
    "approval_rules": "Tasdiqlash qoidalari",
    "examples": "Namunalar",
    "must_never": "Taqiqlar",
}


def _owner_card_text(*, action_kind: str, proposal: CommercialActionProposal) -> str:
    """Human card text for the owner — no internal refs, no run ids.

    The first live cards showed raw debris ("Target: conversation:3,
    Evidence: agent_session:1, hermes_run:...") to a business owner
    (2026-06-10). Render the proposal's own human payload instead.
    """
    owner_task = proposal.payload.get("owner_task")
    if action_kind == "create_business_task" and isinstance(owner_task, dict):
        title = str(owner_task.get("title") or "Yangi vazifa").strip()
        detail = str(owner_task.get("detail") or owner_task.get("reason") or "").strip()
        raw_context = owner_task.get("context")
        context = raw_context if isinstance(raw_context, dict) else {}
        handoff = handoff_kind_from_refs(list(proposal.source_refs or []))
        handoff_header = HANDOFF_HEADERS.get(handoff) if handoff is not None else None
        if handoff_header is not None:
            header = f"{handoff_header} \u2014 Tasdiqlash kerak"
        else:
            kind_labels = {"call": "Qo'ng'iroq", "follow_up": "Kuzatuv", "delivery": "Yetkazish"}
            kind = kind_labels.get(str(owner_task.get("task_kind") or ""), "Vazifa")
            header = f"\U0001f7e0 Tasdiqlash kerak \u2014 {kind}"
        # HTML + emoji structure so the owner can skim in two seconds:
        # bold header / bold title / \ud83d\udc64 who / \ud83d\udcdd chat / detail / \u27a1\ufe0f next step
        lines = [f"<b>{_esc(header)}</b>", "", f"<b>{_esc(title)}</b>"]
        customer_label = str(context.get("customer_label") or "").strip()
        customer_phone = str(context.get("customer_phone") or "").strip()
        telegram_link = str(context.get("telegram_link") or "").strip()
        chat_summary = str(context.get("chat_summary") or "").strip()
        recommended = str(context.get("recommended_action") or "").strip()
        if customer_label:
            name, sep, rest = customer_label.partition(" (")
            name_html = _esc(name)
            if telegram_link:
                # deterministic jump link to the customer's Telegram profile
                name_html = f'<a href="{_esc(telegram_link)}">{name_html}</a>'
            line = f"\U0001f464 <b>{name_html}</b>"
            if customer_phone:
                line += f" · <code>{_esc(customer_phone)}</code>"  # tap to copy
            elif sep:
                line += f" ({_esc(rest)}"
            lines.append(line)
        if chat_summary:
            lines.append(f"\U0001f4dd Suhbat: <i>{_esc(chat_summary)}</i>")
        if detail and detail != title:
            lines.append("")
            lines.append(_esc(detail))
        if recommended:
            lines.append("")
            lines.append(f"\u27a1\ufe0f <b>Keyingi qadam:</b> {_esc(recommended)}")
        return "\n".join(lines)
    if action_kind == "knowledge.promote":
        return "\U0001f4d8 Bilim bazasiga qo'shish uchun tasdiqlash kerak."
    if action_kind == "agent.update_owner_config":
        section_key = str(proposal.payload.get("section_key") or "")
        section_label = _OWNER_SECTION_LABELS.get(section_key, "Sozlama")
        return (
            "<b>⚙️ Sozlamani yangilash — Tasdiqlash kerak</b>\n\n"
            f"Bo'lim: <b>{_esc(section_label)}</b>"
        )
    title = _title_for_action(action_kind)
    target = _target_for_card(proposal)
    return (
        f"<b>{_esc(title)}</b>\n\n"
        f"Target: {_esc(target)}\nRisk: {_esc(str(proposal.risk_level))}"
    )


def _title_for_action(action_kind: str) -> str:
    if action_kind == "reply.send":
        return "Agent action approval"
    if action_kind == "knowledge.promote":
        return "Knowledge promotion approval"
    return f"Agent action approval: {action_kind}"


def _target_for_card(proposal: CommercialActionProposal) -> str:
    if proposal.conversation_id:
        return f"conversation:{proposal.conversation_id}"
    control = proposal.payload.get("agent_control")
    if isinstance(control, dict) and control.get("target_ref"):
        return str(control["target_ref"])
    return proposal.action_type


def _hermes_telegram() -> Any:
    from gateway.platforms import telegram as hermes_telegram

    if not hermes_telegram.check_telegram_requirements():
        raise RuntimeError("hermes_telegram_gateway_unavailable")
    return hermes_telegram


def _inline_keyboard_markup(hermes_telegram: Any, reply_markup: dict[str, Any]) -> Any:
    rows = []
    for row in list(reply_markup.get("inline_keyboard") or []):
        buttons = []
        for button in list(row or []):
            buttons.append(
                hermes_telegram.InlineKeyboardButton(
                    text=str(button.get("text") or ""),
                    callback_data=str(button.get("callback_data") or ""),
                )
            )
        rows.append(buttons)
    return hermes_telegram.InlineKeyboardMarkup(rows)


def _message_result(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value
