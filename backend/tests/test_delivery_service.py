"""Tests for DeliveryService — unified message delivery with typing + delay + retry."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.delivery_runtime import DeliveryRuntime
from app.models.workspace import Workspace
from app.modules.commercial_spine.contracts import BusinessBrainProjection
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.services.channel_adapter_contract import ChannelOutboundMedia
from app.services.delivery import DeliveryService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(
    sidecar_url: str = "http://localhost:3100",
    sidecar_api_key: str = "test-key",
    *,
    event_spine=None,
) -> DeliveryService:
    return DeliveryService(
        sidecar_url=sidecar_url,
        sidecar_api_key=sidecar_api_key,
        event_spine=event_spine,
    )


async def _create_conversation(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    *,
    telegram_chat_id: int | None = 123456789,
) -> Conversation:
    conv = Conversation(
        workspace_id=workspace.id,
        customer_id=customer.id,
        telegram_chat_id=telegram_chat_id,
        channel="telegram_dm",
    )
    db_session.add(conv)
    await db_session.flush()
    return conv


def _mock_httpx_response(status_code: int = 200, json_data: dict | None = None):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = str(json_data)
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


# ---------------------------------------------------------------------------
# TestDeliverMessageHappyPath
# ---------------------------------------------------------------------------


class TestDeliverMessageHappyPath:
    """deliver_message() happy path: typing + read + delay + send all succeed."""

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_happy_path_returns_success(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """deliver_message returns success=True with external_message_id."""
        conv = await _create_conversation(db_session, workspace, customer)

        # Mock /read, /typing, /send, then typing cancellation.
        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        send_resp = _mock_httpx_response(200, {"externalMessageId": "tg_42"})
        cancel_typing_resp = _mock_httpx_response(200, {"ok": True, "typing": False})
        mock_post.side_effect = [read_resp, typing_resp, send_resp, cancel_typing_resp]

        service = _make_service()
        result = await service.deliver_message(
            conv.id, "Salom!", db=db_session, workspace_id=workspace.id,
        )

        assert result.success is True
        assert result.external_message_id == "tg_42"
        assert result.error is None
        assert mock_sleep.await_count == 1  # human delay
        assert mock_post.await_args_list[-1].args[0] == "http://localhost:3100/typing"
        assert mock_post.await_args_list[-1].kwargs["json"]["typing"] is False

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_explicit_pacing_can_skip_typing_reopen_and_keep_online_tail(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        conv = await _create_conversation(db_session, workspace, customer)

        read_resp = _mock_httpx_response(200, {"ok": True})
        send_resp = _mock_httpx_response(200, {"externalMessageId": "tg_43"})
        cancel_typing_resp = _mock_httpx_response(200, {"ok": True, "typing": False})
        online_resp = _mock_httpx_response(200, {"ok": True})
        mock_post.side_effect = [read_resp, send_resp, cancel_typing_resp, online_resp]

        service = _make_service()
        result = await service.deliver_message(
            conv.id,
            "Salom!",
            db=db_session,
            workspace_id=workspace.id,
            delay_override_seconds=0.26,
            typing_indicator=False,
            online_tail_seconds=1.5,
        )

        assert result.success is True
        assert [call.args[0] for call in mock_post.await_args_list] == [
            "http://localhost:3100/read",
            "http://localhost:3100/send",
            "http://localhost:3100/typing",
            "http://localhost:3100/online",
        ]
        assert mock_post.await_args_list[2].kwargs["json"]["typing"] is False
        assert mock_sleep.await_args_list[0].args[0] == 0.26
        assert mock_sleep.await_args_list[1].args[0] == 1.5

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_happy_path_returns_external_message_id(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """deliver_message returns the external_message_id from sidecar."""
        conv = await _create_conversation(db_session, workspace, customer)

        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        send_resp = _mock_httpx_response(200, {"externalMessageId": "tg_42"})
        mock_post.side_effect = [read_resp, typing_resp, send_resp]

        service = _make_service()
        result = await service.deliver_message(
            conv.id, "Reply text", db=db_session, workspace_id=workspace.id,
        )

        assert result.success is True
        assert result.external_message_id == "tg_42"
        assert result.error is None

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_deliver_message_forwards_reply_to_message_id(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        conv = await _create_conversation(db_session, workspace, customer)

        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        send_resp = _mock_httpx_response(200, {"externalMessageId": "tg_reply"})
        mock_post.side_effect = [read_resp, typing_resp, send_resp]

        service = _make_service()
        result = await service.deliver_message(
            conv.id,
            "Reply text",
            db=db_session,
            workspace_id=workspace.id,
            reply_to_message_id=1444,
        )

        assert result.success is True
        assert result.external_message_id == "tg_reply"
        send_payload = mock_post.await_args_list[2].kwargs["json"]
        assert send_payload["replyToMsgId"] == 1444

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_post_accept_event_spine_failure_does_not_fail_send(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """Once Telegram accepts a send, local bookkeeping failures must not become send failures."""
        conv = await _create_conversation(db_session, workspace, customer)
        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        send_resp = _mock_httpx_response(200, {"externalMessageId": "tg_accepted"})
        mock_post.side_effect = [read_resp, typing_resp, send_resp]
        event_spine = MagicMock()
        event_spine.publish.side_effect = [
            None,
            RuntimeError("confirmed event persist failed"),
        ]

        service = _make_service(event_spine=event_spine)
        result = await service.deliver_message(
            conv.id,
            "Provider already accepted this",
            db=db_session,
            workspace_id=workspace.id,
            client_idempotency_key="accepted-send-key",
        )

        assert result.success is True
        assert result.external_message_id == "tg_accepted"
        assert result.state == "confirmed"

        runtime = await db_session.scalar(
            select(DeliveryRuntime).where(
                DeliveryRuntime.workspace_id == workspace.id,
                DeliveryRuntime.client_idempotency_key == "accepted-send-key",
            )
        )
        assert runtime is not None
        assert runtime.state == "confirmed"
        assert runtime.external_message_id == "tg_accepted"


# ---------------------------------------------------------------------------
# TestDeliverMedia
# ---------------------------------------------------------------------------


class TestDeliverMedia:
    """deliver_media() records delivery state and sends a typed media payload."""

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_deliver_media_records_confirmed_runtime_and_sends_payload(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        conv = await _create_conversation(db_session, workspace, customer)
        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        send_resp = _mock_httpx_response(200, {"externalMessageId": "tg_media_42"})
        mock_post.side_effect = [read_resp, typing_resp, send_resp]

        service = _make_service()
        media = ChannelOutboundMedia(
            url="https://cdn.example.com/catalog/ring.jpg",
            media_type="photo",
            mime_type="image/jpeg",
            file_name="ring.jpg",
            asset_id="asset-ring-1",
        )
        result = await service.deliver_media(
            conv.id,
            media,
            caption="Mana rasmi",
            db=db_session,
            workspace_id=workspace.id,
            client_idempotency_key="catalog-media-send-1",
        )

        assert result.success is True
        assert result.external_message_id == "tg_media_42"
        assert result.state == "confirmed"
        assert mock_sleep.await_count == 1

        send_call = next(
            call
            for call in mock_post.await_args_list
            if call.args[0] == "http://localhost:3100/send"
        )
        assert send_call.kwargs["json"] == {
            "workspaceId": workspace.id,
            "chatId": str(conv.telegram_chat_id),
            "caption": "Mana rasmi",
            "media": {
                "url": "https://cdn.example.com/catalog/ring.jpg",
                "mediaType": "photo",
                "mimeType": "image/jpeg",
                "fileName": "ring.jpg",
                "assetId": "asset-ring-1",
            },
            "idempotencyKey": "catalog-media-send-1",
        }

        runtime = await db_session.scalar(
            select(DeliveryRuntime).where(
                DeliveryRuntime.workspace_id == workspace.id,
                DeliveryRuntime.client_idempotency_key == "catalog-media-send-1",
            )
        )
        assert runtime is not None
        assert runtime.conversation_id == conv.id
        assert runtime.state == "confirmed"
        assert runtime.attempt_count == 1
        assert runtime.external_message_id == "tg_media_42"

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_deliver_media_timeout_records_unknown_runtime(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        conv = await _create_conversation(db_session, workspace, customer)
        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        mock_post.side_effect = [
            read_resp,
            typing_resp,
            httpx.TimeoutException("sidecar did not answer"),
        ]

        service = _make_service()
        result = await service.deliver_media(
            conv.id,
            ChannelOutboundMedia(
                url="https://cdn.example.com/catalog/watch.jpg",
                media_type="photo",
                asset_id="asset-watch-1",
            ),
            caption="Mana shu model",
            db=db_session,
            workspace_id=workspace.id,
            client_idempotency_key="catalog-media-timeout-1",
        )

        assert result.success is False
        assert result.state == "unknown"
        assert "sidecar did not answer" in (result.error or "")
        assert mock_sleep.await_count == 1

        runtime = await db_session.scalar(
            select(DeliveryRuntime).where(
                DeliveryRuntime.workspace_id == workspace.id,
                DeliveryRuntime.client_idempotency_key == "catalog-media-timeout-1",
            )
        )
        assert runtime is not None
        assert runtime.state == "unknown"
        assert runtime.attempt_count == 1
        assert runtime.external_message_id is None
        assert runtime.last_error == "sidecar did not answer"

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_deliver_media_permanent_error_not_retried(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """A 422 vault_document_unavailable is terminal: one send, no retry burn."""
        conv = await _create_conversation(db_session, workspace, customer)
        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        # /send returns 422 exactly ONCE — a retry would exhaust side_effect and
        # raise StopIteration, so this also proves no retry happened.
        send_422 = _mock_httpx_response(422, {"error": "vault_document_unavailable"})
        mock_post.side_effect = [read_resp, typing_resp, send_422]

        service = _make_service()
        result = await service.deliver_media(
            conv.id,
            ChannelOutboundMedia(
                url="vault://-100123/42",
                media_type="video",
                vault_peer="-100123",
                vault_message_id=42,
            ),
            caption="x",
            db=db_session,
            workspace_id=workspace.id,
            client_idempotency_key="catalog-media-permanent-1",
        )

        assert result.success is False
        assert result.state == "failed"
        # Exactly one /send call — no retry budget burned on a gone document.
        send_calls = [
            call
            for call in mock_post.await_args_list
            if call.args[0] == "http://localhost:3100/send"
        ]
        assert len(send_calls) == 1

        runtime = await db_session.scalar(
            select(DeliveryRuntime).where(
                DeliveryRuntime.workspace_id == workspace.id,
                DeliveryRuntime.client_idempotency_key == "catalog-media-permanent-1",
            )
        )
        assert runtime is not None
        assert runtime.state == "failed"
        assert runtime.attempt_count == 1


# ---------------------------------------------------------------------------
# TestDeliverMessageRetry
# ---------------------------------------------------------------------------


class TestDeliverMessageRetry:
    """Tests for 3-retry backoff on /send failures."""

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_send_failure_after_retries_returns_failure(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """If /send fails 3 times, returns success=False."""
        conv = await _create_conversation(db_session, workspace, customer)

        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        send_error = _mock_httpx_response(502, {"error": "Telegram send failed"})
        # /read, /typing succeed; /send fails 3 times
        mock_post.side_effect = [read_resp, typing_resp, send_error, send_error, send_error]

        service = _make_service()
        result = await service.deliver_message(
            conv.id, "Will fail", db=db_session, workspace_id=workspace.id,
        )

        assert result.success is False
        assert result.error is not None

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_send_failure_returns_error_message(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """If /send fails 3 times, returns error message in result."""
        conv = await _create_conversation(db_session, workspace, customer)

        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        send_error = _mock_httpx_response(502, {"error": "fail"})
        mock_post.side_effect = [read_resp, typing_resp, send_error, send_error, send_error]

        service = _make_service()
        result = await service.deliver_message(
            conv.id, "Will fail", db=db_session, workspace_id=workspace.id,
        )

        assert result.success is False
        assert result.error is not None
        assert result.external_message_id is None


# ---------------------------------------------------------------------------
# TestFireAndForget
# ---------------------------------------------------------------------------


class TestFireAndForget:
    """Typing/read failures should not prevent message delivery."""

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_typing_failure_still_sends(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """If /typing fails, /send still succeeds."""
        conv = await _create_conversation(db_session, workspace, customer)

        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_error = httpx.ConnectError("Connection refused")
        send_resp = _mock_httpx_response(200, {"externalMessageId": "tg_99"})

        call_count = 0
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return read_resp
            elif call_count == 2:
                raise typing_error
            else:
                return send_resp

        mock_post.side_effect = side_effect

        service = _make_service()
        result = await service.deliver_message(
            conv.id, "Should still work", db=db_session, workspace_id=workspace.id,
        )

        assert result.success is True
        assert result.external_message_id == "tg_99"

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_read_failure_still_sends(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """If /read fails, /send still succeeds."""
        conv = await _create_conversation(db_session, workspace, customer)

        read_error = httpx.ConnectError("Connection refused")
        typing_resp = _mock_httpx_response(200, {"ok": True})
        send_resp = _mock_httpx_response(200, {"externalMessageId": "tg_88"})

        call_count = 0
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise read_error
            elif call_count == 2:
                return typing_resp
            else:
                return send_resp

        mock_post.side_effect = side_effect

        service = _make_service()
        result = await service.deliver_message(
            conv.id, "Should still work", db=db_session, workspace_id=workspace.id,
        )

        assert result.success is True
        assert result.external_message_id == "tg_88"


# ---------------------------------------------------------------------------
# TestConversationResolution
# ---------------------------------------------------------------------------


class TestConversationResolution:
    """Edge cases around conversation lookup."""

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    async def test_missing_conversation_returns_failure(
        self,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        """If conversation does not exist, returns failure immediately."""
        service = _make_service()
        result = await service.deliver_message(
            99999, "No such conv", db=db_session, workspace_id=workspace.id,
        )

        assert result.success is False
        assert "not found" in (result.error or "").lower()

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_no_telegram_chat_id_returns_failure(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """If conversation has no telegram_chat_id, returns failure."""
        conv = await _create_conversation(
            db_session, workspace, customer, telegram_chat_id=None,
        )

        service = _make_service()
        result = await service.deliver_message(
            conv.id, "No TG ID", db=db_session, workspace_id=workspace.id,
        )

        assert result.success is False
        assert "no telegram_chat_id" in (result.error or "").lower()
        # Should not have called sidecar at all
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# TestBusinessBrainVoiceDelay
# ---------------------------------------------------------------------------


class TestBusinessBrainVoiceDelay:
    """Tests for human delay from Business Brain voice projection."""

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_uses_business_brain_voice_projection_delay(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """Business Brain voice projection owns delay_range."""
        conv = await _create_conversation(db_session, workspace, customer)
        await CommercialSpineRepository(db_session).upsert_projection(
            BusinessBrainProjection(
                projection_ref="voice_profile:seller_voice",
                workspace_id=workspace.id,
                projection_type="voice_profile",
                entity_ref="seller_voice",
                state={
                    "traits": [
                        {
                            "message_count_analyzed": 6,
                            "delay_range": {"min_ms": 300, "max_ms": 400},
                        }
                    ],
                    "excluded_fact_ids": [],
                },
                source_refs=["fact:voice_profile:test"],
            )
        )
        await db_session.flush()

        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        send_resp = _mock_httpx_response(200, {"externalMessageId": "tg_1"})
        mock_post.side_effect = [read_resp, typing_resp, send_resp]

        service = _make_service()
        result = await service.deliver_message(
            conv.id, "Test", db=db_session, workspace_id=workspace.id,
        )

        assert result.success is True
        mock_sleep.assert_awaited_once()
        delay_value = mock_sleep.call_args[0][0]
        assert 0.3 <= delay_value <= 0.4

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_default_delay_without_business_brain_voice_signal(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """Without a Business Brain voice delay signal, uses default 1.5-3s delay."""
        conv = await _create_conversation(db_session, workspace, customer)

        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        send_resp = _mock_httpx_response(200, {"externalMessageId": "tg_1"})
        mock_post.side_effect = [read_resp, typing_resp, send_resp]

        service = _make_service()
        result = await service.deliver_message(
            conv.id, "Test", db=db_session, workspace_id=workspace.id,
        )

        assert result.success is True
        mock_sleep.assert_awaited_once()
        delay_value = mock_sleep.call_args[0][0]
        assert 1.5 <= delay_value <= 3.0

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_deliver_message_uses_caller_delay_override(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        conv = await _create_conversation(db_session, workspace, customer)
        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        send_resp = _mock_httpx_response(200, {"externalMessageId": "tg_override"})
        mock_post.side_effect = [read_resp, typing_resp, send_resp]

        service = _make_service()
        result = await service.deliver_message(
            conv.id,
            "Salom",
            db=db_session,
            workspace_id=workspace.id,
            client_idempotency_key="delay-override",
            delay_override_seconds=0.2,
        )

        assert result.success is True
        mock_sleep.assert_awaited_once_with(0.2)


# ---------------------------------------------------------------------------
# TestRateLimitSurfacing
# ---------------------------------------------------------------------------


class TestRateLimitSurfacing:
    """Sidecar 429 (FloodWait) must surface retry_after_seconds on DeliveryResult."""

    @patch("app.services.delivery.asyncio.sleep", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    async def test_send_rate_limit_surfaces_retry_after(
        self,
        mock_post: AsyncMock,
        mock_sleep: AsyncMock,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        """Sidecar 429 (FloodWait) must surface retry_after_seconds, not just a string."""
        conv = await _create_conversation(db_session, workspace, customer)
        read_resp = _mock_httpx_response(200, {"ok": True})
        typing_resp = _mock_httpx_response(200, {"ok": True})
        rate_resp = _mock_httpx_response(429, {"retryAfter": 30})
        mock_post.side_effect = [read_resp, typing_resp, rate_resp]

        service = _make_service()
        result = await service.deliver_message(
            conv.id, "Paced send", db=db_session, workspace_id=workspace.id,
        )

        assert result.success is False
        assert result.retry_after_seconds == 30.0
