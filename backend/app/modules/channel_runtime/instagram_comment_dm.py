"""Comment->DM: owner-bounded, agent-judged Instagram private replies.

Boundary = an active Trigger row (event_source="instagram_comment_received",
matching_scope={"all_posts": bool, "post_ids": [...], "goal": str}).
Judgment = CONTROL_CHAIN structured call (LLM judgment, never keyword match).
Action = Instagram private reply (the only sanctioned business-initiated DM).
The opener NEVER states business facts; the grounded conversation happens in
the DM thread via the normal Hermes pipeline once the user replies.

Durability order: the commenter-keyed message row (with the igpr dedup key)
is COMMITTED before the Graph send. A crash between persist and send leaves a
'failed'-retryable row, so Meta webhook redelivery retries the send instead of
duplicating the DM.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.llm import generate_structured_json
from app.brain.llm_policy import CONTROL_CHAIN
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.trigger import Trigger
from app.models.workspace import Workspace
from app.modules.conversation_core.service import PersistMessageInput, persist_message
from app.services.delivery_runtime import (
    DELIVERY_CONFIRMED,
    DELIVERY_FAILED,
    DELIVERY_SENDING,
)
from app.services.instagram_channel_adapter import InstagramChannelAdapter
from app.services.instagram_messaging_policy import queue_instagram_owner_notification

logger = get_logger("channel_runtime.instagram_comment_dm")

DEFAULT_HOURLY_CAP = 30
_PRIVATE_REPLY_UUID_PREFIX = "igpr:"
# Graph rejects private replies over 1000 chars; clamp with margin.
_MAX_OPENER_CHARS = 900


class InstagramCommentDmDecision(BaseModel):
    should_dm: bool = False
    reason: str = ""
    opening_message: str = ""


@dataclass(slots=True)
class CommentDmResult:
    sent: bool
    skipped_reason: str | None = None
    external_message_id: str | None = None


class InstagramCommentDmService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        http_client_factory: Callable[..., Any] = httpx.AsyncClient,
        hourly_cap: int = DEFAULT_HOURLY_CAP,
    ) -> None:
        self._db = db
        self._http_client_factory = http_client_factory
        self._hourly_cap = hourly_cap

    async def handle_comment(
        self, *, workspace: Workspace, value: dict[str, Any]
    ) -> CommentDmResult:
        comment_id = str(value.get("id") or "")
        comment_text = str(value.get("text") or "")
        media_id = str((value.get("media") or {}).get("id") or "")
        author = value.get("from") or {}
        author_id = str(author.get("id") or "")
        author_username = str(author.get("username") or "")

        if not comment_id or not author_id:
            return CommentDmResult(sent=False, skipped_reason="malformed_payload")
        # The business's own comment may arrive under either stored id (Meta's
        # id vs user_id). Match both so the bot never DMs itself.
        own_ids = {
            str(workspace.instagram_page_id or "") or None,
            str(workspace.instagram_account_id or "") or None,
        }
        if author_id in own_ids:
            return CommentDmResult(sent=False, skipped_reason="own_comment")

        trigger = await self._matching_trigger(workspace.id, media_id)
        if trigger is None:
            return CommentDmResult(sent=False, skipped_reason="no_active_trigger")

        if await self._already_replied(workspace.id, comment_id):
            return CommentDmResult(sent=False, skipped_reason="duplicate_comment")

        if await self._hourly_count(workspace.id) >= self._hourly_cap:
            logger.warning(
                "instagram comment-dm hourly cap reached workspace=%s cap=%s",
                workspace.id,
                self._hourly_cap,
            )
            await queue_instagram_owner_notification(
                self._db,
                workspace_id=workspace.id,
                title="Instagram izoh-DM soatlik chegarasi",
                summary=(
                    f"Bir soat ichida {self._hourly_cap} ta izoh-DM yuborildi -- "
                    "yangi izohlar bu soatda DM olmaydi."
                ),
                recommended_action="Kerak bo'lsa chegarani oshirishni so'rang.",
                idempotency_key=(
                    f"ig_comment_cap:{workspace.id}:{datetime.now(UTC).strftime('%Y%m%d%H')}"
                ),
            )
            return CommentDmResult(sent=False, skipped_reason="hourly_cap_reached")

        scope = trigger.matching_scope if isinstance(trigger.matching_scope, dict) else {}
        goal = str(scope.get("goal") or "(ko'rsatilmagan)")
        # A flaky LLM response must not 500 the webhook entry: any judgment
        # failure declines safely. Infra raise semantics live at the send.
        try:
            decision_raw = await generate_structured_json(
                chain=CONTROL_CHAIN,
                system=_comment_dm_system_prompt(),
                prompt=(
                    f"Biznes: {workspace.name}\n"
                    f"Egasining maqsadi: {goal}\n"
                    f"Post: {media_id}\n"
                    f"Izoh muallifi: @{author_username or author_id}\n"
                    f"Izoh matni: {comment_text}"
                ),
                response_schema=InstagramCommentDmDecision,
                operation="instagram_comment_dm",
                workspace_id=workspace.id,
            )
            decision = InstagramCommentDmDecision.model_validate(decision_raw or {})
        except Exception as exc:
            logger.warning(
                "instagram comment-dm judgment failed workspace=%s comment=%s error=%s",
                workspace.id,
                comment_id,
                type(exc).__name__,
            )
            return CommentDmResult(sent=False, skipped_reason="judgment_failed")

        opening = decision.opening_message.strip()[:_MAX_OPENER_CHARS]
        if not decision.should_dm or not opening:
            if decision.should_dm and not opening:
                # Prompt-quality signal: the judge wanted a DM but wrote no opener.
                logger.info(
                    "instagram comment-dm declined: should_dm without opener "
                    "workspace=%s comment=%s",
                    workspace.id,
                    comment_id,
                )
            return CommentDmResult(sent=False, skipped_reason="agent_declined")

        # Persist the placeholder FIRST (commenter-keyed customer + conversation
        # + durable igpr dedup key), THEN send. The customer IS the commenter:
        # sender_external_id keys the Customer row on the IG user id, so the
        # inbound DM pipeline finds the SAME customer/conversation when the
        # user replies. A failed earlier attempt is REUSED, never duplicated.
        dedup_key = f"{_PRIVATE_REPLY_UUID_PREFIX}{comment_id}"
        failed_retry = await self._find_failed_reply(workspace.id, comment_id)
        if failed_retry is not None:
            failed_retry.content = opening
            failed_retry.delivery_state = DELIVERY_SENDING
            message = failed_retry
            conversation_id = failed_retry.conversation_id
        else:
            persisted = await persist_message(
                self._db,
                PersistMessageInput(
                    workspace_id=workspace.id,
                    channel="instagram_dm",
                    external_chat_id=author_id,
                    sender_external_id=author_id,
                    sender_id=None,
                    sender_name=author_username or author_id,
                    text=opening,
                    is_outgoing=True,
                    media_metadata={
                        "instagram_comment_id": comment_id,
                        "instagram_media_id": media_id,
                        "instagram_private_reply": True,
                    },
                ),
            )
            message = persisted.message
            conversation_id = persisted.conversation.id
            message.client_message_uuid = dedup_key
            message.delivery_state = DELIVERY_SENDING
        await self._db.commit()  # durable dedup key BEFORE the send

        adapter = InstagramChannelAdapter(
            account_id=str(workspace.instagram_page_id or ""),
            access_token=workspace.instagram_access_token,
            http_client_factory=self._http_client_factory,
        )
        try:
            send_result = await adapter.send_private_reply(
                workspace_id=workspace.id,
                comment_id=comment_id,
                text=opening,
                idempotency_key=dedup_key,
            )
        except Exception as exc:
            message.delivery_state = DELIVERY_FAILED
            await self._db.commit()
            logger.warning(
                "instagram private reply send failed workspace=%s comment=%s error=%s",
                workspace.id,
                comment_id,
                type(exc).__name__,
            )
            return CommentDmResult(sent=False, skipped_reason="send_failed")

        message.external_message_id = send_result.external_message_id or None
        message.delivery_state = DELIVERY_CONFIRMED
        await self._db.commit()

        logger.info(
            "instagram private reply sent workspace=%s comment=%s conversation=%s",
            workspace.id,
            comment_id,
            conversation_id,
        )
        return CommentDmResult(
            sent=True, external_message_id=send_result.external_message_id
        )

    async def _matching_trigger(self, workspace_id: int, media_id: str) -> Trigger | None:
        triggers = (
            await self._db.execute(
                select(Trigger).where(
                    Trigger.workspace_id == workspace_id,
                    Trigger.event_source == "instagram_comment_received",
                    Trigger.active.is_(True),
                )
            )
        ).scalars().all()
        for trigger in triggers:
            scope = trigger.matching_scope if isinstance(trigger.matching_scope, dict) else {}
            if scope.get("all_posts") is True:
                return trigger
            post_ids = scope.get("post_ids")
            if isinstance(post_ids, list) and media_id in [str(p) for p in post_ids]:
                return trigger
        return None

    async def _already_replied(self, workspace_id: int, comment_id: str) -> bool:
        # Failed sends are retryable: only non-failed rows block a redelivery.
        count = await self._db.scalar(
            select(func.count(Message.id))
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.workspace_id == workspace_id,
                Message.client_message_uuid == f"{_PRIVATE_REPLY_UUID_PREFIX}{comment_id}",
                Message.delivery_state != DELIVERY_FAILED,
            )
        )
        return bool(count)

    async def _find_failed_reply(
        self, workspace_id: int, comment_id: str
    ) -> Message | None:
        """A prior failed attempt for this comment, to reuse instead of
        persisting a second row (its external_message_id is None, so
        persist_message dedup would not match)."""
        return (
            (
                await self._db.execute(
                    select(Message)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(
                        Conversation.workspace_id == workspace_id,
                        Message.client_message_uuid
                        == f"{_PRIVATE_REPLY_UUID_PREFIX}{comment_id}",
                        Message.delivery_state == DELIVERY_FAILED,
                    )
                    .order_by(Message.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

    async def _hourly_count(self, workspace_id: int) -> int:
        since = datetime.now(UTC) - timedelta(hours=1)
        count = await self._db.scalar(
            select(func.count(Message.id))
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.workspace_id == workspace_id,
                Message.client_message_uuid.like(f"{_PRIVATE_REPLY_UUID_PREFIX}%"),
                Message.created_at >= since,
            )
        )
        return int(count or 0)


@lru_cache(maxsize=1)
def _comment_dm_system_prompt() -> str:
    from app.brain.prompt_registry import get_prompt_registry

    return (
        get_prompt_registry()
        .load("agent_runtime.instagram_comment_dm", version="1.0.0")
        .body.strip()
    )
