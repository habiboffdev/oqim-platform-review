from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict

from app.schemas.workspace import coerce_trust_mode

# Two trust states only: 'disabled' (off) or 'autopilot' (run + send). Legacy
# 'draft'/'autonomous' coerce to 'disabled' (see coerce_trust_mode).
TrustModeField = Annotated[Literal["disabled", "autopilot"], BeforeValidator(coerce_trust_mode)]

AgentTypeLiteral = Literal[
    "customer",
    "business",
    "seller",
    "support",
    "catalog_update",
    "follow_up",
    "bi",
    "custom",
    "owner",
]


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    persona: dict = {}
    instructions: str | None = None
    example_responses: list = []
    knowledge_config: dict = {"use_catalog": True, "use_knowledge": True}
    channel_config: dict = {"mode": "dm", "chat_ids": []}
    tools_config: dict = {"enabled_tools": ["knowledge_search_catalog"]}
    trust_mode: TrustModeField = "disabled"
    auto_send_threshold: float = 0.85
    escalation_topics: list = []
    agent_type: AgentTypeLiteral = "seller"
    contact_scope: Literal["business", "all"] = "business"


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    is_active: bool | None = None
    is_default: bool | None = None
    persona: dict | None = None
    instructions: str | None = None
    example_responses: list | None = None
    knowledge_config: dict | None = None
    channel_config: dict | None = None
    tools_config: dict | None = None
    trust_mode: TrustModeField | None = None
    auto_send_threshold: float | None = None
    escalation_topics: list | None = None
    agent_type: AgentTypeLiteral | None = None
    contact_scope: Literal["business", "all"] | None = None


class AgentResponse(BaseModel):
    id: int
    workspace_id: int
    name: str
    is_default: bool
    is_active: bool
    persona: dict
    instructions: str | None = None
    example_responses: list
    knowledge_config: dict
    channel_config: dict
    tools_config: dict
    trust_mode: TrustModeField
    auto_send_threshold: float
    escalation_topics: list
    agent_type: str
    contact_scope: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
