"""Instagram webhook intake (Meta-pushed events).

Auth model: Meta signs each POST with X-Hub-Signature-256 (HMAC-SHA256 of the
raw body with the app secret). GET handles the one-time hub.challenge verify.
Always ACK with 200 once the signature is valid — Meta retries non-200s and
disables subscriptions after sustained failures, so one poison entry must
never fail the batch.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

import redis.exceptions
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.webhook import _observe_event_spine_workspace
from app.core.config import Settings
from app.core.correlation import current_correlation_id
from app.core.deps import get_conversation_turn_runner, get_db_session, get_settings_dep
from app.core.logging import get_logger
from app.models.workspace import Workspace
from app.modules.channel_runtime.instagram_comment_dm import InstagramCommentDmService
from app.services.channel_conversation_sync import ChannelConversationSync
from app.services.instagram_channel_adapter import InstagramChannelAdapter

if TYPE_CHECKING:
    from app.modules.conversation_turns.runner import ConversationTurnRunner

router = APIRouter(prefix="/webhook/instagram", tags=["webhook-instagram"])
logger = get_logger("api.webhook_instagram")

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
ConversationTurnRunnerDep = Annotated["ConversationTurnRunner", Depends(get_conversation_turn_runner)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


@router.get("")
async def webhook_instagram_verify(
    settings: SettingsDep,
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> PlainTextResponse:
    expected = settings.instagram_webhook_verify_token
    if (
        not expected
        or hub_mode != "subscribe"
        or not hmac.compare_digest(hub_verify_token.encode("utf-8"), expected.encode("utf-8"))
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify_token_mismatch")
    return PlainTextResponse(hub_challenge)


def _verify_signature(body: bytes, signature_header: str, app_secret: str) -> bool:
    if not app_secret or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header.removeprefix("sha256="), expected)


@router.post("")
async def webhook_instagram(
    request: Request,
    session: SessionDep,
    conversation_turn_runner: ConversationTurnRunnerDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(body, signature, settings.instagram_app_secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid_signature")

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_json") from exc

    received_at = datetime.now(UTC).timestamp()
    sync = ChannelConversationSync()
    ingested = 0
    skipped = 0
    failed = 0
    for entry in payload.get("entry") or []:
        entry_id = str(entry.get("id") or "")
        try:
            # Match either stored id: Meta's webhook entry.id may be the account
            # `id` or the `user_id` and their docs don't say which. The empty
            # guard stops a malformed (id-less) entry from matching a workspace
            # whose other id column is NULL.
            workspace = None
            if entry_id:
                workspace = (
                    (
                        await session.execute(
                            select(Workspace)
                            .where(
                                or_(
                                    Workspace.instagram_page_id == entry_id,
                                    Workspace.instagram_account_id == entry_id,
                                )
                            )
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
            if workspace is None:
                skipped += 1
                logger.warning("instagram webhook entry for unknown page_id=%s skipped", entry_id)
                continue
            await _observe_event_spine_workspace(request, workspace.id)

            adapter = InstagramChannelAdapter(account_id=entry_id)
            messages = await adapter.receive_events({"workspaceId": workspace.id, "entry": entry})
            for message in messages:
                event = message.to_event(correlation_id=current_correlation_id())
                await request.app.state.event_spine.append(event)
                ingested += 1
                if not settings.is_event_spine_authoritative():
                    await sync.ingest_event(
                        raw_payload={
                            **message.to_bridge_payload(),
                            "backend_webhook_received_at": received_at,
                        },
                        workspace=workspace,
                        session=session,
                        conversation_turn_runner=conversation_turn_runner,
                        channel="instagram_dm",
                    )

            for change in entry.get("changes") or []:
                if change.get("field") != "comments":
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                result = await InstagramCommentDmService(session).handle_comment(
                    workspace=workspace, value=value
                )
                logger.info(
                    "instagram comment handled workspace=%s sent=%s reason=%s",
                    workspace.id,
                    result.sent,
                    result.skipped_reason,
                )
        except (
            OperationalError,
            InterfaceError,
            redis.exceptions.ConnectionError,
            redis.exceptions.TimeoutError,
        ):
            # Infra outage: let it 500 — Meta retries with backoff and the
            # spine idempotency key makes redelivery safe. Swallowing this
            # would silently drop customer messages (never-drop rule).
            raise
        except Exception:
            failed += 1
            logger.exception(
                "instagram webhook entry processing failed page_id=%s", entry_id
            )
            continue

    return {"status": "accepted", "ingested": ingested, "skipped": skipped, "failed": failed}
