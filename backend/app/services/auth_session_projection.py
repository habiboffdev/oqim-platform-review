from __future__ import annotations

from app.core.config import get_settings
from app.models.workspace import Workspace
from app.schemas.auth import (
    AuthSessionProjection,
    IntegrationProjection,
    IntegrationProvider,
    IntegrationState,
)
from app.schemas.workspace import WorkspaceResponse


def _integration_state(
    *,
    identity_linked: bool,
    durable_connected: bool,
) -> IntegrationState:
    if durable_connected and identity_linked:
        return "connected"
    if identity_linked:
        return "needs_reconnect"
    if durable_connected:
        return "degraded"
    return "not_linked"


def _integration_projection(
    *,
    provider: IntegrationProvider,
    identity_linked: bool,
    durable_connected: bool,
    external_id: str | None,
) -> IntegrationProjection:
    state = _integration_state(
        identity_linked=identity_linked,
        durable_connected=durable_connected,
    )
    return IntegrationProjection(
        provider=provider,
        state=state,
        identity_linked=identity_linked,
        durable_connected=durable_connected,
        needs_reconnect=state == "needs_reconnect",
        external_id=external_id,
    )


def build_auth_session_projection(workspace: Workspace) -> AuthSessionProjection:
    """Build DB-only auth and integration state for protected UI bootstrap.

    Live channel health belongs to channel-specific status endpoints. This
    projection intentionally does not call the GramJS sidecar.
    """
    integrations = [
        _integration_projection(
            provider="telegram_personal",
            identity_linked=workspace.telegram_user_id is not None,
            durable_connected=workspace.telegram_connected,
            external_id=(
                str(workspace.telegram_user_id)
                if workspace.telegram_user_id is not None
                else None
            ),
        ),
        _integration_projection(
            provider="telegram_business_bot",
            identity_linked=bool(workspace.business_connection_id),
            durable_connected=workspace.telegram_business_bot_connected,
            external_id=workspace.business_connection_id,
        ),
        _integration_projection(
            provider="instagram",
            identity_linked=bool(workspace.instagram_page_id),
            durable_connected=workspace.instagram_connected,
            external_id=workspace.instagram_page_id,
        ),
    ]
    is_founder = workspace.id in get_settings().get_admin_workspace_ids()
    return AuthSessionProjection(
        workspace=WorkspaceResponse.model_validate(workspace),
        platform_role="founder" if is_founder else "business_owner",
        is_founder=is_founder,
        onboarding_completed=workspace.onboarding_completed,
        integrations=integrations,
    )
