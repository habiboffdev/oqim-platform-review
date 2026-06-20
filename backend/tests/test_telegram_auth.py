"""Telegram auth, connection, and onboarding runtime tests."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from httpx import AsyncClient
from sqlalchemy import select

from app.models.commercial_spine import BusinessBrainFactRecord
from app.models.telegram_auth_attempt import TelegramAuthAttempt
from app.services.telegram_auth_recovery import TelegramAuthRecoveryWorker
from app.services.telegram_connection_state import TelegramConnectionStatus


class TestTelegramPhoneAuth:
    async def test_scan_channels_queues_business_brain_source_without_legacy_catalog(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session,
        workspace,
    ):
        async def mock_get(path: str, *, params=None, timeout_seconds: float = 30.0):
            _ = params, timeout_seconds
            assert path == "/channel-posts"
            return [
                {
                    "postId": 701,
                    "text": "Binafsha sumka 250 000 so'm. Yetkazib berish bor.",
                    "date": 1_700_000_000,
                    "mediaType": "photo",
                    "media_url": "https://cdn.example.com/sumka.jpg",
                }
            ]

        learning_calls: list[dict] = []

        class _NoopSourceLearningRuntime:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def process_workspace_sources(self, **kwargs):
                learning_calls.append(kwargs)
                return SimpleNamespace(
                    processed_count=0,
                    review_ready_count=0,
                    retrying_count=0,
                    failed_count=0,
                )

        with patch("app.api.routes.telegram_auth._sidecar_get", side_effect=mock_get), patch(
            "app.api.routes.telegram_auth.OnboardingSourceLearningRuntimeService",
            _NoopSourceLearningRuntime,
        ):
            resp = await client.post(
                "/api/telegram/scan-channels",
                headers=auth_headers,
                json={"channel_ids": [12345]},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"
        assert resp.json()["queued_sources"] == 1
        assert resp.json()["processed"] == 0
        assert learning_calls == [
            {
                "workspace_id": workspace.id,
                "correlation_id": f"telegram-scan-source-learning:{workspace.id}",
                "limit": 1,
                "source_refs": {f"telegram_channel:{workspace.id}:12345"},
                "force": True,
            }
        ]

        facts = (
            await db_session.execute(
                select(BusinessBrainFactRecord).where(
                    BusinessBrainFactRecord.workspace_id == workspace.id,
                    BusinessBrainFactRecord.fact_type == "business_source_fact",
                )
            )
        ).scalars().all()
        assert len(facts) == 1
        assert facts[0].entity_ref == f"workspace:source:telegram_channel:{workspace.id}:12345"
        assert facts[0].value["kind"] == "telegram_channel"
        assert facts[0].value["processing"]["state"] == "queued"
        assert facts[0].value["input"]["messages"][0]["message_id"] == "701"

    async def test_send_code_sets_temp_session_cookie(self, client: AsyncClient):
        with patch(
            "app.api.routes.telegram_auth._sidecar_post_json",
            new=AsyncMock(return_value={"phoneCodeHash": "hash-1", "tempSessionId": "temp-1"}),
        ):
            resp = await client.post(
                "/api/telegram/auth/send-code",
                json={"phone": "+998991234567"},
            )

        assert resp.status_code == 200
        assert resp.json()["tempSessionId"] == "temp-1"
        assert resp.cookies.get("oqim_tg_temp_session") == "temp-1"

    async def test_send_code_requests_app_delivery_preference(self, client: AsyncClient):
        mock_post = AsyncMock(return_value={"phoneCodeHash": "hash-1", "tempSessionId": "temp-1"})

        with patch("app.api.routes.telegram_auth._sidecar_post_json", new=mock_post):
            resp = await client.post(
                "/api/telegram/auth/send-code",
                json={"phone": "+998991234567"},
            )

        assert resp.status_code == 200
        mock_post.assert_awaited_once_with(
            "/auth/send-code",
            {
                "phoneNumber": "+998991234567",
                "deliveryPreference": "app",
            },
        )

    async def test_send_code_can_request_alternate_tcp_auth_transport(self, client: AsyncClient):
        mock_post = AsyncMock(return_value={"phoneCodeHash": "hash-2", "tempSessionId": "temp-2"})

        with patch("app.api.routes.telegram_auth._sidecar_post_json", new=mock_post):
            resp = await client.post(
                "/api/telegram/auth/send-code",
                json={
                    "phone": "+998991234567",
                    "tempSessionId": "old-temp",
                    "authTransport": "tcp",
                },
            )

        assert resp.status_code == 200
        mock_post.assert_awaited_once_with(
            "/auth/send-code",
            {
                "phoneNumber": "+998991234567",
                "deliveryPreference": "app",
                "tempSessionId": "old-temp",
                "authTransport": "tcp",
            },
        )

    async def test_send_code_rejects_unknown_auth_transport(self, client: AsyncClient):
        with patch("app.api.routes.telegram_auth._sidecar_post_json", new=AsyncMock()) as mock_post:
            resp = await client.post(
                "/api/telegram/auth/send-code",
                json={"phone": "+998991234567", "authTransport": "bad"},
            )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "authTransport must be web or tcp"
        mock_post.assert_not_awaited()

    async def test_send_code_surfaces_device_code_unavailable(
        self,
        client: AsyncClient,
        db_session,
    ):
        with patch(
            "app.api.routes.telegram_auth._sidecar_post_json",
            new=AsyncMock(
                return_value={
                    "_sidecar_status_code": 409,
                    "error": "DEVICE_CODE_UNAVAILABLE",
                    "code": "DEVICE_CODE_UNAVAILABLE",
                    "message": "Telegram did not offer app/device code delivery for this phone number.",
                }
            ),
        ):
            resp = await client.post(
                "/api/telegram/auth/send-code",
                json={"phone": "+998991234567"},
            )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "DEVICE_CODE_UNAVAILABLE"
        attempt = (
            await db_session.execute(
                select(TelegramAuthAttempt).where(
                    TelegramAuthAttempt.phone_number == "+998991234567",
                    TelegramAuthAttempt.last_step == "send_code",
                )
            )
        ).scalar_one()
        assert attempt.state == "failed"
        assert attempt.last_error == "DEVICE_CODE_UNAVAILABLE"

    async def test_send_code_persists_auth_attempt(self, client: AsyncClient, db_session):
        with patch(
            "app.api.routes.telegram_auth._sidecar_post_json",
            new=AsyncMock(
                return_value={
                    "phoneCodeHash": "hash-1",
                    "tempSessionId": "temp-1",
                    "delivery": {
                        "type": "auth.SentCodeTypeSms",
                        "nextType": "auth.CodeTypeCall",
                        "timeoutSeconds": 90,
                        "preferredType": "auth.SentCodeTypeApp",
                        "degraded": True,
                        "degradedReason": "telegram_selected_non_app_delivery",
                    },
                }
            ),
        ):
            resp = await client.post(
                "/api/telegram/auth/send-code",
                json={"phone": "+998991234567"},
            )

        assert resp.status_code == 200
        attempt = (
            await db_session.execute(
                select(TelegramAuthAttempt).where(
                    TelegramAuthAttempt.temp_session_id == "temp-1"
                )
            )
        ).scalar_one()
        assert attempt.phone_number == "+998991234567"
        assert attempt.phone_code_hash == "hash-1"
        assert attempt.state == "code_sent"
        assert attempt.last_step == "send_code"
        assert attempt.delivery_type == "auth.SentCodeTypeSms"
        assert attempt.preferred_delivery_type == "auth.SentCodeTypeApp"
        assert attempt.delivery_degraded is True
        assert attempt.delivery_degraded_reason == "telegram_selected_non_app_delivery"
        assert attempt.next_delivery_type == "auth.CodeTypeCall"
        assert attempt.timeout_seconds == 90
        assert attempt.next_recovery_at is not None
        assert attempt.recovery_state == "scheduled"

    async def test_send_code_app_delivery_without_next_route_waits_for_code(
        self,
        client: AsyncClient,
        db_session,
    ):
        with patch(
            "app.api.routes.telegram_auth._sidecar_post_json",
            new=AsyncMock(
                return_value={
                    "phoneCodeHash": "hash-app",
                    "tempSessionId": "temp-app",
                    "delivery": {
                        "type": "auth.SentCodeTypeApp",
                        "preferredType": "auth.SentCodeTypeApp",
                        "degraded": False,
                    },
                }
            ),
        ):
            resp = await client.post(
                "/api/telegram/auth/send-code",
                json={"phone": "+998991234567"},
            )

        assert resp.status_code == 200
        attempt = (
            await db_session.execute(
                select(TelegramAuthAttempt).where(
                    TelegramAuthAttempt.temp_session_id == "temp-app"
                )
            )
        ).scalar_one()
        assert attempt.state == "code_sent"
        assert attempt.delivery_type == "auth.SentCodeTypeApp"
        assert attempt.next_delivery_type is None
        assert attempt.next_recovery_at is None
        assert attempt.recovery_state == "awaiting_code"

    async def test_send_code_stores_temp_session_data_server_side_only(
        self,
        client: AsyncClient,
        db_session,
    ):
        with patch(
            "app.api.routes.telegram_auth._sidecar_post_json",
            new=AsyncMock(
                return_value={
                    "phoneCodeHash": "hash-1",
                    "tempSessionId": "temp-1",
                    "tempSessionString": "server-only-session",
                }
            ),
        ):
            resp = await client.post(
                "/api/telegram/auth/send-code",
                json={"phone": "+998991234567"},
            )

        assert resp.status_code == 200
        assert "tempSessionString" not in resp.json()
        attempt = (
            await db_session.execute(
                select(TelegramAuthAttempt).where(
                    TelegramAuthAttempt.temp_session_id == "temp-1"
                )
            )
        ).scalar_one()
        assert attempt.temp_session_data == "server-only-session"

    async def test_resend_code_uses_temp_session_cookie(self, client: AsyncClient):
        mock_post = AsyncMock(
            return_value={
                "phoneCodeHash": "hash-2",
                "tempSessionId": "temp-2",
                "delivery": {"type": "auth.SentCodeTypeApp"},
            },
        )
        client.cookies.set("oqim_tg_temp_session", "temp-2")

        with patch("app.api.routes.telegram_auth._sidecar_post_json", new=mock_post):
            resp = await client.post(
                "/api/telegram/auth/resend-code",
                json={"phone": "+998991234567"},
            )

        assert resp.status_code == 200
        mock_post.assert_awaited_once_with(
            "/auth/resend-code",
            {"phoneNumber": "+998991234567", "tempSessionId": "temp-2"},
        )

    async def test_resend_code_prefers_explicit_temp_session_id(self, client: AsyncClient):
        mock_post = AsyncMock(
            return_value={
                "phoneCodeHash": "hash-3",
                "tempSessionId": "temp-explicit",
            },
        )
        client.cookies.set("oqim_tg_temp_session", "temp-cookie")

        with patch("app.api.routes.telegram_auth._sidecar_post_json", new=mock_post):
            resp = await client.post(
                "/api/telegram/auth/resend-code",
                json={
                    "phone": "+998991234567",
                    "tempSessionId": "temp-explicit",
                    "phoneCodeHash": "hash-2",
                },
            )

        assert resp.status_code == 200
        mock_post.assert_awaited_once_with(
            "/auth/resend-code",
            {
                "phoneNumber": "+998991234567",
                "tempSessionId": "temp-explicit",
                "phoneCodeHash": "hash-2",
            },
        )

    async def test_resend_code_restores_temp_session_data_after_sidecar_restart(
        self,
        client: AsyncClient,
        db_session,
    ):
        db_session.add(
            TelegramAuthAttempt(
                phone_number="+998991234567",
                temp_session_id="temp-1",
                phone_code_hash="hash-1",
                temp_session_data="persisted-session",
                state="code_sent",
                last_step="send_code",
            )
        )
        await db_session.commit()
        mock_post = AsyncMock(
            return_value={
                "phoneCodeHash": "hash-2",
                "tempSessionId": "temp-1",
                "tempSessionString": "rotated-session",
            },
        )

        with patch("app.api.routes.telegram_auth._sidecar_post_json", new=mock_post):
            resp = await client.post(
                "/api/telegram/auth/resend-code",
                json={"phone": "+998991234567", "tempSessionId": "temp-1"},
            )

        assert resp.status_code == 200
        assert "tempSessionString" not in resp.json()
        mock_post.assert_awaited_once_with(
            "/auth/resend-code",
            {
                "phoneNumber": "+998991234567",
                "tempSessionId": "temp-1",
                "tempSessionString": "persisted-session",
                "phoneCodeHash": "hash-1",
            },
        )
        attempt = (
            await db_session.execute(
                select(TelegramAuthAttempt).where(
                    TelegramAuthAttempt.temp_session_id == "temp-1"
                )
            )
        ).scalar_one()
        assert attempt.temp_session_data == "rotated-session"

    async def test_resend_code_updates_existing_auth_attempt(self, client: AsyncClient, db_session):
        with patch(
            "app.api.routes.telegram_auth._sidecar_post_json",
            new=AsyncMock(return_value={"phoneCodeHash": "hash-1", "tempSessionId": "temp-1"}),
        ):
            await client.post("/api/telegram/auth/send-code", json={"phone": "+998991234567"})

        with patch(
            "app.api.routes.telegram_auth._sidecar_post_json",
            new=AsyncMock(
                return_value={
                    "phoneCodeHash": "hash-2",
                    "tempSessionId": "temp-1",
                    "delivery": {"type": "auth.SentCodeTypeCall"},
                }
            ),
        ):
            resp = await client.post(
                "/api/telegram/auth/resend-code",
                json={
                    "phone": "+998991234567",
                    "tempSessionId": "temp-1",
                    "phoneCodeHash": "hash-1",
                },
            )

        assert resp.status_code == 200
        attempt = (
            await db_session.execute(
                select(TelegramAuthAttempt).where(
                    TelegramAuthAttempt.temp_session_id == "temp-1"
                )
            )
        ).scalar_one()
        assert attempt.state == "recovery_sent"
        assert attempt.last_step == "resend_code"
        assert attempt.phone_code_hash == "hash-2"
        assert attempt.delivery_type == "auth.SentCodeTypeCall"
        assert attempt.recovery_state == "exhausted"
        assert attempt.attempt_count == 2

    async def test_sidecar_auth_error_preserves_http_status(self, client: AsyncClient):
        with patch(
            "app.api.routes.telegram_auth._sidecar_post_json",
            new=AsyncMock(
                return_value={
                    "error": "No pending code. Call /auth/send-code first.",
                    "_sidecar_status_code": 400,
                }
            ),
        ):
            resp = await client.post(
                "/api/telegram/auth/resend-code",
                json={"phone": "+998991234567", "tempSessionId": "missing-temp"},
            )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "No pending code. Call /auth/send-code first."

    async def test_sidecar_auth_error_persists_failed_auth_attempt(self, client: AsyncClient, db_session):
        with patch(
            "app.api.routes.telegram_auth._sidecar_post_json",
            new=AsyncMock(
                return_value={
                    "error": "No pending code. Call /auth/send-code first.",
                    "_sidecar_status_code": 400,
                }
            ),
        ):
            resp = await client.post(
                "/api/telegram/auth/resend-code",
                json={
                    "phone": "+998991234567",
                    "tempSessionId": "missing-temp",
                    "phoneCodeHash": "hash-missing",
                },
            )

        assert resp.status_code == 400
        attempt = (
            await db_session.execute(
                select(TelegramAuthAttempt).where(
                    TelegramAuthAttempt.temp_session_id == "missing-temp"
                )
            )
        ).scalar_one()
        assert attempt.state == "failed"
        assert attempt.last_step == "resend_code"
        assert attempt.last_error == "No pending code. Call /auth/send-code first."

    async def test_send_code_connect_failure_clears_stale_delivery_truth(
        self,
        client: AsyncClient,
        db_session,
    ):
        db_session.add(
            TelegramAuthAttempt(
                phone_number="+998991234567",
                temp_session_id="old-temp",
                state="code_sent",
                last_step="send_code",
                delivery_type="auth.SentCodeTypeSms",
                preferred_delivery_type="auth.SentCodeTypeApp",
                delivery_degraded=True,
                delivery_degraded_reason="telegram_selected_non_app_delivery",
                next_delivery_type="auth.CodeTypeCall",
                timeout_seconds=90,
                delivery_payload={"type": "auth.SentCodeTypeSms"},
            )
        )
        await db_session.commit()

        with patch(
            "app.api.routes.telegram_auth._sidecar_post_json",
            new=AsyncMock(
                return_value={
                    "error": "PHONE_CODE_SEND_FAILED",
                    "code": "PHONE_CODE_SEND_FAILED",
                    "_sidecar_status_code": 400,
                }
            ),
        ):
            resp = await client.post(
                "/api/telegram/auth/send-code",
                json={"phone": "+998991234567"},
            )

        assert resp.status_code == 400
        attempt = (
            await db_session.execute(
                select(TelegramAuthAttempt).where(
                    TelegramAuthAttempt.phone_number == "+998991234567"
                )
            )
        ).scalar_one()
        assert attempt.state == "failed"
        assert attempt.last_step == "send_code"
        assert attempt.last_error == "PHONE_CODE_SEND_FAILED"
        assert attempt.delivery_type is None
        assert attempt.preferred_delivery_type is None
        assert attempt.delivery_degraded is False
        assert attempt.delivery_degraded_reason is None
        assert attempt.next_delivery_type is None
        assert attempt.timeout_seconds is None
        assert attempt.delivery_payload is None
        assert attempt.next_recovery_at is None

    async def test_send_code_throttles_repeated_phone_attempts(self, client: AsyncClient, db_session):
        now = datetime.now(UTC)
        for index in range(3):
            db_session.add(
                TelegramAuthAttempt(
                    phone_number="+998991234567",
                    temp_session_id=f"temp-throttle-{index}",
                    state="code_sent",
                    last_step="send_code",
                    created_at=now - timedelta(minutes=1),
                )
            )
        await db_session.commit()

        resp = await client.post(
            "/api/telegram/auth/send-code",
            json={"phone": "+998991234567"},
        )

        assert resp.status_code == 429
        assert resp.headers["retry-after"] == "600"

    async def test_send_code_throttles_reused_attempt_row(self, client: AsyncClient, db_session):
        now = datetime.now(UTC)
        db_session.add(
            TelegramAuthAttempt(
                phone_number="+998991234567",
                temp_session_id="temp-reused-throttle",
                state="code_sent",
                last_step="send_code",
                attempt_count=3,
                updated_at=now - timedelta(minutes=1),
            )
        )
        await db_session.commit()

        resp = await client.post(
            "/api/telegram/auth/send-code",
            json={"phone": "+998991234567"},
        )

        assert resp.status_code == 429
        assert resp.headers["retry-after"] == "600"

    async def test_attempt_status_returns_browser_safe_recovery_state(
        self,
        client: AsyncClient,
        db_session,
    ):
        next_recovery_at = datetime.now(UTC) + timedelta(seconds=90)
        db_session.add(
            TelegramAuthAttempt(
                phone_number="+998991234567",
                temp_session_id="temp-status",
                phone_code_hash="hash-status",
                temp_session_data="server-only-session",
                state="code_sent",
                recovery_state="scheduled",
                delivery_payload={"type": "auth.SentCodeTypeSms"},
                next_recovery_at=next_recovery_at,
                recovery_attempt_count=0,
                max_recovery_attempts=2,
            )
        )
        await db_session.commit()

        resp = await client.get(
            "/api/telegram/auth/attempt-status",
            params={"temp_session_id": "temp-status"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "code_sent"
        assert data["recoveryState"] == "scheduled"
        assert data["nextRecoveryAt"] is not None
        assert "server-only-session" not in json.dumps(data)

    async def test_sign_in_uses_stored_temp_session_after_backend_restart(
        self,
        client: AsyncClient,
        db_session,
    ):
        db_session.add(
            TelegramAuthAttempt(
                phone_number="+998991234567",
                temp_session_id="temp-restart",
                phone_code_hash="hash-restart",
                temp_session_data="persisted-temp-session",
                state="code_sent",
                last_step="send_code",
            )
        )
        await db_session.commit()
        mock_post = AsyncMock(
            return_value={
                "user": {"userId": "42", "phone": "+998991234567"},
                "tempSessionId": "temp-restart",
            }
        )

        with patch("app.api.routes.telegram_auth._sidecar_post_json", new=mock_post):
            resp = await client.post(
                "/api/telegram/auth/sign-in",
                json={
                    "phone": "+998991234567",
                    "code": "12345",
                    "tempSessionId": "temp-restart",
                },
            )

        assert resp.status_code == 200
        mock_post.assert_awaited_once_with(
            "/auth/sign-in",
            {
                "phoneNumber": "+998991234567",
                "phoneCodeHash": "hash-restart",
                "phoneCode": "12345",
                "tempSessionId": "temp-restart",
                "tempSessionString": "persisted-temp-session",
            },
        )

    async def test_check_2fa_uses_temp_session_cookie(self, client: AsyncClient):
        mock_post = AsyncMock(return_value={"user": {"userId": "42"}, "tempSessionId": "temp-2"})
        client.cookies.set("oqim_tg_temp_session", "temp-2")

        with patch("app.api.routes.telegram_auth._sidecar_post_json", new=mock_post):
            resp = await client.post(
                "/api/telegram/auth/check-2fa",
                json={"password": "secret"},
            )

        assert resp.status_code == 200
        mock_post.assert_awaited_once_with(
            "/auth/check-password",
            {"password": "secret", "tempSessionId": "temp-2"},
        )

    async def test_qr_start_proxies_to_sidecar(self, client: AsyncClient):
        mock_post = AsyncMock(return_value={"status": "started"})
        with patch("app.api.routes.telegram_auth._sidecar_post_json", new=mock_post):
            resp = await client.post("/api/telegram/auth/qr/start")

        assert resp.status_code == 200
        assert resp.json()["status"] == "started"
        mock_post.assert_awaited_once_with("/qr-auth/start", {})

    async def test_qr_status_proxies_user(self, client: AsyncClient):
        mock_get = AsyncMock(
            return_value={
                "status": "success",
                "user": {"userId": "42", "phone": "+998991234567"},
            },
        )
        with patch("app.api.routes.telegram_auth._sidecar_get", new=mock_get):
            resp = await client.get("/api/telegram/auth/qr/status")

        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        mock_get.assert_awaited_once_with("/qr-auth/status")


class TestTelegramAuthRecoveryWorker:
    async def test_worker_resends_due_attempt_without_browser(
        self,
        db_session,
    ):
        now = datetime.now(UTC)
        attempt = TelegramAuthAttempt(
            phone_number="+998991234567",
            temp_session_id="temp-worker",
            phone_code_hash="hash-1",
            temp_session_data="persisted-session",
            state="code_sent",
            recovery_state="scheduled",
            delivery_type="auth.SentCodeTypeSms",
            next_delivery_type="auth.CodeTypeCall",
            timeout_seconds=1,
            next_recovery_at=now - timedelta(seconds=1),
        )
        db_session.add(attempt)
        await db_session.commit()
        sidecar_post = AsyncMock(
            return_value={
                "phoneCodeHash": "hash-2",
                "tempSessionId": "temp-worker",
                "tempSessionString": "rotated-session",
                "delivery": {"type": "auth.SentCodeTypeCall"},
            }
        )

        @asynccontextmanager
        async def db_factory():
            yield db_session

        worker = TelegramAuthRecoveryWorker(
            db_factory=db_factory,
            sidecar_post_json=sidecar_post,
        )
        processed = await worker.run_once(now=now)

        assert processed == 1
        sidecar_post.assert_awaited_once_with(
            "/auth/resend-code",
            {
                "phoneNumber": "+998991234567",
                "tempSessionId": "temp-worker",
                "phoneCodeHash": "hash-1",
                "tempSessionString": "persisted-session",
            },
        )
        refreshed = await db_session.get(TelegramAuthAttempt, attempt.id)
        assert refreshed.state == "recovery_sent"
        assert refreshed.last_step == "server_recovery"
        assert refreshed.phone_code_hash == "hash-2"
        assert refreshed.temp_session_data == "rotated-session"
        assert refreshed.recovery_attempt_count == 1
        assert refreshed.recovery_state == "exhausted"

    async def test_worker_reschedules_rate_limited_attempt(self, db_session):
        now = datetime.now(UTC)
        attempt = TelegramAuthAttempt(
            phone_number="+998991234567",
            temp_session_id="temp-rate-limited",
            phone_code_hash="hash-1",
            state="code_sent",
            recovery_state="scheduled",
            next_delivery_type="auth.CodeTypeCall",
            next_recovery_at=now - timedelta(seconds=1),
            max_recovery_attempts=2,
        )
        db_session.add(attempt)
        await db_session.commit()

        @asynccontextmanager
        async def db_factory():
            yield db_session

        worker = TelegramAuthRecoveryWorker(
            db_factory=db_factory,
            sidecar_post_json=AsyncMock(
                return_value={
                    "_sidecar_status_code": 429,
                    "error": "Rate limited",
                    "retryAfter": 120,
                }
            ),
        )
        processed = await worker.run_once(now=now)

        assert processed == 1
        refreshed = await db_session.get(TelegramAuthAttempt, attempt.id)
        assert refreshed.state == "code_sent"
        assert refreshed.recovery_state == "scheduled"
        assert refreshed.retry_after_seconds == 120
        assert refreshed.next_recovery_at is not None


class TestTelegramOnboardingCompat:
    async def test_auth_status_unauthenticated_returns_sanitized_disconnected(
        self,
        client: AsyncClient,
    ):
        with patch(
            "app.api.routes.telegram_auth._sidecar_get",
            new=AsyncMock(
                return_value={
                    "state": "connected",
                    "workspaceId": 999,
                    "userId": "42",
                    "phone": "+998991234567",
                    "reconnectAttempts": 0,
                },
            ),
        ) as mock_sidecar_get:
            resp = await client.get("/api/telegram/auth/status")

        assert resp.status_code == 200
        assert resp.json() == {
            "state": "disconnected",
            "workspaceId": 0,
            "userId": None,
            "phone": None,
            "reconnectAttempts": 0,
            "lastError": None,
            "queueSize": 0,
            "lastCatchUpAt": None,
            "lastCatchUpCount": 0,
            "identityLinked": False,
            "needsReconnect": False,
        }
        mock_sidecar_get.assert_not_awaited()

    async def test_status_compat_alias_is_unmounted(
        self,
        client: AsyncClient,
    ):
        resp = await client.get("/api/telegram/status")

        assert resp.status_code == 404

    async def test_ingestion_progress_and_events_aliases_are_unmounted(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        progress = await client.get(
            "/api/telegram/ingestion-progress", headers=auth_headers
        )
        events = await client.get(
            "/api/telegram/ingestion-events", headers=auth_headers
        )

        assert progress.status_code == 404
        assert events.status_code == 404

    async def test_auth_status_uses_canonical_connection_truth(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        with patch(
            "app.api.routes.telegram_auth.resolve_telegram_connection_status",
            new=AsyncMock(
                return_value=TelegramConnectionStatus(
                    state="connected",
                    workspace_id=1,
                    user_id="42",
                    phone="+998991234567",
                    reconnect_attempts=0,
                ),
            ),
        ) as mock_resolve:
            resp = await client.get("/api/telegram/auth/status", headers=auth_headers)

        assert resp.status_code == 200
        assert resp.json()["state"] == "connected"
        mock_resolve.assert_awaited_once()

    async def test_auth_status_does_not_emit_compat_connected_flag(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        with patch(
            "app.api.routes.telegram_auth.resolve_telegram_connection_status",
            new=AsyncMock(
                return_value=TelegramConnectionStatus(
                    state="connected",
                    workspace_id=1,
                    user_id="42",
                    phone="+998991234567",
                    reconnect_attempts=0,
                ),
            ),
        ):
            resp = await client.get("/api/telegram/auth/status", headers=auth_headers)

        assert resp.status_code == 200
        assert resp.json()["state"] == "connected"
        assert "connected" not in resp.json()

    async def test_auth_status_preserves_reconnecting_state(self, client: AsyncClient, auth_headers: dict[str, str]):
        with patch(
            "app.api.routes.telegram_auth.resolve_telegram_connection_status",
            new=AsyncMock(
                return_value=TelegramConnectionStatus(
                    state="reconnecting",
                    workspace_id=1,
                    user_id=None,
                    phone=None,
                    reconnect_attempts=2,
                    last_error="TIMEOUT",
                ),
            ),
        ):
            resp = await client.get("/api/telegram/auth/status", headers=auth_headers)

        assert resp.status_code == 200
        assert resp.json()["state"] == "reconnecting"
        assert resp.json()["lastError"] == "TIMEOUT"

    async def test_auth_status_marks_linked_revoked_sessions_as_reconnect_needed(
        self,
        client: AsyncClient,
        workspace,
        auth_headers: dict[str, str],
        db_session,
    ):
        workspace.telegram_user_id = 424242
        db_session.add(workspace)
        await db_session.commit()

        with patch(
            "app.api.routes.telegram_auth.resolve_telegram_connection_status",
            new=AsyncMock(
                return_value=TelegramConnectionStatus(
                    state="revoked",
                    workspace_id=workspace.id,
                    user_id=None,
                    phone=None,
                    reconnect_attempts=0,
                    last_error="SESSION_REVOKED",
                ),
            ),
        ):
            resp = await client.get("/api/telegram/auth/status", headers=auth_headers)

        assert resp.status_code == 200
        assert resp.json()["identityLinked"] is True
        assert resp.json()["needsReconnect"] is True
        assert resp.json()["state"] == "revoked"
        assert resp.json()["lastError"] == "SESSION_REVOKED"

    async def test_auth_status_marks_stale_linked_session_as_reconnect_needed(
        self,
        client: AsyncClient,
        workspace,
        auth_headers: dict[str, str],
        db_session,
    ):
        workspace.telegram_user_id = 424242
        db_session.add(workspace)
        await db_session.commit()

        with patch(
            "app.api.routes.telegram_auth.resolve_telegram_connection_status",
            new=AsyncMock(
                return_value=TelegramConnectionStatus(
                    state="stale",
                    workspace_id=workspace.id,
                    user_id=None,
                    phone=None,
                    reconnect_attempts=0,
                    last_error="WORKSPACE_STALE",
                ),
            ),
        ):
            resp = await client.get("/api/telegram/auth/status", headers=auth_headers)

        assert resp.status_code == 200
        assert resp.json()["identityLinked"] is True
        assert resp.json()["needsReconnect"] is True
        assert resp.json()["state"] == "stale"

    async def test_auth_status_marks_identity_mismatch_as_reconnect_needed(
        self,
        client: AsyncClient,
        workspace,
        auth_headers: dict[str, str],
        db_session,
    ):
        workspace.telegram_user_id = 424242
        db_session.add(workspace)
        await db_session.commit()

        with patch(
            "app.api.routes.telegram_auth.resolve_telegram_connection_status",
            new=AsyncMock(
                return_value=TelegramConnectionStatus(
                    state="connected",
                    workspace_id=workspace.id,
                    user_id="999999",
                    phone="+998991234567",
                    reconnect_attempts=0,
                ),
            ),
        ):
            resp = await client.get("/api/telegram/auth/status", headers=auth_headers)

        data = resp.json()
        assert resp.status_code == 200
        assert data["identityLinked"] is True
        assert data["identityMismatch"] is True
        assert data["identityVerified"] is False
        assert data["needsReconnect"] is True
        assert data["state"] == "stale"
        assert data["userId"] is None
        assert data["phone"] is None
        assert data["lastError"] == "telegram_identity_mismatch"

    async def test_auth_status_marks_linked_connected_without_identity_as_unverified(
        self,
        client: AsyncClient,
        workspace,
        auth_headers: dict[str, str],
        db_session,
    ):
        workspace.telegram_user_id = 424242
        db_session.add(workspace)
        await db_session.commit()

        with patch(
            "app.api.routes.telegram_auth.resolve_telegram_connection_status",
            new=AsyncMock(
                return_value=TelegramConnectionStatus(
                    state="connected",
                    workspace_id=workspace.id,
                    user_id=None,
                    phone="+998991234567",
                    reconnect_attempts=0,
                ),
            ),
        ):
            resp = await client.get("/api/telegram/auth/status", headers=auth_headers)

        data = resp.json()
        assert resp.status_code == 200
        assert data["identityLinked"] is True
        assert data["identityMismatch"] is False
        assert data["identityVerified"] is False
        assert data["needsReconnect"] is True
        assert data["state"] == "stale"
        assert data["userId"] is None
        assert data["phone"] is None
        assert data["lastError"] == "telegram_identity_unverified"

    async def test_auth_status_uses_short_sidecar_timeout(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        mock_resolve = AsyncMock(
            return_value=TelegramConnectionStatus(
                state="connected",
                workspace_id=1,
                user_id=None,
                phone=None,
                reconnect_attempts=0,
            ),
        )
        with patch("app.api.routes.telegram_auth.resolve_telegram_connection_status", new=mock_resolve):
            resp = await client.get("/api/telegram/auth/status", headers=auth_headers)

        assert resp.status_code == 200
        mock_resolve.assert_awaited_once()

    async def test_start_ingestion_spawns_bridge(self, client: AsyncClient, auth_headers: dict[str, str]):
        start_runtime = AsyncMock(return_value={"status": "started", "workspace_id": 1, "progress": {"phase": "starting"}})
        with patch(
            "app.api.routes.telegram_auth.resolve_telegram_connection_status",
            new=AsyncMock(
                return_value=TelegramConnectionStatus(
                    state="connected",
                    workspace_id=1,
                    user_id="42",
                    phone="+998991234567",
                    reconnect_attempts=0,
                ),
            ),
        ), patch(
            "app.api.routes.telegram_auth.onboarding_runtime.start_ingestion",
            new=start_runtime,
        ):
            resp = await client.post("/api/telegram/start-ingestion", headers=auth_headers)

        assert resp.status_code == 200
        assert resp.json()["status"] == "started"
        start_runtime.assert_awaited_once()

    async def test_start_ingestion_allows_degraded_linked_session(self, client: AsyncClient, auth_headers: dict[str, str]):
        start_runtime = AsyncMock(return_value={"status": "started", "workspace_id": 1, "progress": {"phase": "starting"}})
        with patch(
            "app.api.routes.telegram_auth.resolve_telegram_connection_status",
            new=AsyncMock(
                return_value=TelegramConnectionStatus(
                    state="degraded",
                    workspace_id=1,
                    user_id="42",
                    phone="+998991234567",
                    reconnect_attempts=0,
                    last_error="GET_DIALOGS_TIMEOUT",
                ),
            ),
        ), patch(
            "app.api.routes.telegram_auth.onboarding_runtime.start_ingestion",
            new=start_runtime,
        ):
            resp = await client.post("/api/telegram/start-ingestion", headers=auth_headers)

        assert resp.status_code == 200
        start_runtime.assert_awaited_once()

    async def test_start_ingestion_is_idempotent_when_progress_is_active(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        start_runtime = AsyncMock(return_value={
            "status": "already_running",
            "workspace_id": 1,
            "progress": {"phase": "reading_dialogs"},
        })

        with patch(
            "app.api.routes.telegram_auth.resolve_telegram_connection_status",
            new=AsyncMock(
                return_value=TelegramConnectionStatus(
                    state="connected",
                    workspace_id=1,
                    user_id="42",
                    phone="+998991234567",
                    reconnect_attempts=0,
                ),
            ),
        ), patch(
            "app.api.routes.telegram_auth.onboarding_runtime.start_ingestion",
            new=start_runtime,
        ):
            resp = await client.post("/api/telegram/start-ingestion", headers=auth_headers)

        assert resp.status_code == 200
        assert resp.json()["status"] == "already_running"
        assert resp.json()["progress"]["phase"] == "reading_dialogs"
        start_runtime.assert_awaited_once()

    async def test_start_ingestion_does_not_restart_completed_progress(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        start_runtime = AsyncMock(return_value={
            "status": "completed",
            "workspace_id": 1,
            "progress": {"phase": "done", "completed": True},
        })

        with patch(
            "app.api.routes.telegram_auth.resolve_telegram_connection_status",
            new=AsyncMock(
                return_value=TelegramConnectionStatus(
                    state="connected",
                    workspace_id=1,
                    user_id="42",
                    phone="+998991234567",
                    reconnect_attempts=0,
                ),
            ),
        ), patch(
            "app.api.routes.telegram_auth.onboarding_runtime.start_ingestion",
            new=start_runtime,
        ):
            resp = await client.post("/api/telegram/start-ingestion", headers=auth_headers)

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        start_runtime.assert_awaited_once()

    async def test_set_progress_reconciles_before_notifying(self):
        from app.services.onboarding_runtime import set_progress

        progress = {
            "workspace_id": 1,
            "phase": "not_started",
            "percent": 0,
            "contacts_found": 0,
            "customers_identified": 0,
            "products_extracted": 0,
            "knowledge_items": 0,
            "voice_profile_ready": False,
            "voice_discoveries": [],
            "completed": False,
            "errors": [],
        }
        reconciled = {**progress, "contacts_found": 50, "customers_identified": 4}
        store_progress = AsyncMock()
        notify_progress = AsyncMock()

        with patch("app.services.onboarding_runtime.reconcile_progress_with_db", new=AsyncMock(return_value=reconciled)), \
             patch("app.services.onboarding_runtime.store_progress", new=store_progress), \
             patch("app.services.onboarding_runtime.notify_progress", new=notify_progress):
            result = await set_progress(progress, phase="reading_dialogs", percent=35, contacts_found=0)

        assert result["contacts_found"] == 50
        assert progress["contacts_found"] == 50
        store_progress.assert_awaited_once_with(1, progress)
        notify_progress.assert_awaited_once_with(progress)

    def test_progress_db_floor_does_not_show_zero_after_dialogs_exist(self):
        from app.services.onboarding_runtime import apply_progress_db_floor

        progress = {
            "workspace_id": 1,
            "phase": "not_started",
            "percent": 0,
            "contacts_found": 0,
            "customers_identified": 0,
            "products_extracted": 0,
            "knowledge_items": 0,
            "voice_profile_ready": False,
            "voice_discoveries": [],
            "completed": False,
            "errors": [],
        }

        reconciled = apply_progress_db_floor(
            progress,
            contact_count=548,
            customer_count=0,
        )

        assert reconciled["phase"] == "reading_dialogs"
        assert reconciled["percent"] == 35
        assert reconciled["contacts_found"] == 548
        assert reconciled["voice_profile_ready"] is False

    def test_progress_db_floor_advances_when_business_brain_voice_projection_exists(self):
        from app.services.onboarding_runtime import apply_progress_db_floor

        progress = {
            "workspace_id": 1,
            "phase": "reading_dialogs",
            "percent": 35,
            "contacts_found": 548,
            "customers_identified": 0,
            "products_extracted": 0,
            "knowledge_items": 0,
            "voice_profile_ready": False,
            "voice_discoveries": [],
            "completed": False,
            "errors": [],
        }
        voice_projection = Mock(
            degraded=False,
            state={"traits": [{"message_count_analyzed": 8, "quality_score": "weak"}]},
        )

        reconciled = apply_progress_db_floor(
            progress,
            contact_count=548,
            customer_count=0,
            voice_projection=voice_projection,
        )

        assert reconciled["phase"] == "awaiting_channels"
        assert reconciled["percent"] == 65
        assert reconciled["voice_profile_ready"] is True

    async def test_channels_proxy_returns_list(self, client: AsyncClient, auth_headers: dict[str, str]):
        sidecar_channels = [{"id": 1, "name": "Deals", "member_count": 10}]
        with patch("app.api.routes.telegram_auth._sidecar_get", new=AsyncMock(return_value=sidecar_channels)):
            resp = await client.get("/api/telegram/channels", headers=auth_headers)

        assert resp.status_code == 200
        assert resp.json()["count"] == 1
