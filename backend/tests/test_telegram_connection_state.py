from app.services.telegram_connection_state import (
    TelegramConnectionStatus,
    resolve_telegram_connection_status,
)


class TestResolveTelegramConnectionStatus:
    async def test_connected_state_requires_matching_workspace(self):
        async def fetch_status(*_args, **_kwargs):
            return {
                "state": "connected",
                "workspaceId": 7,
                "userId": "42",
                "phone": "+998991234567",
                "reconnectAttempts": 0,
            }

        status = await resolve_telegram_connection_status(
            workspace_id=7,
            fetch_status=fetch_status,
        )

        assert status == TelegramConnectionStatus(
            state="connected",
            workspace_id=7,
            user_id="42",
            phone="+998991234567",
            reconnect_attempts=0,
        )

    async def test_reconnecting_state_is_preserved(self):
        async def fetch_status(*_args, **_kwargs):
            return {
                "state": "reconnecting",
                "workspaceId": 7,
                "userId": "42",
                "phone": "+998991234567",
                "reconnectAttempts": 2,
                "lastError": "TIMEOUT",
            }

        status = await resolve_telegram_connection_status(
            workspace_id=7,
            fetch_status=fetch_status,
        )

        assert status == TelegramConnectionStatus(
            state="reconnecting",
            workspace_id=7,
            user_id=None,
            phone=None,
            reconnect_attempts=2,
            last_error="TIMEOUT",
        )

    async def test_connected_state_with_runtime_error_becomes_degraded(self):
        async def fetch_status(*_args, **_kwargs):
            return {
                "state": "connected",
                "workspaceId": 7,
                "userId": "42",
                "phone": "+998991234567",
                "reconnectAttempts": 0,
                "queueSize": 3,
                "lastError": "CATCH_UP_FAILED",
                "lastCatchUpAt": "2026-04-24T10:00:00.000Z",
                "lastCatchUpCount": 12,
            }

        status = await resolve_telegram_connection_status(
            workspace_id=7,
            fetch_status=fetch_status,
        )

        assert status == TelegramConnectionStatus(
            state="degraded",
            workspace_id=7,
            user_id="42",
            phone="+998991234567",
            reconnect_attempts=0,
            last_error="CATCH_UP_FAILED",
            queue_size=3,
            last_catch_up_at="2026-04-24T10:00:00.000Z",
            last_catch_up_count=12,
        )

    async def test_revoked_session_is_explicit_even_when_transport_reports_disconnected(self):
        async def fetch_status(*_args, **_kwargs):
            return {
                "state": "disconnected",
                "workspaceId": 7,
                "userId": None,
                "phone": None,
                "reconnectAttempts": 1,
                "lastError": "SESSION_REVOKED",
            }

        status = await resolve_telegram_connection_status(
            workspace_id=7,
            fetch_status=fetch_status,
        )

        assert status == TelegramConnectionStatus(
            state="revoked",
            workspace_id=7,
            user_id=None,
            phone=None,
            reconnect_attempts=1,
            last_error="SESSION_REVOKED",
        )

    async def test_stale_session_state_is_preserved_without_identity_leak(self):
        async def fetch_status(*_args, **_kwargs):
            return {
                "state": "stale",
                "workspaceId": 7,
                "userId": "42",
                "phone": "+998991234567",
                "reconnectAttempts": 0,
                "lastError": "WORKSPACE_STALE",
            }

        status = await resolve_telegram_connection_status(
            workspace_id=7,
            fetch_status=fetch_status,
        )

        assert status == TelegramConnectionStatus(
            state="stale",
            workspace_id=7,
            user_id=None,
            phone=None,
            reconnect_attempts=0,
            last_error="WORKSPACE_STALE",
        )

    async def test_connected_state_with_other_workspace_becomes_disconnected(self):
        async def fetch_status(*_args, **_kwargs):
            return {
                "state": "connected",
                "workspaceId": 3,
                "userId": "42",
                "phone": "+998991234567",
                "reconnectAttempts": 0,
            }

        status = await resolve_telegram_connection_status(
            workspace_id=7,
            fetch_status=fetch_status,
        )

        assert status == TelegramConnectionStatus.disconnected(workspace_id=7)

    async def test_missing_or_unreachable_status_becomes_disconnected(self):
        async def fetch_status(*_args, **_kwargs):
            return None

        status = await resolve_telegram_connection_status(
            workspace_id=7,
            fetch_status=fetch_status,
        )

        assert status == TelegramConnectionStatus.disconnected(workspace_id=7)
