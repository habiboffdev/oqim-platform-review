from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TelegramControlBotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TelegramControlBotCard(TelegramControlBotModel):
    schema_version: Literal["telegram_control_bot_card.v1"] = (
        "telegram_control_bot_card.v1"
    )
    text: str = Field(min_length=1)
    reply_markup: dict[str, Any]


class TelegramControlBotCallbackResult(TelegramControlBotModel):
    schema_version: Literal["telegram_control_bot_callback_result.v1"] = (
        "telegram_control_bot_callback_result.v1"
    )
    ok: bool
    action: Literal["approve", "reject"]
    workspace_id: int = Field(gt=0)
    proposal_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    action_kind: str = Field(min_length=1)
    answer_text: str = Field(min_length=1)
