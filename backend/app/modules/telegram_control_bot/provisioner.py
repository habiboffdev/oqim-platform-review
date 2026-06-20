"""Self-provisioning: OQIM creates its own owner control bot via BotFather.

The workspace's connected Telegram account (the GramJS sidecar userbot) talks
to @BotFather exactly like a human would — /newbot, name, username — then OQIM
parses the token out of BotFather's reply, stores it on the workspace, polishes
the bot through the Bot API (name/description/about), optionally sets the
profile photo through the /setuserpic conversation, /start-s the new bot from
the userbot account so it is allowed to message that chat, and binds
``owner_control_chat_id``. Zero human steps.

Founder direction (2026-06-10): "oqim must create the bot itself, while
talking to botfather — it has access to my telegram account."
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.models.workspace import Workspace

logger = logging.getLogger(__name__)

BOTFATHER = "BotFather"
_TOKEN_RE = re.compile(r"(\d{6,}:[A-Za-z0-9_-]{30,})")
_USERNAME_TAKEN_MARKERS = ("already taken", "sorry", "invalid", "occupied")


class BotFatherChannel(Protocol):
    """A conversation lane to @BotFather through the workspace userbot."""

    async def last_message_id(self) -> int: ...

    async def send(self, text: str, *, media_url: str | None = None) -> None: ...

    async def await_reply(self, *, after_id: int, timeout_seconds: float = 20.0) -> str: ...


class SidecarBotFatherChannel:
    """BotFatherChannel over the GramJS sidecar (/send + /messages)."""

    def __init__(
        self,
        *,
        sidecar_url: str,
        sidecar_api_key: str,
        workspace_id: int,
        chat: str = BOTFATHER,
        poll_seconds: float = 1.0,
    ) -> None:
        self._base = sidecar_url.rstrip("/")
        self._api_key = sidecar_api_key
        self._workspace_id = workspace_id
        self._chat = chat
        self._poll = poll_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-Sidecar-Key"] = self._api_key
        return headers

    async def last_message_id(self) -> int:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self._base}/messages",
                params={
                    "workspaceId": self._workspace_id,
                    "chatId": self._chat,
                    "limit": 1,
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            messages = response.json()
        return max((int(m.get("messageId") or 0) for m in messages), default=0)

    async def send(self, text: str, *, media_url: str | None = None) -> None:
        payload: dict[str, Any] = {
            "workspaceId": self._workspace_id,
            "chatId": self._chat,
            "idempotencyKey": f"botfather:{uuid.uuid4()}",
        }
        if media_url:
            payload["media"] = {"url": media_url}
            if text:
                payload["caption"] = text
        else:
            payload["text"] = text
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base}/send",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()

    async def await_reply(self, *, after_id: int, timeout_seconds: float = 20.0) -> str:
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self._base}/messages",
                    params={
                        "workspaceId": self._workspace_id,
                        "chatId": self._chat,
                        "limit": 10,
                        "afterId": after_id,
                    },
                    headers=self._headers(),
                )
                response.raise_for_status()
                messages = response.json()
            inbound = [m for m in messages if not m.get("isOutgoing")]
            if inbound:
                return str(inbound[-1].get("text") or "")
            await asyncio.sleep(self._poll)
        raise TimeoutError(f"botfather_reply_timeout after_id={after_id}")


class BotApiPolisher(Protocol):
    async def polish(
        self,
        *,
        token: str,
        name: str,
        description: str,
        short_description: str,
    ) -> None: ...


class HermesGatewayBotApiPolisher:
    """Polish the freshly minted bot through the Bot API (PTB via Hermes gateway)."""

    async def polish(
        self,
        *,
        token: str,
        name: str,
        description: str,
        short_description: str,
    ) -> None:
        from gateway.platforms import telegram as hermes_telegram

        if not hermes_telegram.check_telegram_requirements():
            raise RuntimeError("hermes_telegram_gateway_unavailable")
        bot = hermes_telegram.Bot(token)
        await bot.set_my_name(name=name[:64])
        await bot.set_my_description(description=description[:512])
        await bot.set_my_short_description(short_description=short_description[:120])


@dataclass
class ProvisionResult:
    bot_username: str
    token_stored: bool
    owner_chat_bound: bool
    polished: bool
    pfp_set: bool
    transcript: list[str] = field(default_factory=list)


class BotFatherProvisioner:
    def __init__(
        self,
        *,
        channel: BotFatherChannel,
        polisher: BotApiPolisher,
        bot_channel_factory: Any = None,
    ) -> None:
        self._channel = channel
        self._polisher = polisher
        # factory(username) -> BotFatherChannel aimed at the NEW bot (for /start)
        self._bot_channel_factory = bot_channel_factory

    async def provision(
        self,
        *,
        workspace: Workspace,
        display_name: str | None = None,
        username: str | None = None,
        description: str | None = None,
        short_description: str | None = None,
        pfp_url: str | None = None,
        force: bool = False,
    ) -> ProvisionResult:
        if workspace.control_bot_token and not force:
            raise ValueError("control_bot_already_provisioned")

        transcript: list[str] = []
        name = display_name or f"{workspace.name} — OQIM boshqaruv"
        description = description or (
            f"{workspace.name} uchun OQIM boshqaruv boti. Yangi lidlar, "
            "eskalatsiyalar va agent yaratgan vazifalarni shu yerda olasiz; "
            "Approve/Reject tugmalari bilan tasdiqlaysiz."
        )
        short_description = short_description or "OQIM: lidlar, eskalatsiyalar va tasdiqlashlar."

        token, bot_username = await self._create_bot(
            name=name,
            preferred_username=username or _default_username(workspace.name),
            transcript=transcript,
        )

        workspace.control_bot_token = token
        workspace.control_bot_username = bot_username
        # Token prefix IS the bot's Telegram user id — stored so the persist
        # consumer can refuse to ingest the bot's chat as a customer.
        workspace.control_bot_user_id = int(token.split(":", 1)[0])
        token_stored = True

        polished = False
        try:
            await self._polisher.polish(
                token=token,
                name=name,
                description=description,
                short_description=short_description,
            )
            polished = True
        except Exception as exc:  # bot works without polish; report, don't fail
            logger.warning("control_bot_polish_failed: %s", exc)
            transcript.append(f"[polish failed: {exc}]")

        pfp_set = False
        if pfp_url:
            try:
                await self._set_profile_photo(
                    bot_username=bot_username,
                    pfp_url=pfp_url,
                    transcript=transcript,
                )
                pfp_set = True
            except Exception as exc:
                logger.warning("control_bot_pfp_failed: %s", exc)
                transcript.append(f"[pfp failed: {exc}]")

        # D4 (#451): provisioning never binds an owner. The human owner binds
        # their own chat via the one-time deep-link token (BindTokenService);
        # the userbot must not auto-bind the business account.
        owner_chat_bound = False

        return ProvisionResult(
            bot_username=bot_username,
            token_stored=token_stored,
            owner_chat_bound=owner_chat_bound,
            polished=polished,
            pfp_set=pfp_set,
            transcript=transcript,
        )

    async def _create_bot(
        self,
        *,
        name: str,
        preferred_username: str,
        transcript: list[str],
    ) -> tuple[str, str]:
        reply = await self._exchange("/newbot", transcript)
        if "20 bots" in reply.lower():
            raise RuntimeError("botfather_bot_limit_reached")
        reply = await self._exchange(name, transcript)

        candidates = _username_candidates(preferred_username)
        for candidate in candidates:
            reply = await self._exchange(candidate, transcript)
            match = _TOKEN_RE.search(reply)
            if match:
                return match.group(1), candidate
            lowered = reply.lower()
            if not any(marker in lowered for marker in _USERNAME_TAKEN_MARKERS):
                # Unexpected reply — surface the transcript instead of looping.
                raise RuntimeError(f"botfather_unexpected_reply: {reply[:200]}")
        raise RuntimeError("botfather_all_usernames_taken")

    async def _set_profile_photo(
        self,
        *,
        bot_username: str,
        pfp_url: str,
        transcript: list[str],
    ) -> None:
        reply = await self._exchange("/setuserpic", transcript)
        if "choose a bot" not in reply.lower():
            raise RuntimeError(f"setuserpic_unexpected_reply: {reply[:120]}")
        reply = await self._exchange(f"@{bot_username}", transcript)
        if "photo" not in reply.lower():
            raise RuntimeError(f"setuserpic_no_photo_prompt: {reply[:120]}")
        after_id = await self._channel.last_message_id()
        await self._channel.send("", media_url=pfp_url)
        reply = await self._channel.await_reply(after_id=after_id)
        transcript.append(f"<- {reply[:120]}")
        if "success" not in reply.lower() and "updated" not in reply.lower():
            raise RuntimeError(f"setuserpic_not_confirmed: {reply[:120]}")

    async def _exchange(self, text: str, transcript: list[str]) -> str:
        after_id = await self._channel.last_message_id()
        await self._channel.send(text)
        transcript.append(f"-> {text}")
        reply = await self._channel.await_reply(after_id=after_id)
        transcript.append(f"<- {reply[:200]}")
        return reply


def build_workspace_provisioner(*, workspace_id: int) -> BotFatherProvisioner:
    """Production wiring for the owner-bot provisioning feature.

    Shared by the workspace API route, onboarding flows, and ops tooling —
    any caller with a workspace gets the same BotFather conversation lane
    (through that workspace's connected userbot) and Bot API polisher.
    """
    from app.core.config import get_settings

    settings = get_settings()

    def channel_for(chat: str) -> SidecarBotFatherChannel:
        return SidecarBotFatherChannel(
            sidecar_url=settings.sidecar_url,
            sidecar_api_key=getattr(settings, "sidecar_api_key", "") or "",
            workspace_id=workspace_id,
            chat=chat,
        )

    return BotFatherProvisioner(
        channel=channel_for(BOTFATHER),
        polisher=HermesGatewayBotApiPolisher(),
        bot_channel_factory=channel_for,
    )


def _default_username(workspace_name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "", workspace_name.lower().replace(" ", "_"))
    slug = re.sub(r"_+", "_", slug).strip("_") or "workspace"
    base = f"oqim_{slug}"[:26].rstrip("_")
    return f"{base}_bot"


def _username_candidates(preferred: str) -> list[str]:
    preferred = preferred if preferred.endswith("bot") else f"{preferred}_bot"
    stem = preferred[: -len("_bot")] if preferred.endswith("_bot") else preferred[: -len("bot")]
    suffix = uuid.uuid4().hex[:4]
    return [
        preferred,
        f"{stem}_{suffix}_bot"[:32],
        f"{stem}_{uuid.uuid4().hex[:6]}_bot"[:32],
    ]
