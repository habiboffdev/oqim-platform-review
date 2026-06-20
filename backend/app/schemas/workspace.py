from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict


def coerce_trust_mode(value: object) -> object:
    """Two trust states only: 'disabled' (off) or 'autopilot' (run + send). Legacy
    rows/clients sending 'draft' or 'autonomous' map to 'disabled' (neither ever
    auto-sent); anything else falls through to the Literal check and is rejected."""
    if isinstance(value, str) and value.strip().lower() in {"draft", "autonomous"}:
        return "disabled"
    return value


TrustMode = Annotated[Literal["disabled", "autopilot"], BeforeValidator(coerce_trust_mode)]


class WorkspaceResponse(BaseModel):
    id: int
    phone_number: str
    name: str
    type: str
    monthly_revenue_band: str | None = None
    description: str | None = None
    onboarding_profile: dict | None = None
    pipeline_stages: list[str]
    subscription_tier: str
    trust_mode: TrustMode = "disabled"
    telegram_connected: bool
    onboarding_completed: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    monthly_revenue_band: str | None = None
    description: str | None = None
    pipeline_stages: list[str] | None = None
    working_hours: dict | None = None
    trust_mode: TrustMode | None = None
