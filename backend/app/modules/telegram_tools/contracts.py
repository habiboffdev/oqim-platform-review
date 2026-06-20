from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TELEGRAM_READ_MESSAGES = "telegram.read_messages"
TELEGRAM_SEND_MESSAGE = "telegram.send_message"
TELEGRAM_SEND_REACTION = "telegram.send_reaction"
TELEGRAM_EDIT_MESSAGE = "telegram.edit_message"
TELEGRAM_WATCH_CHANNEL = "telegram.watch_channel"
TELEGRAM_FETCH_MEDIA = "telegram.fetch_media"
TELEGRAM_SYNC_HISTORY = "telegram.sync_history"

TelegramToolRisk = Literal["low", "medium", "high"]
TelegramToolOperationKind = Literal["read", "write", "watch", "sync", "media"]
TelegramToolPermissionMode = Literal["ask_always", "auto_approve", "full_access"]


class TelegramToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str
    connector: Literal["telegram"] = "telegram"
    verb: str
    label_uz: str
    description_uz: str
    short_label: str
    operation_kind: TelegramToolOperationKind
    risk_level: TelegramToolRisk
    mutates_external_state: bool
    requires_action_proposal: bool
    default_permission_mode: TelegramToolPermissionMode = "ask_always"
    owner_visible: bool = True
    runtime_boundary: Literal["telegram_tool_runtime"] = "telegram_tool_runtime"


TELEGRAM_TOOL_DEFINITIONS: dict[str, TelegramToolDefinition] = {
    TELEGRAM_READ_MESSAGES: TelegramToolDefinition(
        scope=TELEGRAM_READ_MESSAGES,
        verb="read_messages",
        label_uz="Suhbatni o‘qish",
        description_uz="Agent suhbat tarixini o‘qib, javobni kontekstdan tuzadi.",
        short_label="read",
        operation_kind="read",
        risk_level="medium",
        mutates_external_state=False,
        requires_action_proposal=False,
    ),
    TELEGRAM_SEND_MESSAGE: TelegramToolDefinition(
        scope=TELEGRAM_SEND_MESSAGE,
        verb="send_message",
        label_uz="Javob yuborish",
        description_uz="Agent faqat tasdiqlangan yoki ruxsat berilgan javobni yuboradi.",
        short_label="send",
        operation_kind="write",
        risk_level="high",
        mutates_external_state=True,
        requires_action_proposal=True,
    ),
    TELEGRAM_EDIT_MESSAGE: TelegramToolDefinition(
        scope=TELEGRAM_EDIT_MESSAGE,
        verb="edit_message",
        label_uz="Yuborilgan javobni tahrirlash",
        description_uz="Agent faqat OQIM yuborgan xabarni qayta tahrirlaydi.",
        short_label="edit",
        operation_kind="write",
        risk_level="high",
        mutates_external_state=True,
        requires_action_proposal=True,
    ),
    TELEGRAM_SEND_REACTION: TelegramToolDefinition(
        scope=TELEGRAM_SEND_REACTION,
        verb="send_reaction",
        label_uz="Reaksiya qo‘yish",
        description_uz="Agent mijoz xabariga Telegram reaksiyasini qo‘yadi.",
        short_label="react",
        operation_kind="write",
        risk_level="medium",
        mutates_external_state=True,
        requires_action_proposal=True,
    ),
    TELEGRAM_WATCH_CHANNEL: TelegramToolDefinition(
        scope=TELEGRAM_WATCH_CHANNEL,
        verb="watch_channel",
        label_uz="Telegram kanalni kuzatish",
        description_uz="Agent kanal yoki manba o‘zgarganda ish boshlashi mumkin.",
        short_label="watch",
        operation_kind="watch",
        risk_level="medium",
        mutates_external_state=False,
        requires_action_proposal=False,
    ),
    TELEGRAM_FETCH_MEDIA: TelegramToolDefinition(
        scope=TELEGRAM_FETCH_MEDIA,
        verb="fetch_media",
        label_uz="Media ochish",
        description_uz="Agent rasm, chek va faylni ochib dalil sifatida tekshiradi.",
        short_label="media",
        operation_kind="media",
        risk_level="medium",
        mutates_external_state=False,
        requires_action_proposal=False,
    ),
    TELEGRAM_SYNC_HISTORY: TelegramToolDefinition(
        scope=TELEGRAM_SYNC_HISTORY,
        verb="sync_history",
        label_uz="Suhbat tarixini yangilash",
        description_uz="Agent oxirgi suhbat tarixini qayta sinxronlaydi.",
        short_label="sync",
        operation_kind="sync",
        risk_level="medium",
        mutates_external_state=False,
        requires_action_proposal=False,
    ),
}

TELEGRAM_TOOL_SCOPES = frozenset(TELEGRAM_TOOL_DEFINITIONS)

TelegramToolStatus = Literal[
    "executed",
    "replayed",
    "blocked",
    "failed",
    "unsupported",
]


class TelegramToolMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    message_id: str
    sender_id: str
    sender_name: str = ""
    text: str | None = None
    sent_at: float
    is_outgoing: bool = False
    media_type: str | None = None
    media_metadata: dict[str, Any] | None = None


class TelegramToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: int = Field(gt=0)
    agent_id: int = Field(gt=0)
    scope: str
    status: TelegramToolStatus
    reason_code: str
    correlation_id: str
    idempotency_key: str
    conversation_id: int | None = None
    message_id: int | None = None
    external_message_id: str | None = None
    trigger_id: int | None = None
    delivery_state: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    messages: list[TelegramToolMessage] = Field(default_factory=list)
