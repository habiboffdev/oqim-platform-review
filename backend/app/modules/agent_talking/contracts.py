from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentTalkingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TalkingMode(StrEnum):
    REPLY = "reply"
    DRAFT = "draft"
    SILENT = "silent"
    OWNER_ONLY = "owner_only"
    BROADCAST = "broadcast"
    SCANNER = "scanner"


class TalkActionKind(StrEnum):
    SEND_MSG = "send_msg"
    SEND_MEDIA = "send_media"
    SEND_REACTION = "send_reaction"
    REPLY_TO_MSG = "reply_to_msg"
    DELETE_MESSAGE = "delete_message"
    SEND_STICKER = "send_sticker"


class TalkingPolicy(AgentTalkingModel):
    schema_version: Literal["talking_policy.v1"] = "talking_policy.v1"
    mode: TalkingMode
    allowed_channel_kinds: list[str] = Field(default_factory=lambda: ["telegram_dm"])
    allowed_chat_scope: list[str] = Field(default_factory=list)
    allowed_user_scope: list[str] = Field(default_factory=list)
    max_bubbles_per_turn: int = Field(default=3, ge=1, le=8)
    max_chars_per_bubble: int = Field(default=700, ge=1, le=4000)
    allow_media: bool = True
    allow_reaction: bool = False
    allow_delete: bool = False
    allow_reply_to_message: bool = True
    allow_sticker: bool = False
    pacing_profile: Literal["none", "fast", "human", "slow"] = "human"
    typing_indicator: Literal["off", "auto"] = "auto"
    emoji_usage: Literal["low", "medium", "high"] = "medium"
    draft_bundle: bool = True
    requires_owner_approval: bool = False

    @classmethod
    def seller_default(cls) -> TalkingPolicy:
        return cls(
            mode=TalkingMode.REPLY,
            max_chars_per_bubble=400,  # warm seller bubbles, not terse one-liners (2026-06-11)
            allow_media=True,
            allow_reaction=True,
            allow_delete=False,
            allow_reply_to_message=True,
            # "human" (was "fast", 2026-06-10): fast capped typing at 0.9s so
            # a long bubble materialized instantly after a short one --
            # inverted vs human rhythm (typing time must grow with length)
            pacing_profile="human",
            typing_indicator="auto",
        )

    @classmethod
    def for_agent(
        cls,
        *,
        max_bubbles: int | None = None,
        max_chars: int | None = None,
        allow_reaction: bool | None = None,
        pacing: str | None = None,
        emoji_usage: str | None = None,
    ) -> TalkingPolicy:
        """Seller defaults with optional owner-tunable overrides.

        Overrides come from the agent's ``channel_config["talking"]`` JSON, so
        the owner — not a code constant — controls cadence. Unknown/unset keys
        keep the defaults.
        """
        base = cls.seller_default()
        updates = {
            key: value
            for key, value in {
                "max_bubbles_per_turn": max_bubbles,
                "max_chars_per_bubble": max_chars,
                "allow_reaction": allow_reaction,
                "pacing_profile": pacing,
                "emoji_usage": emoji_usage,
            }.items()
            if value is not None
        }
        if not updates:
            return base
        # Re-validate so bad owner input (e.g. emoji_usage="extreme") fails loudly.
        return cls.model_validate({**base.model_dump(), **updates})

    @classmethod
    def draft_default(cls) -> TalkingPolicy:
        return cls(mode=TalkingMode.DRAFT, requires_owner_approval=True)


class TalkAction(AgentTalkingModel):
    schema_version: Literal["talk_action.v1"] = "talk_action.v1"
    kind: TalkActionKind
    text: str | None = Field(default=None, max_length=4000)
    media_ref: str | None = Field(default=None, max_length=512)
    target_message_ref: str | None = Field(default=None, max_length=255)
    reaction: str | None = Field(default=None, max_length=40)
    sticker_ref: str | None = Field(default=None, max_length=255)
    visibility: Literal["customer", "owner", "internal"] = "customer"
    risk_level: Literal["low", "medium", "high"] = "medium"
    requires_scope: str = Field(default="telegram.send_message", min_length=1, max_length=120)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_payload_for_kind(self) -> TalkAction:
        if self.kind in {TalkActionKind.SEND_MSG, TalkActionKind.REPLY_TO_MSG} and not (
            self.text or ""
        ).strip():
            raise ValueError(f"{self.kind.value} requires non-empty text")
        if self.kind is TalkActionKind.SEND_MEDIA and not self.media_ref:
            raise ValueError("send_media requires media_ref")
        if self.kind is TalkActionKind.SEND_REACTION and not self.reaction:
            raise ValueError("send_reaction requires reaction")
        if self.kind is TalkActionKind.DELETE_MESSAGE and not self.target_message_ref:
            raise ValueError("delete_message requires target_message_ref")
        if self.kind is TalkActionKind.SEND_STICKER and not self.sticker_ref:
            raise ValueError("send_sticker requires sticker_ref")
        return self


class TalkBundle(AgentTalkingModel):
    schema_version: Literal["talk_bundle.v1"] = "talk_bundle.v1"
    workspace_id: int = Field(gt=0)
    agent_id: int = Field(gt=0)
    hermes_run_id: str = Field(min_length=1, max_length=128)
    trigger_ref: str | None = Field(default=None, max_length=255)
    conversation_id: int | None = Field(default=None, gt=0)
    channel_account_id: str | None = Field(default=None, max_length=255)
    actions: list[TalkAction] = Field(default_factory=list)
    talking_policy_snapshot: TalkingPolicy
    policy_decision: str | None = Field(default=None, max_length=120)
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    tool_errors: list[str] = Field(default_factory=list)
    source_trace: list[dict[str, Any]] = Field(default_factory=list)

    def text_preview(self) -> str:
        return "\n\n".join(
            action.text.strip()
            for action in self.actions
            if action.kind in {TalkActionKind.SEND_MSG, TalkActionKind.REPLY_TO_MSG}
            and action.text
            and action.text.strip()
        )


class TalkPolicyDecision(AgentTalkingModel):
    action: Literal["auto_send", "propose", "blocked"]
    reason: str
    blocked_action_indexes: list[int] = Field(default_factory=list)
    required_scopes: list[str] = Field(default_factory=list)


class PacingPlan(AgentTalkingModel):
    action_index: int
    typing_ms: int = Field(ge=0)
    delay_after_ms: int = Field(ge=0)


class TalkBubbleExecutionResult(AgentTalkingModel):
    schema_version: Literal["talk_bubble_execution_result.v1"] = (
        "talk_bubble_execution_result.v1"
    )
    action_index: int = Field(ge=0)
    action_kind: TalkActionKind
    status: Literal["executed", "replayed", "blocked", "failed", "unknown", "unsupported"]
    delivery_state: Literal[
        "confirmed",
        "unknown",
        "failed",
        "blocked",
        "unsupported",
        "replayed",
    ]
    text_preview: str | None = Field(default=None, max_length=160)
    message_id: int | None = None
    external_message_id: str | None = None
    reply_to_message_ref: str | None = Field(default=None, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=512)
    reason_code: str | None = Field(default=None, max_length=120)
    error: str | None = Field(default=None, max_length=500)


class TalkBundleExecutionResult(AgentTalkingModel):
    schema_version: Literal["talk_bundle_execution_result.v1"] = (
        "talk_bundle_execution_result.v1"
    )
    status: Literal["executed", "partial", "blocked", "failed", "unknown"]
    delivery_state: Literal[
        "confirmed",
        "partially_sent",
        "unknown",
        "failed",
        "blocked",
        "unsupported",
    ]
    reason: str
    bundle_key: str = Field(min_length=1, max_length=512)
    conversation_id: int | None = None
    sent_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    unknown_count: int = Field(default=0, ge=0)
    blocked_count: int = Field(default=0, ge=0)
    bubbles: list[TalkBubbleExecutionResult] = Field(default_factory=list)

    def text_preview(self) -> str:
        return "\n\n".join(
            bubble.text_preview or ""
            for bubble in self.bubbles
            if (bubble.text_preview or "").strip()
        ).strip()
