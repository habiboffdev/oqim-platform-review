from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.commerce_catalog import CatalogMediaRecord
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.conversation import Conversation
from app.models.message import Message, SenderType
from app.models.workspace import Workspace
from app.modules.agent_talking.contracts import (
    TalkAction,
    TalkActionKind,
    TalkBundle,
    TalkingPolicy,
)
from app.modules.agent_talking.service import TalkBundleService
from app.modules.channel_runtime.source import ChannelRuntimeCore
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService
from app.services.channel_adapter_contract import (
    ChannelCapabilities,
    ChannelDeliveryStatus,
    ChannelSendResult,
)
from app.services.delivery import DeliveryResult


@dataclass
class FakeDelivery:
    calls: list[dict[str, Any]] = field(default_factory=list)
    media_calls: list[dict[str, Any]] = field(default_factory=list)

    async def deliver_message(self, conversation_id: int, text: str, **kwargs: Any) -> DeliveryResult:
        self.calls.append({"conversation_id": conversation_id, "text": text, **kwargs})
        return DeliveryResult(success=True, external_message_id=f"ext:{len(self.calls)}", state="confirmed")

    async def deliver_media(self, conversation_id: int, media, **kwargs: Any) -> DeliveryResult:
        self.media_calls.append({"conversation_id": conversation_id, "media": media, **kwargs})
        return DeliveryResult(
            success=True,
            external_message_id=f"media-ext:{len(self.media_calls)}",
            state="confirmed",
        )


@dataclass
class FakeTelegramAdapter:
    reactions: list[dict[str, Any]] = field(default_factory=list)
    channel: str = "telegram_dm"

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(send_reaction=True)

    async def send_reaction(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        message_id: str,
        reaction: str,
        idempotency_key: str,
    ) -> ChannelSendResult:
        self.reactions.append(
            {
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "reaction": reaction,
                "idempotency_key": idempotency_key,
            }
        )
        return ChannelSendResult(
            external_message_id=message_id,
            status=ChannelDeliveryStatus(status="sent", external_message_id=message_id),
        )


@dataclass
class SequencedDelivery:
    results: list[DeliveryResult]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def deliver_message(self, conversation_id: int, text: str, **kwargs: Any) -> DeliveryResult:
        self.calls.append({"conversation_id": conversation_id, "text": text, **kwargs})
        return self.results.pop(0)


def _bundle(workspace: Workspace, agent: Agent, conversation: Conversation) -> TalkBundle:
    return TalkBundle(
        workspace_id=workspace.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:bundle",
        conversation_id=conversation.id,
        channel_account_id="telegram:1",
        actions=[
            TalkAction(kind=TalkActionKind.SEND_MSG, text="Salom", requires_scope="telegram.send_message"),
            TalkAction(
                kind=TalkActionKind.SEND_MSG,
                text="Starter coins 5 ta — 40 000 UZS",
                requires_scope="telegram.send_message",
            ),
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
        confidence=0.91,
    )


async def test_draft_bundle_creates_one_action_proposal(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    service = TalkBundleService(db_session)
    proposal = await service.propose_bundle(
        bundle=_bundle(workspace, agent, conversation),
        reason="draft: owner approval required",
    )

    stored = await db_session.scalar(
        select(CommercialActionProposalRecord).where(
            CommercialActionProposalRecord.workspace_id == workspace.id,
            CommercialActionProposalRecord.proposal_id == proposal.proposal_id,
        )
    )

    assert stored is not None
    assert stored.action_type == "send_reply_bundle"
    assert stored.requires_approval is True
    assert len(stored.payload["talk_bundle"]["actions"]) == 2


async def test_execute_bundle_sends_bubbles_sequentially_with_idempotency(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope="telegram.send_message"),
    )
    delivery = FakeDelivery()
    service = TalkBundleService(db_session, delivery=delivery)

    result = await service.execute_bundle(
        bundle=_bundle(workspace, agent, conversation),
        correlation_id="test-bundle-execute",
    )

    assert result.status == "executed"
    assert result.delivery_state == "confirmed"
    assert result.sent_count == 2
    assert [call["text"] for call in delivery.calls] == [
        "Salom",
        "Starter coins 5 ta — 40 000 UZS",
    ]
    assert delivery.calls[0]["client_idempotency_key"].endswith(":0")
    assert delivery.calls[1]["client_idempotency_key"].endswith(":1")
    assert delivery.calls[0]["reply_to_message_id"] is None
    assert delivery.calls[1]["reply_to_message_id"] is None
    assert delivery.calls[0]["typing_indicator"] is True
    assert delivery.calls[1]["typing_indicator"] is True
    assert 0.2 <= delivery.calls[0]["delay_override_seconds"] <= 0.9
    assert delivery.calls[1]["delay_override_seconds"] >= delivery.calls[0]["delay_override_seconds"]
    assert delivery.calls[0]["online_tail_seconds"] == 0.0
    assert delivery.calls[1]["online_tail_seconds"] == 1.5


async def test_execute_bundle_supports_explicit_reply_to_message_action(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope="telegram.send_message"),
    )
    delivery = FakeDelivery()
    service = TalkBundleService(db_session, delivery=delivery)
    bundle = TalkBundle(
        workspace_id=workspace.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:reply-to-bundle",
        conversation_id=conversation.id,
        actions=[
            TalkAction(
                kind=TalkActionKind.REPLY_TO_MSG,
                text="Salom",
                target_message_ref="1440",
                requires_scope="telegram.send_message",
            )
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )

    result = await service.execute_bundle(
        bundle=bundle,
        correlation_id="test-bundle-reply-to",
    )

    assert result.status == "executed"
    assert delivery.calls[0]["text"] == "Salom"
    assert delivery.calls[0]["reply_to_message_id"] == 1440


async def test_execute_bundle_sends_reaction_through_telegram_runtime(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope="telegram.send_reaction"),
    )
    target = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="rahmat",
        telegram_message_id=1440,
        external_message_id="1440",
    )
    db_session.add(target)
    await db_session.flush()
    adapter = FakeTelegramAdapter()
    bundle = TalkBundle(
        workspace_id=workspace.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:reaction-bundle",
        trigger_ref=f"message:{target.id}",
        conversation_id=conversation.id,
        actions=[
            TalkAction(
                kind=TalkActionKind.SEND_REACTION,
                reaction="👍",
                requires_scope="telegram.send_reaction",
            )
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )

    result = await TalkBundleService(
        db_session,
        adapter=adapter,
        sleep=lambda _: None,
    ).execute_bundle(
        bundle=bundle,
        correlation_id="test-bundle-reaction",
    )

    assert result.status == "executed"
    assert result.sent_count == 1
    assert result.bubbles[0].action_kind == TalkActionKind.SEND_REACTION
    assert result.bubbles[0].reply_to_message_ref == f"message:{target.id}"
    assert adapter.reactions == [
        {
            "workspace_id": workspace.id,
            "conversation_id": str(conversation.external_chat_id or conversation.telegram_chat_id),
            "message_id": "1440",
            "reaction": "👍",
            "idempotency_key": result.bubbles[0].idempotency_key,
        }
    ]


async def test_execute_bundle_sends_media_bubble_through_telegram_runtime(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope="telegram.send_message"),
    )
    db_session.add(
        CatalogMediaRecord(
            workspace_id=workspace.id,
            media_ref="catalog_media:satstation:exam-engine",
            product_ref="catalog_product:satstation",
            media_kind="image",
            url="https://cdn.example.com/satstation/exam-engine.png",
            caption="SATStation exam engine",
            ocr_text="",
            visual_summary="Digital SAT exam engine",
            authority_state="approved",
            source_refs=["source:satstation"],
            source_fact_ids=["fact:satstation-media"],
            metadata_={"content_type": "image/png"},
        )
    )
    await db_session.flush()
    delivery = FakeDelivery()
    service = TalkBundleService(db_session, delivery=delivery, sleep=lambda _: None)
    bundle = TalkBundle(
        workspace_id=workspace.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:media-bundle",
        conversation_id=conversation.id,
        actions=[
            TalkAction(
                kind=TalkActionKind.SEND_MEDIA,
                text="Mana rasmi",
                media_ref="catalog_media:satstation:exam-engine",
                requires_scope="telegram.send_message",
            )
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )

    result = await service.execute_bundle(
        bundle=bundle,
        correlation_id="test-bundle-send-media",
    )

    assert result.status == "executed"
    assert result.delivery_state == "confirmed"
    assert result.sent_count == 1
    assert result.bubbles[0].action_kind == TalkActionKind.SEND_MEDIA
    assert result.bubbles[0].external_message_id == "media-ext:1"
    assert delivery.calls == []
    assert delivery.media_calls[0]["media"].url == "https://cdn.example.com/satstation/exam-engine.png"
    assert delivery.media_calls[0]["caption"] == "Mana rasmi"
    assert delivery.media_calls[0]["client_idempotency_key"].endswith(":0")
    assert delivery.media_calls[0]["typing_indicator"] is True
    assert 0.2 <= delivery.media_calls[0]["delay_override_seconds"] <= 0.9
    assert delivery.media_calls[0]["online_tail_seconds"] == 1.5


async def test_channel_runtime_plans_multi_bubble_delivery_intents(
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    bundle = _bundle(workspace, agent, conversation).model_copy(
        update={
            "actions": [
                TalkAction(
                    kind=TalkActionKind.REPLY_TO_MSG,
                    text="Salom",
                    target_message_ref="message:1440",
                    requires_scope="telegram.send_message",
                ),
                TalkAction(
                    kind=TalkActionKind.SEND_MSG,
                    text="Starter coins 5 ta — 40 000 UZS",
                    requires_scope="telegram.send_message",
                    idempotency_key="owner-supplied-bubble-key",
                ),
            ]
        }
    )

    plan = ChannelRuntimeCore().plan_talk_bundle_delivery(bundle)

    assert plan.workspace_id == workspace.id
    assert plan.conversation_id == conversation.id
    assert plan.channel == "telegram_dm"
    assert plan.source_bundle_key == f"talk_bundle:{workspace.id}:{agent.id}:hermes_run:bundle"
    assert [intent.action_index for intent in plan.intents] == [0, 1]
    assert [intent.kind for intent in plan.intents] == ["send_text", "send_text"]
    assert [intent.text for intent in plan.intents] == [
        "Salom",
        "Starter coins 5 ta — 40 000 UZS",
    ]
    assert plan.intents[0].reply_to_message_ref == "message:1440"
    assert plan.intents[0].client_idempotency_key.endswith(":0")
    assert plan.intents[1].client_idempotency_key == "owner-supplied-bubble-key"
    assert all(intent.typing_ms >= 0 for intent in plan.intents)
    assert plan.intents[0].delivery_policy["reply_to_message"] is True


async def test_channel_runtime_plans_reaction_intent_immediately_no_typing(
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    bundle = _bundle(workspace, agent, conversation).model_copy(
        update={
            "trigger_ref": "message:1440",
            "actions": [
                TalkAction(
                    kind=TalkActionKind.SEND_REACTION,
                    reaction="👍",
                    requires_scope="telegram.send_reaction",
                ),
                TalkAction(
                    kind=TalkActionKind.SEND_MSG,
                    text="Rahmat!",
                    requires_scope="telegram.send_message",
                ),
            ]
        }
    )

    plan = ChannelRuntimeCore().plan_talk_bundle_delivery(bundle)

    assert [intent.kind for intent in plan.intents] == ["send_reaction", "send_text"]
    reaction_intent = plan.intents[0]
    assert reaction_intent.action_index == 0
    # Reactions are instant: no typing, no pacing delay.
    assert reaction_intent.typing_ms == 0
    assert reaction_intent.delay_after_ms == 0
    # Falls back to the trigger message when the model omits a target.
    assert reaction_intent.reply_to_message_ref == "message:1440"
    assert reaction_intent.client_idempotency_key.endswith(":0")


@dataclass
class RaisingReactionAdapter:
    """Adapter whose send_reaction raises an UNEXPECTED error (not a known
    transport failure). The executor must isolate it per-action (#418)."""

    channel: str = "telegram_dm"

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(send_reaction=True)

    async def send_reaction(self, **_: Any) -> ChannelSendResult:
        raise RuntimeError("unexpected reaction failure")


async def test_execute_bundle_isolates_raising_action_after_text_delivered(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    """A reaction that raises must NOT abort the bundle after the text bubble
    already delivered: the text stays sent, the reaction becomes a failed
    bubble, and no exception escapes execute_bundle (#418)."""
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope="telegram.send_message"),
    )
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope="telegram.send_reaction"),
    )
    target = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="998901234567",
        telegram_message_id=2440,
        external_message_id="2440",
    )
    db_session.add(target)
    await db_session.flush()
    delivery = FakeDelivery()
    bundle = TalkBundle(
        workspace_id=workspace.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:reaction-raises",
        trigger_ref=f"message:{target.id}",
        conversation_id=conversation.id,
        actions=[
            TalkAction(
                kind=TalkActionKind.REPLY_TO_MSG,
                text="Raqamingizni oldim, rahmat.",
                target_message_ref=f"message:{target.id}",
                requires_scope="telegram.send_message",
            ),
            TalkAction(
                kind=TalkActionKind.SEND_REACTION,
                reaction="👍",
                requires_scope="telegram.send_reaction",
            ),
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )

    result = await TalkBundleService(
        db_session,
        delivery=delivery,
        adapter=RaisingReactionAdapter(),
        sleep=lambda _: None,
    ).execute_bundle(
        bundle=bundle,
        correlation_id="test-reaction-raises",
    )

    assert result.status == "partial"
    assert result.sent_count == 1
    assert result.failed_count == 1
    assert [bubble.status for bubble in result.bubbles] == ["executed", "failed"]
    assert result.bubbles[1].action_kind == TalkActionKind.SEND_REACTION
    assert result.bubbles[1].reason_code == "action_execution_error"
    assert len(delivery.calls) == 1


async def test_execute_bundle_reports_partial_delivery_state(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope="telegram.send_message"),
    )
    delivery = SequencedDelivery(
        results=[
            DeliveryResult(success=True, external_message_id="ext:sent", state="confirmed"),
            DeliveryResult(success=False, error="sidecar timeout", state="unknown"),
        ],
    )

    result = await TalkBundleService(db_session, delivery=delivery, sleep=lambda _: None).execute_bundle(
        bundle=_bundle(workspace, agent, conversation),
        correlation_id="test-partial-bundle",
    )

    assert result.status == "partial"
    assert result.delivery_state == "partially_sent"
    assert result.sent_count == 1
    assert result.unknown_count == 1
    assert [bubble.status for bubble in result.bubbles] == ["executed", "unknown"]
    assert result.bubbles[0].external_message_id == "ext:sent"
    assert result.bubbles[1].reason_code == "delivery_not_confirmed"


async def test_execute_bundle_blocks_empty_conversation_without_delivery(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    delivery = FakeDelivery()
    bundle = TalkBundle(
        workspace_id=workspace.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:no-conversation",
        conversation_id=None,
        actions=[
            TalkAction(kind=TalkActionKind.SEND_MSG, text="Salom", requires_scope="telegram.send_message")
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )

    result = await TalkBundleService(db_session, delivery=delivery).execute_bundle(
        bundle=bundle,
        correlation_id="test-missing-conversation",
    )

    assert result.status == "blocked"
    assert result.delivery_state == "blocked"
    assert result.reason == "missing_conversation_id"
    assert delivery.calls == []
