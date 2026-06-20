from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.workspace import WorkspaceResponse


class RegisterRequest(BaseModel):
    phone_number: str = Field(..., pattern=r"^\+\d{10,15}$")
    name: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    phone_number: str = Field(..., pattern=r"^\+\d{10,15}$")
    password: str = Field(..., min_length=1)


class BridgeLoginRequest(BaseModel):
    """Complete GramJS Telegram login without an OQIM password."""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId")
    phone: str = Field(..., pattern=r"^\+\d{10,15}$")
    first_name: str = Field(default="", alias="firstName")
    last_name: str = Field(default="", alias="lastName")
    temp_session_id: str | None = Field(default=None, alias="tempSessionId")
    auth_method: str | None = Field(default=None, alias="authMethod")


class CompleteOnboardingRequest(BaseModel):
    """Finish Telegram-first onboarding by setting business basics + OQIM login."""
    name: str | None = Field(default=None, min_length=2, max_length=255)
    category: str | None = Field(default=None, min_length=2, max_length=50)
    monthly_revenue_band: str | None = Field(default=None, min_length=2, max_length=64)
    phone_number: str | None = Field(default=None, pattern=r"^\+\d{10,15}$")
    password: str | None = Field(default=None, min_length=8, max_length=128)
    business_profile: dict[str, Any] | None = None
    preferences: dict[str, Any] | None = None
    sources: dict[str, Any] | None = None
    owner_rules: dict[str, Any] | None = None
    launch_mode: Literal["start", "later"] = "start"


class AuthResponse(BaseModel):
    """Response body for login/register — token is in httpOnly cookie, not here."""
    id: int
    phone_number: str
    name: str
    telegram_connected: bool = False
    onboarding_completed: bool = False
    is_new: bool = False

    class Config:
        from_attributes = True


IntegrationProvider = Literal[
    "telegram_personal",
    "telegram_business_bot",
    "instagram",
]

IntegrationState = Literal[
    "not_linked",
    "linked",
    "connected",
    "needs_reconnect",
    "degraded",
]


class IntegrationProjection(BaseModel):
    provider: IntegrationProvider
    state: IntegrationState
    identity_linked: bool
    durable_connected: bool
    needs_reconnect: bool
    source: Literal["workspace_projection"] = "workspace_projection"
    external_id: str | None = None
    live_state: Literal["not_checked"] = "not_checked"


class AuthSessionProjection(BaseModel):
    schema_version: Literal["auth_session_projection.v1"] = "auth_session_projection.v1"
    authenticated: bool = True
    workspace: WorkspaceResponse
    platform_role: Literal["business_owner", "founder"] = "business_owner"
    is_founder: bool = False
    onboarding_completed: bool
    integrations: list[IntegrationProjection]
