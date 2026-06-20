"""OQIM provisions its own owner control bot by talking to BotFather.

Founder direction (2026-06-10): the platform has the workspace's Telegram
account, so it creates the bot itself — /newbot, name, username, token parse,
Bot API polish, /start + auto-bind. No human steps.
"""

from __future__ import annotations

import pytest

from app.modules.telegram_control_bot.provisioner import (
    BotFatherProvisioner,
    _default_username,
)

pytestmark = pytest.mark.asyncio

TOKEN = "7654321098:AAHfakeFAKEfakeFAKEfakeFAKEfakeFAKE12"

NEWBOT_PROMPT = "Alright, a new bot. How are we going to call it?"
USERNAME_PROMPT = "Good. Now let's choose a username for your bot."
SUCCESS = (
    "Done! Congratulations on your new bot.\n"
    f"Use this token to access the HTTP API:\n{TOKEN}\n"
    "Keep your token secure."
)
TAKEN = "Sorry, this username is already taken. Please try something different."


class _FakeChannel:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.sent: list[str] = []
        self._message_id = 0

    async def last_message_id(self) -> int:
        return self._message_id

    async def send(self, text: str, *, media_url: str | None = None) -> None:
        self._message_id += 1
        self.sent.append(text if media_url is None else f"[media:{media_url}] {text}")

    async def await_reply(self, *, after_id: int, timeout_seconds: float = 20.0) -> str:
        self._message_id += 1
        if not self.replies:
            raise TimeoutError("fake_channel_empty")
        return self.replies.pop(0)


class _FakePolisher:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self._fail = fail

    async def polish(self, **kwargs) -> None:
        if self._fail:
            raise RuntimeError("polish_boom")
        self.calls.append(kwargs)


class _FakeBotChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str, *, media_url: str | None = None) -> None:
        self.sent.append(text)


async def test_provisions_bot_end_to_end(db_session, workspace):
    workspace.telegram_user_id = 777000111
    await db_session.flush()
    channel = _FakeChannel([NEWBOT_PROMPT, USERNAME_PROMPT, SUCCESS])
    polisher = _FakePolisher()
    bot_channel = _FakeBotChannel()
    provisioner = BotFatherProvisioner(
        channel=channel,
        polisher=polisher,
        bot_channel_factory=lambda username: bot_channel,
    )

    result = await provisioner.provision(workspace=workspace)

    assert channel.sent[0] == "/newbot"
    assert result.token_stored is True
    assert workspace.control_bot_token == TOKEN
    assert result.bot_username.startswith("oqim_") and result.bot_username.endswith("bot")
    assert workspace.control_bot_username == result.bot_username
    # polished through the Bot API
    assert polisher.calls and polisher.calls[0]["token"] == TOKEN
    assert result.polished is True
    # D4 (#451): provisioning never auto-binds an owner — the human binds via the
    # one-time deep-link token. No /start is sent from the userbot.
    assert bot_channel.sent == []
    assert workspace.owner_control_chat_id is None
    assert result.owner_chat_bound is False


async def test_retries_username_when_taken(db_session, workspace):
    channel = _FakeChannel([NEWBOT_PROMPT, USERNAME_PROMPT, TAKEN, SUCCESS])
    provisioner = BotFatherProvisioner(channel=channel, polisher=_FakePolisher())

    result = await provisioner.provision(workspace=workspace)

    assert workspace.control_bot_token == TOKEN
    # two username attempts were sent (after /newbot and the display name)
    assert len(channel.sent) == 4
    assert result.bot_username != channel.sent[2] or channel.sent[2] != channel.sent[3]


async def test_refuses_double_provision_without_force(db_session, workspace):
    workspace.control_bot_token = TOKEN
    provisioner = BotFatherProvisioner(
        channel=_FakeChannel([]), polisher=_FakePolisher()
    )

    with pytest.raises(ValueError, match="control_bot_already_provisioned"):
        await provisioner.provision(workspace=workspace)


async def test_polish_failure_does_not_lose_the_token(db_session, workspace):
    channel = _FakeChannel([NEWBOT_PROMPT, USERNAME_PROMPT, SUCCESS])
    provisioner = BotFatherProvisioner(channel=channel, polisher=_FakePolisher(fail=True))

    result = await provisioner.provision(workspace=workspace)

    assert workspace.control_bot_token == TOKEN
    assert result.polished is False
    assert any("polish failed" in line for line in result.transcript)


async def test_default_username_is_telegram_safe():
    username = _default_username("Biznesni tizimlashtirish")
    assert username.startswith("oqim_")
    assert username.endswith("_bot")
    assert len(username) <= 32
    assert username.replace("_", "").isalnum()
