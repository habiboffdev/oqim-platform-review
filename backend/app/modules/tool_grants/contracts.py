from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolGrantInput(BaseModel):
    """Owner-supplied grant request.

    ``scope`` is an integration-prefixed verb such as ``telegram.send_message``.
    Cross-workspace agent_id is rejected by the service.
    """

    agent_id: int
    scope: str = Field(min_length=3, max_length=120)
    granted_by: str = Field(default="owner", max_length=64)
    grant_reason: str = Field(default="", max_length=2000)
    audit_metadata: dict[str, Any] = Field(default_factory=dict)


class ToolGrantProposalInput(BaseModel):
    """Owner-visible permission change request.

    Creating or revoking an integration permission is not applied directly from
    the UI. It becomes an Action proposal, then Action Runtime applies it after
    owner approval.
    """

    action: Literal["grant", "revoke"]
    scope: str = Field(min_length=3, max_length=120)
    reason: str = Field(default="", max_length=500)
    correlation_id: str = Field(
        default="api:intelligence:agent_tool_grant",
        min_length=1,
        max_length=200,
    )
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=240)


class ToolGrantRead(BaseModel):
    """Read-side projection used by API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    agent_id: int
    scope: str
    granted_by: str
    grant_reason: str
    audit_metadata: dict[str, Any]
    granted_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    use_count: int
    active: bool
