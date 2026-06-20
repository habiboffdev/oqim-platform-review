"""Comment->DM: owner boundary + agent judgment + private reply + dedup + cap."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message
from app.models.trigger import Trigger
from app.modules.channel_runtime.instagram_comment_dm import (
    InstagramCommentDmService,
)

pytestmark = pytest.mark.asyncio

PAGE_ID = "17841400000000000"


@pytest.fixture
async def ig_workspace(db_session, workspace):
    workspace.instagram_connected = True
    workspace.instagram_page_id = PAGE_ID
    workspace.instagram_access_token = "IGAA-test-token"
    await db_session.flush()
    return workspace


async def _enable_trigger(db_session, workspace, agent, *, post_ids=None, all_posts=False):
    """Create an active instagram_comment_received trigger.

    Required non-null fields without DB defaults:
      - owner_agent_id: FK to agents.id (must be a real agent row)
      - action_proposal_type: String(120) nullable=False
      - idempotency_key: String(120) nullable=False
    """
    trigger = Trigger(
        workspace_id=workspace.id,
        owner_agent_id=agent.id,
        event_source="instagram_comment_received",
        action_proposal_type="instagram.comment_dm",
        idempotency_key=f"igcmt:{workspace.id}:{all_posts}:{','.join(sorted(post_ids or []))}",
        matching_scope={
            "all_posts": all_posts,
            "post_ids": post_ids or [],
            "goal": "Kurs haqida qiziqqanlarni DMga taklif qilish",
        },
        active=True,
    )
    db_session.add(trigger)
    await db_session.flush()
    return trigger


def _comment_value(comment_id="c-100", media_id="media-1", text="Narxi qancha?", author_id="999000111", username="ali_uz"):
    return {
        "id": comment_id,
        "text": text,
        "media": {"id": media_id},
        "from": {"id": author_id, "username": username},
    }


def _graph_factory(message_id="mid.pr1"):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"recipient_id": "999000111", "message_id": message_id}
    response.raise_for_status.return_value = None
    post_mock = AsyncMock(return_value=response)

    @asynccontextmanager
    async def _client(*args, **kwargs):
        client = MagicMock()
        client.post = post_mock
        yield client

    return _client, post_mock


def _failing_graph_factory():
    post_mock = AsyncMock(side_effect=httpx.ConnectError("boom"))

    @asynccontextmanager
    async def _client(*args, **kwargs):
        client = MagicMock()
        client.post = post_mock
        yield client

    return _client, post_mock


_DECISION_DM = {"should_dm": True, "reason": "price question", "opening_message": "Salom! Savolingizga DMda javob beraman."}
_DECISION_SKIP = {"should_dm": False, "reason": "praise only", "opening_message": ""}


async def test_no_trigger_means_no_dm_and_no_llm(db_session, ig_workspace):
    service = InstagramCommentDmService(db_session)
    with patch(
        "app.modules.channel_runtime.instagram_comment_dm.generate_structured_json",
        new=AsyncMock(return_value=_DECISION_DM),
    ) as llm:
        result = await service.handle_comment(workspace=ig_workspace, value=_comment_value())
    assert result.sent is False
    assert result.skipped_reason == "no_active_trigger"
    assert not llm.called


async def test_enabled_post_judged_and_private_reply_sent(db_session, ig_workspace, agent):
    await _enable_trigger(db_session, ig_workspace, agent, post_ids=["media-1"])
    factory, post_mock = _graph_factory()
    service = InstagramCommentDmService(db_session, http_client_factory=factory)
    with patch(
        "app.modules.channel_runtime.instagram_comment_dm.generate_structured_json",
        new=AsyncMock(return_value=_DECISION_DM),
    ):
        result = await service.handle_comment(workspace=ig_workspace, value=_comment_value())

    assert result.sent is True
    assert post_mock.call_args.kwargs["json"]["recipient"] == {"comment_id": "c-100"}

    # The reply is persisted as a seller message in an instagram_dm conversation
    # whose Customer is keyed on the COMMENTER's IG user id (so the inbound DM
    # pipeline finds the same customer/conversation when the user replies).
    conversation = (
        await db_session.execute(
            select(Conversation).where(
                Conversation.workspace_id == ig_workspace.id,
                Conversation.external_chat_id == "999000111",
            )
        )
    ).scalar_one()
    assert conversation.channel == "instagram_dm"
    customer = (
        await db_session.execute(
            select(Customer).where(Customer.id == conversation.customer_id)
        )
    ).scalar_one()
    assert customer.external_id == "999000111"
    message = (
        await db_session.execute(
            select(Message).where(Message.conversation_id == conversation.id)
        )
    ).scalars().one()
    assert message.client_message_uuid == "igpr:c-100"
    assert message.delivery_state == "confirmed"


async def test_two_commenters_get_separate_conversations(db_session, ig_workspace, agent):
    await _enable_trigger(db_session, ig_workspace, agent, all_posts=True)
    factory, _ = _graph_factory()
    service = InstagramCommentDmService(db_session, http_client_factory=factory)
    with patch(
        "app.modules.channel_runtime.instagram_comment_dm.generate_structured_json",
        new=AsyncMock(return_value=_DECISION_DM),
    ):
        first = await service.handle_comment(
            workspace=ig_workspace,
            value=_comment_value(comment_id="c-1", author_id="111", username="ali"),
        )
        second = await service.handle_comment(
            workspace=ig_workspace,
            value=_comment_value(comment_id="c-2", author_id="222", username="vali"),
        )
    assert first.sent is True
    assert second.sent is True

    conversations = (
        await db_session.execute(
            select(Conversation).where(
                Conversation.workspace_id == ig_workspace.id,
                Conversation.channel == "instagram_dm",
            )
        )
    ).scalars().all()
    assert {c.external_chat_id for c in conversations} == {"111", "222"}
    assert len(conversations) == 2

    customers = (
        await db_session.execute(
            select(Customer).where(
                Customer.id.in_([c.customer_id for c in conversations])
            )
        )
    ).scalars().all()
    assert {c.external_id for c in customers} == {"111", "222"}
    assert len(customers) == 2


async def test_agent_judgment_can_decline(db_session, ig_workspace, agent):
    await _enable_trigger(db_session, ig_workspace, agent, post_ids=["media-1"])
    factory, post_mock = _graph_factory()
    service = InstagramCommentDmService(db_session, http_client_factory=factory)
    with patch(
        "app.modules.channel_runtime.instagram_comment_dm.generate_structured_json",
        new=AsyncMock(return_value=_DECISION_SKIP),
    ):
        result = await service.handle_comment(
            workspace=ig_workspace, value=_comment_value(text="Zo'r!")
        )
    assert result.sent is False
    assert result.skipped_reason == "agent_declined"
    assert not post_mock.called


async def test_judgment_failure_declines_safely(db_session, ig_workspace, agent):
    await _enable_trigger(db_session, ig_workspace, agent, post_ids=["media-1"])
    factory, post_mock = _graph_factory()
    service = InstagramCommentDmService(db_session, http_client_factory=factory)
    with patch(
        "app.modules.channel_runtime.instagram_comment_dm.generate_structured_json",
        new=AsyncMock(side_effect=RuntimeError("llm exploded")),
    ):
        result = await service.handle_comment(workspace=ig_workspace, value=_comment_value())
    assert result.sent is False
    assert result.skipped_reason == "judgment_failed"
    assert not post_mock.called


async def test_duplicate_comment_is_idempotent(db_session, ig_workspace, agent):
    await _enable_trigger(db_session, ig_workspace, agent, post_ids=["media-1"])
    factory, post_mock = _graph_factory()
    service = InstagramCommentDmService(db_session, http_client_factory=factory)
    with patch(
        "app.modules.channel_runtime.instagram_comment_dm.generate_structured_json",
        new=AsyncMock(return_value=_DECISION_DM),
    ):
        first = await service.handle_comment(workspace=ig_workspace, value=_comment_value())
        second = await service.handle_comment(workspace=ig_workspace, value=_comment_value())
    assert first.sent is True
    assert second.sent is False
    assert second.skipped_reason == "duplicate_comment"
    assert post_mock.call_count == 1


async def test_send_failure_is_retryable(db_session, ig_workspace, agent):
    await _enable_trigger(db_session, ig_workspace, agent, post_ids=["media-1"])
    failing_factory, _ = _failing_graph_factory()
    failing_service = InstagramCommentDmService(
        db_session, http_client_factory=failing_factory
    )
    with patch(
        "app.modules.channel_runtime.instagram_comment_dm.generate_structured_json",
        new=AsyncMock(return_value=_DECISION_DM),
    ):
        first = await failing_service.handle_comment(
            workspace=ig_workspace, value=_comment_value()
        )
    assert first.sent is False
    assert first.skipped_reason == "send_failed"

    failed_message = (
        await db_session.execute(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.workspace_id == ig_workspace.id,
                Message.client_message_uuid == "igpr:c-100",
            )
        )
    ).scalars().one()
    assert failed_message.delivery_state == "failed"

    # Meta redelivery: the failed row is REUSED (no second row), then confirmed.
    working_factory, post_mock = _graph_factory()
    retry_service = InstagramCommentDmService(
        db_session, http_client_factory=working_factory
    )
    with patch(
        "app.modules.channel_runtime.instagram_comment_dm.generate_structured_json",
        new=AsyncMock(return_value=_DECISION_DM),
    ):
        second = await retry_service.handle_comment(
            workspace=ig_workspace, value=_comment_value()
        )
    assert second.sent is True
    assert post_mock.call_count == 1

    messages = (
        await db_session.execute(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.workspace_id == ig_workspace.id,
                Message.client_message_uuid == "igpr:c-100",
            )
        )
    ).scalars().all()
    assert len(messages) == 1
    assert messages[0].delivery_state == "confirmed"


async def test_own_comment_is_skipped(db_session, ig_workspace, agent):
    await _enable_trigger(db_session, ig_workspace, agent, all_posts=True)
    service = InstagramCommentDmService(db_session)
    value = _comment_value()
    value["from"] = {"id": PAGE_ID, "username": "ownshop"}
    result = await service.handle_comment(workspace=ig_workspace, value=value)
    assert result.sent is False
    assert result.skipped_reason == "own_comment"


async def test_own_comment_under_secondary_id_is_skipped(db_session, ig_workspace, agent):
    """The business's own comment may carry its `id` (not the user_id stored as
    page_id). Self-comments must still be recognised, or the bot DMs itself."""
    ig_workspace.instagram_account_id = "27100000000000000"
    await db_session.flush()
    await _enable_trigger(db_session, ig_workspace, agent, all_posts=True)
    service = InstagramCommentDmService(db_session)
    value = _comment_value()
    value["from"] = {"id": "27100000000000000", "username": "ownshop"}
    result = await service.handle_comment(workspace=ig_workspace, value=value)
    assert result.sent is False
    assert result.skipped_reason == "own_comment"


async def test_malformed_payload_skipped(db_session, ig_workspace):
    service = InstagramCommentDmService(db_session)
    result = await service.handle_comment(workspace=ig_workspace, value={})
    assert result.sent is False
    assert result.skipped_reason == "malformed_payload"


async def test_hourly_cap_blocks_and_surfaces(db_session, ig_workspace, agent):
    await _enable_trigger(db_session, ig_workspace, agent, all_posts=True)
    factory, _ = _graph_factory()
    service = InstagramCommentDmService(
        db_session, http_client_factory=factory, hourly_cap=1
    )
    with patch(
        "app.modules.channel_runtime.instagram_comment_dm.generate_structured_json",
        new=AsyncMock(return_value=_DECISION_DM),
    ):
        first = await service.handle_comment(
            workspace=ig_workspace, value=_comment_value(comment_id="c-1")
        )
        second = await service.handle_comment(
            workspace=ig_workspace, value=_comment_value(comment_id="c-2")
        )
    assert first.sent is True
    assert second.sent is False
    assert second.skipped_reason == "hourly_cap_reached"
