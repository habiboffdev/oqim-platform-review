"""amoCRM webhook intake (CRM -> OQIM).

amoCRM CRM-entity / Digital-Pipeline webhooks (lead status moved, responsible
reassigned, note added — the events OQIM subscribes to) are **UNSIGNED**:
``application/x-www-form-urlencoded`` bodies with NO X-Signature/HMAC header. The
HMAC-SHA1 ``X-Signature`` scheme belongs to the SEPARATE Chats API channel
webhooks (keyed with the per-channel secret, NOT the integration client_secret).
An earlier build wrongly required that signature and 401'd every real card-move,
so the human-touch latch never applied. Do NOT re-add a signature gate.

Verification here is: an unguessable ``webhook_token`` in the URL (404 on
unknown — never leak existence) plus a soft account binding (subdomain must match
the connection). The effect is fail-safe and DB-only (latch
``stage_authority='human'`` + record the observed stage — never a command, never
a CRM write, never a send). We always ACK 200 once the token resolves — amoCRM
disables a webhook after sustained non-2xx responses, so a poison event must not
cost us the subscription.
"""
from __future__ import annotations

from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db_session
from app.core.logging import get_logger
from app.models.crm_connection import CrmConnection
from app.modules.crm_connector.factory import provider_for
from app.modules.crm_connector.webhook_service import CrmWebhookService

router = APIRouter(prefix="/webhook/amocrm", tags=["webhook-amocrm"])
logger = get_logger("api.webhook_amocrm")

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.post("/{webhook_token}")
async def webhook_amocrm(
    webhook_token: str, request: Request, session: SessionDep
) -> dict:
    raw = await request.body()
    conn = (
        await session.execute(
            select(CrmConnection).where(
                CrmConnection.webhook_token == webhook_token,
                # Resolve by the unguessable token across active AND degraded — a
                # degraded connection (auth-dead refresh, recoverable) must still
                # ACK + apply, or repeated 404s make amoCRM DISABLE the webhook
                # (the exact failure the always-ACK design exists to prevent). The
                # latch is DB-only, so no token is needed to apply it.
                CrmConnection.status.in_(("active", "degraded")),
            ).limit(1)
        )
    ).scalars().first()
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown_webhook")

    form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
    batch = provider_for(conn.provider).parse_webhook(form)

    # Diagnostic: KEY NAMES + shape ONLY — never the webhook_token, note text, or
    # any form VALUES. has_x_signature confirms (live) amoCRM CRM webhooks are
    # unsigned; the rest helps verify the parser sees the real wire shape.
    logger.info(
        "amocrm webhook connection_id=%s workspace_id=%s has_x_signature=%s "
        "account_subdomain=%s form_keys=%s event_kinds=%s",
        conn.id,
        conn.workspace_id,
        "X-Signature" in request.headers,
        batch.account_subdomain,
        sorted(form.keys()),
        [e.kind for e in batch.events],
    )

    if batch.account_subdomain and batch.account_subdomain != conn.provider_account_ref:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account_mismatch")

    try:
        await CrmWebhookService(session).apply(connection_id=conn.id, batch=batch)
        await session.commit()
    except Exception:  # never fail the ACK — protect the subscription
        logger.warning("amocrm webhook apply failed", exc_info=True)
    return {"status": "ok"}
