from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent_talking.contracts import (
    TalkActionKind,
    TalkBundle,
)
from app.modules.agent_talking.pacing import compute_pacing_plan


class ChannelRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChannelDeliveryIntent(ChannelRuntimeModel):
    schema_version: Literal["channel_delivery_intent.v1"] = "channel_delivery_intent.v1"
    action_index: int = Field(ge=0)
    kind: Literal["send_text", "send_media", "send_reaction"]
    channel: str = Field(min_length=1, max_length=80)
    conversation_id: int = Field(gt=0)
    text: str | None = Field(default=None, max_length=4000)
    media_ref: str | None = Field(default=None, max_length=512)
    reply_to_message_ref: str | None = Field(default=None, max_length=255)
    client_idempotency_key: str = Field(min_length=1, max_length=512)
    typing_ms: int = Field(default=0, ge=0)
    delay_after_ms: int = Field(default=0, ge=0)
    delivery_policy: dict[str, Any] = Field(default_factory=dict)


class ChannelDeliveryPlan(ChannelRuntimeModel):
    schema_version: Literal["channel_delivery_plan.v1"] = "channel_delivery_plan.v1"
    workspace_id: int = Field(gt=0)
    agent_id: int = Field(gt=0)
    hermes_run_id: str = Field(min_length=1, max_length=128)
    conversation_id: int = Field(gt=0)
    channel: str = Field(min_length=1, max_length=80)
    source_bundle_key: str = Field(min_length=1, max_length=512)
    intents: list[ChannelDeliveryIntent] = Field(default_factory=list)


class ChannelRuntimeCore:
    """Channel-owned planning boundary for policy-approved delivery work."""

    def plan_talk_bundle_delivery(self, bundle: TalkBundle) -> ChannelDeliveryPlan:
        if bundle.conversation_id is None:
            raise ValueError("talk bundle delivery requires conversation_id")

        bundle_key = _bundle_key(bundle)
        channel = _bundle_channel(bundle)
        pacing = compute_pacing_plan(bundle)
        intents: list[ChannelDeliveryIntent] = []
        for idx, action in enumerate(bundle.actions):
            if action.kind is TalkActionKind.SEND_REACTION:
                # Reactions are instant: planner-owned idempotency, no typing,
                # no pacing delay; target falls back to the trigger message.
                intents.append(
                    ChannelDeliveryIntent(
                        action_index=idx,
                        kind="send_reaction",
                        channel=channel,
                        conversation_id=bundle.conversation_id,
                        reply_to_message_ref=(
                            action.target_message_ref or bundle.trigger_ref
                        ),
                        client_idempotency_key=action.idempotency_key
                        or f"{bundle_key}:{idx}",
                        typing_ms=0,
                        delay_after_ms=0,
                        delivery_policy={
                            "reply_to_message": False,
                            "typing_indicator": "off",
                            "pacing_profile": bundle.talking_policy_snapshot.pacing_profile,
                        },
                    )
                )
                continue
            if action.kind not in {
                TalkActionKind.SEND_MSG,
                TalkActionKind.REPLY_TO_MSG,
                TalkActionKind.SEND_MEDIA,
            }:
                continue
            plan = pacing[idx] if idx < len(pacing) else None
            delivery_kind: Literal["send_text", "send_media"] = (
                "send_media" if action.kind is TalkActionKind.SEND_MEDIA else "send_text"
            )
            intents.append(
                ChannelDeliveryIntent(
                    action_index=idx,
                    kind=delivery_kind,
                    channel=channel,
                    conversation_id=bundle.conversation_id,
                    text=(action.text or "").strip() or None,
                    media_ref=action.media_ref,
                    reply_to_message_ref=(
                        action.target_message_ref
                        if action.kind is TalkActionKind.REPLY_TO_MSG
                        else None
                    ),
                    client_idempotency_key=action.idempotency_key or f"{bundle_key}:{idx}",
                    typing_ms=plan.typing_ms if plan is not None else 0,
                    delay_after_ms=plan.delay_after_ms if plan is not None else 0,
                    delivery_policy={
                        "reply_to_message": action.kind is TalkActionKind.REPLY_TO_MSG,
                        "typing_indicator": bundle.talking_policy_snapshot.typing_indicator,
                        "pacing_profile": bundle.talking_policy_snapshot.pacing_profile,
                    },
                )
            )
        return ChannelDeliveryPlan(
            workspace_id=bundle.workspace_id,
            agent_id=bundle.agent_id,
            hermes_run_id=bundle.hermes_run_id,
            conversation_id=bundle.conversation_id,
            channel=channel,
            source_bundle_key=bundle_key,
            intents=intents,
        )


def _bundle_key(bundle: TalkBundle) -> str:
    return f"talk_bundle:{bundle.workspace_id}:{bundle.agent_id}:{bundle.hermes_run_id}"


def _bundle_channel(bundle: TalkBundle) -> str:
    value = str(bundle.channel_account_id or "").strip().lower()
    if value.startswith("instagram"):
        return "instagram_dm"
    if value.startswith("whatsapp"):
        return "whatsapp_dm"
    return "telegram_dm"
