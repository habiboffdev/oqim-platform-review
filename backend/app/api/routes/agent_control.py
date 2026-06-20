from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_current_workspace, get_db_session
from app.models.workspace import Workspace
from app.modules.agent_control.audit import AgentControlAuditService
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.knowledge_mcp.service import KnowledgeMCPService
from app.modules.telegram_control_bot.service import (
    DisabledTelegramControlBotClient,
    HermesTelegramBotGatewayClient,
    TelegramControlBotService,
)

router = APIRouter(prefix="/agent-control", tags=["agent-control"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def _telegram_bot_client() -> HermesTelegramBotGatewayClient | DisabledTelegramControlBotClient:
    token = get_settings().telegram_control_bot_token
    if not token:
        return DisabledTelegramControlBotClient()
    return HermesTelegramBotGatewayClient(token=token)


@router.get("/telegram/proposals/{proposal_id}/card")
async def preview_telegram_control_card(
    proposal_id: str,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    proposal = await CommercialSpineRepository(session).get_action_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal_id,
    )
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="action_proposal_not_found",
        )
    card = await TelegramControlBotService(session=session).approval_card(proposal)
    return card.model_dump(mode="json")


@router.get("/knowledge/stats")
async def knowledge_mcp_stats(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    personal_owner_id = (
        f"user:{workspace.telegram_user_id}"
        if workspace.telegram_user_id
        else f"workspace-user:{workspace.id}"
    )
    return await KnowledgeMCPService(session).stats(
        workspace_id=workspace.id,
        personal_owner_id=personal_owner_id,
    )


@router.get("/runs/{run_id}/audit")
async def agent_control_run_audit(
    run_id: str,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        return await AgentControlAuditService(session).run_audit(
            workspace_id=workspace.id,
            run_id=run_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/owner-bot")
async def owner_bot_status(
    workspace: WorkspaceDep,
) -> dict[str, Any]:
    """Status of this workspace's owner control bot (self-provisioned)."""
    username = workspace.control_bot_username
    return {
        "provisioned": bool(workspace.control_bot_token),
        "bot_username": username,
        "deep_link": f"https://t.me/{username}" if username else None,
        "owner_chat_bound": workspace.owner_control_chat_id is not None,
    }


@router.post("/owner-bot/provision")
async def provision_owner_bot(
    workspace: WorkspaceDep,
    session: SessionDep,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create + set up this workspace's owner control bot, fully automated.

    OQIM runs the BotFather conversation through the workspace's connected
    Telegram account (/newbot, name, username), stores the token, polishes
    name/description/about via the Bot API, optionally sets the profile photo
    (``pfp_url``), /start-s the bot from the userbot account, and binds the
    owner chat. The token is never returned.
    """
    from app.modules.telegram_control_bot.provisioner import build_workspace_provisioner

    body = body or {}
    provisioner = build_workspace_provisioner(workspace_id=workspace.id)
    db_workspace = await session.get(Workspace, workspace.id)
    if db_workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace_not_found")
    try:
        result = await provisioner.provision(
            workspace=db_workspace,
            display_name=body.get("name"),
            username=body.get("username"),
            description=body.get("description"),
            short_description=body.get("short_description"),
            pfp_url=body.get("pfp_url"),
            force=bool(body.get("force")),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except (TimeoutError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"botfather_provisioning_failed: {exc}",
        ) from exc
    await session.commit()
    # Provisioning the control bot is the owner saying "I want to run this by chat":
    # create the Owner Agent now (idempotent, one per workspace).
    from app.modules.agent_runtime_v2.owner_agent import ensure_owner_agent

    await ensure_owner_agent(session, workspace.id)
    await session.commit()
    return {
        "bot_username": result.bot_username,
        "deep_link": f"https://t.me/{result.bot_username}",
        "owner_chat_bound": result.owner_chat_bound,
        "polished": result.polished,
        "pfp_set": result.pfp_set,
        "transcript": result.transcript,
    }


async def _bot_get_me(token: str) -> dict[str, Any] | None:
    """Validate a pasted bot token via the Bot API getMe; return its result
    ({id, username, ...}) or None on any failure."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(f"https://api.telegram.org/bot{token}/getMe")
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data["result"] if data.get("ok") else None
    except (httpx.HTTPError, KeyError, ValueError):
        return None


@router.post("/owner-bot/bind-link")
async def owner_bot_bind_link(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Mint a one-time deep-link bind token for the owner to tap (#451)."""
    from app.modules.telegram_control_bot.bind_token_service import BindTokenService

    if not workspace.control_bot_username:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="control_bot_not_provisioned"
        )
    token = await BindTokenService(session).mint(workspace_id=workspace.id)
    await session.commit()
    return {
        "deep_link": f"https://t.me/{workspace.control_bot_username}?start={token}",
    }


@router.post("/owner-bot/token")
async def owner_bot_manual_token(
    workspace: WorkspaceDep,
    session: SessionDep,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Manual fallback: validate a pasted bot token via getMe, dedup, store."""
    token = str((body or {}).get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=422, detail="token_required")
    me = await _bot_get_me(token)
    if me is None:
        raise HTTPException(status_code=422, detail="invalid_bot_token")
    dup = (
        await session.execute(
            select(Workspace).where(
                Workspace.control_bot_token == token, Workspace.id != workspace.id
            )
        )
    ).scalars().first()
    if dup is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="token_in_use")
    db_workspace = await session.get(Workspace, workspace.id)
    db_workspace.control_bot_token = token
    db_workspace.control_bot_username = me["username"]
    db_workspace.control_bot_user_id = int(me["id"])
    await session.commit()
    return {"bot_username": me["username"]}


@router.post("/owner-bot/unbind")
async def owner_bot_unbind(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Clear the bound owner chat + invalidate outstanding tokens (#451)."""
    from app.modules.telegram_control_bot.bind_token_service import BindTokenService

    await BindTokenService(session).unbind(workspace_id=workspace.id)
    await session.commit()
    return {"ok": True}


@router.post("/telegram/webhook")
async def telegram_control_bot_webhook(
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    settings = get_settings()
    expected_secret = settings.telegram_control_bot_secret_token
    if expected_secret:
        actual_secret = request.headers.get("x-telegram-bot-api-secret-token")
        if actual_secret != expected_secret:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="telegram_control_bot_secret_mismatch",
            )
    update = await request.json()
    try:
        result = await TelegramControlBotService(
            session=session,
            client=_telegram_bot_client(),
        ).handle_update(update)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    await session.commit()
    return result.model_dump(mode="json")
