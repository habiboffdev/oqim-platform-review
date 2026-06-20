from datetime import timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware.rate_limit import auth_limiter
from app.core.config import get_settings
from app.core.deps import get_current_workspace, get_db_session
from app.core.logging import get_logger
from app.core.security import (
    clear_auth_cookies,
    create_access_token,
    generate_csrf_token,
    hash_password,
    set_auth_cookies,
    verify_password,
)
from app.db.session import async_session
from app.models.agent import Agent
from app.models.telegram_auth_attempt import TelegramAuthAttempt
from app.models.workspace import Workspace
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.contracts import OnboardingLearningBootstrapInput
from app.modules.onboarding_learning.service import (
    OnboardingLearningBootstrapService,
    build_personalized_onboarding_profile,
    trust_mode_from_onboarding_preferences,
)
from app.modules.onboarding_learning.source_runtime import OnboardingSourceLearningRuntimeService
from app.modules.workspace_os.provisioner import WorkspaceOSProvisioner
from app.schemas.auth import (
    AuthResponse,
    AuthSessionProjection,
    BridgeLoginRequest,
    CompleteOnboardingRequest,
    LoginRequest,
    RegisterRequest,
)
from app.schemas.workspace import WorkspaceResponse, WorkspaceUpdate
from app.services.auth_session_projection import build_auth_session_projection

logger = get_logger("api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])
TEMP_SESSION_COOKIE = "oqim_tg_temp_session"

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]

JWT_EXPIRE_DAYS = 30


@router.post("/register", status_code=status.HTTP_201_CREATED)
@auth_limiter.limit("5/minute")
async def register(
    request: Request,
    body: RegisterRequest,
    session: SessionDep,
):
    existing = await session.execute(select(Workspace).where(Workspace.phone_number == body.phone_number))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phone number already registered",
        )

    workspace = Workspace(
        phone_number=body.phone_number,
        name=body.name,
        password_hash=hash_password(body.password),
    )
    session.add(workspace)
    await session.flush()

    # Customer Agent — replies to customers in seller's voice
    customer_agent = Agent(
        workspace_id=workspace.id,
        name=f"{body.name} AI",
        is_active=True,
        is_default=True,
        agent_type="customer",
        contact_scope="business",
        auto_send_threshold=0.70,
        trust_mode="disabled",
        persona={
            "role": "Sales assistant",
            "tone": "Friendly",
            "instructions": "Reply to customer messages in the seller's voice.",
        },
        tools_config={"enabled_tools": ["knowledge_search_catalog"]},
        knowledge_config={"use_catalog": True, "use_knowledge": True},
    )
    session.add(customer_agent)

    # Business Agent — answers seller's BI questions
    business_agent = Agent(
        workspace_id=workspace.id,
        name=f"{body.name} Yordamchi",
        is_active=True,
        is_default=False,
        agent_type="business",
        contact_scope="all",
        auto_send_threshold=0.0,
        trust_mode="disabled",
        persona={
            "role": "Business assistant",
            "tone": "Helpful",
            "instructions": "Answer the seller's business questions using their data.",
        },
        tools_config={"enabled_tools": []},
        knowledge_config={"use_catalog": True, "use_knowledge": True},
    )
    session.add(business_agent)
    await session.commit()
    await session.refresh(workspace)

    token = create_access_token(subject=str(workspace.id))
    csrf = generate_csrf_token()
    response = Response(
        content=AuthResponse.model_validate(workspace).model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )
    set_auth_cookies(response, token, csrf)
    return response


@router.post("/login")
@auth_limiter.limit("5/minute")
async def login(
    request: Request,
    body: LoginRequest,
    session: SessionDep,
):
    result = await session.execute(select(Workspace).where(Workspace.phone_number == body.phone_number))
    workspace = result.scalar_one_or_none()
    if not workspace or not verify_password(body.password, workspace.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    token = create_access_token(subject=str(workspace.id))
    csrf = generate_csrf_token()
    response = Response(
        content=AuthResponse.model_validate(workspace).model_dump_json(),
        media_type="application/json",
    )
    set_auth_cookies(response, token, csrf)
    return response


@router.post("/logout")
async def logout():
    response = Response(
        content='{"ok": true}',
        media_type="application/json",
    )
    clear_auth_cookies(response)
    return response


@router.get("/dev-login/{workspace_id}")
async def dev_login(workspace_id: int, session: SessionDep):
    """Dev-only: login as any workspace without password. NOT for production."""
    settings = get_settings()
    if settings.cookie_secure:
        raise HTTPException(status_code=404, detail="Not found")

    result = await session.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    token = create_access_token(subject=str(workspace.id))
    csrf = generate_csrf_token()
    from starlette.responses import RedirectResponse

    response = RedirectResponse(url="/", status_code=302)
    set_auth_cookies(response, token, csrf)
    return response


# ---------------------------------------------------------------------------
# Telegram-first auth
# ---------------------------------------------------------------------------


async def _get_or_create_workspace(
    session: AsyncSession, phone: str, telegram_user_id: int | None = None
) -> tuple[Workspace, bool]:
    """Find workspace by phone or create new one. Returns (workspace, is_new)."""

    async def _find_existing() -> Workspace | None:
        if telegram_user_id is not None:
            result = await session.execute(select(Workspace).where(Workspace.telegram_user_id == telegram_user_id))
            workspace = result.scalar_one_or_none()
            if workspace:
                return workspace

        result = await session.execute(select(Workspace).where(Workspace.phone_number == phone))
        return result.scalar_one_or_none()

    workspace = await _find_existing()
    if workspace:
        return workspace, False

    # Create new workspace — no password, Telegram is the auth
    workspace = Workspace(
        phone_number=phone,
        name="",  # filled later or auto-detected from Telegram
        telegram_connected=True,
        telegram_user_id=telegram_user_id,
    )
    session.add(workspace)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        workspace = await _find_existing()
        if workspace:
            logger.info(
                "Telegram auth reused existing workspace after duplicate insert race: phone=%s user_id=%s workspace_id=%d",
                phone,
                telegram_user_id,
                workspace.id,
            )
            return workspace, False
        raise

    # Create default agents
    customer_agent = Agent(
        workspace_id=workspace.id,
        name="AI Sotuvchi",
        is_active=True,
        is_default=True,
        agent_type="customer",
        contact_scope="business",
        auto_send_threshold=0.70,
        trust_mode="disabled",
        persona={"role": "Sales assistant", "tone": "Friendly"},
        tools_config={"enabled_tools": ["knowledge_search_catalog"]},
        knowledge_config={"use_catalog": True, "use_knowledge": True},
    )
    business_agent = Agent(
        workspace_id=workspace.id,
        name="Biznes Yordamchi",
        is_active=True,
        is_default=False,
        agent_type="business",
        contact_scope="all",
        auto_send_threshold=0.0,
        trust_mode="disabled",
        persona={"role": "Business assistant", "tone": "Helpful"},
        tools_config={"enabled_tools": []},
        knowledge_config={"use_catalog": True, "use_knowledge": True},
    )
    session.add_all([customer_agent, business_agent])
    await session.flush()
    logger.info("New workspace created via Telegram auth: id=%d phone=%s", workspace.id, phone)

    return workspace, True


def _make_auth_response(response: Response, workspace: Workspace) -> Response:
    """Set auth cookies and return AuthResponse."""
    token = create_access_token(
        subject=str(workspace.id),
        expires_delta=timedelta(days=JWT_EXPIRE_DAYS),
    )
    csrf = generate_csrf_token()
    set_auth_cookies(response, token, csrf)
    return response


def _clear_temp_session_cookie(response: Response) -> None:
    settings = get_settings()
    kwargs = {"path": "/"}
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    response.delete_cookie(TEMP_SESSION_COOKIE, **kwargs)


async def _register_sidecar_session(
    temp_session_id: str,
    workspace_id: int,
    user_id: str | None = None,
) -> dict:
    """Promote a temporary auth session into a permanent workspace session."""
    settings = get_settings()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.sidecar_api_key:
        headers["X-Sidecar-Key"] = settings.sidecar_api_key

    url = f"{settings.sidecar_url}/sessions/register"
    async with async_session() as lookup_session:
        auth_attempt = (
            await lookup_session.execute(
                select(TelegramAuthAttempt).where(TelegramAuthAttempt.temp_session_id == temp_session_id)
            )
        ).scalar_one_or_none()
    temp_session_data = auth_attempt.temp_session_data if auth_attempt else None
    delivery_payload = auth_attempt.delivery_payload if auth_attempt else {}
    auth_transport = delivery_payload.get("authTransport") if isinstance(delivery_payload, dict) else None
    auth_client_profile = delivery_payload.get("authClientProfile") if isinstance(delivery_payload, dict) else None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={
                    "workspaceId": workspace_id,
                    "tempSessionId": temp_session_id,
                    **({"tempSessionString": temp_session_data} if temp_session_data else {}),
                    **({"authTransport": auth_transport} if auth_transport else {}),
                    **({"authClientProfile": auth_client_profile} if auth_client_profile else {}),
                    **({"userId": user_id} if user_id else {}),
                },
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        logger.warning(
            "Failed to register sidecar session for workspace %d: %s",
            workspace_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Telegram session registration failed",
        ) from exc


async def _bind_bootstrap_sidecar_session(workspace_id: int, user_id: str | None = None) -> dict:
    """Bind bootstrap QR auth session into a permanent workspace session."""
    settings = get_settings()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.sidecar_api_key:
        headers["X-Sidecar-Key"] = settings.sidecar_api_key

    url = f"{settings.sidecar_url}/auth/bind-workspace"
    payload: dict[str, str | int] = {"workspaceId": workspace_id}
    if user_id:
        payload["userId"] = user_id

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        logger.warning(
            "Failed to bind bootstrap sidecar session for workspace %d: %s",
            workspace_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Telegram session binding failed",
        ) from exc


# Legacy channel-transport telegram/send-code, telegram/verify, and
# telegram/verify-2fa endpoints were removed. GramJS QR/temp-session auth now
# completes through /bridge-login.


# ---------------------------------------------------------------------------
# GramJS auth completion
# ---------------------------------------------------------------------------


@router.post("/bridge-login")
@auth_limiter.limit("10/minute")
async def bridge_login(
    request: Request,
    body: BridgeLoginRequest,
    session: SessionDep,
):
    """Complete GramJS Telegram login and create or return the workspace."""
    temp_session_id = (
        None
        if body.auth_method == "qr"
        else body.temp_session_id or request.cookies.get(TEMP_SESSION_COOKIE)
    )
    if not temp_session_id and body.auth_method != "qr":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tempSessionId is required",
        )

    workspace, is_new = await _get_or_create_workspace(
        session,
        body.phone,
        telegram_user_id=int(body.user_id) if body.user_id else None,
    )
    if is_new and body.first_name:
        workspace.name = body.first_name
    if body.user_id:
        workspace.telegram_user_id = int(body.user_id)
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)

    if temp_session_id:
        registration = await _register_sidecar_session(temp_session_id, workspace.id, body.user_id)
    else:
        registration = await _bind_bootstrap_sidecar_session(workspace.id, body.user_id)
    registered_user = registration.get("user") or {}
    registered_user_id = registered_user.get("userId") or body.user_id
    registered_first_name = registered_user.get("firstName") or body.first_name

    if is_new and registered_first_name:
        workspace.name = registered_first_name

    if registered_user_id:
        workspace.telegram_user_id = int(registered_user_id)
    workspace.telegram_connected = True
    session.add(workspace)

    await session.commit()

    resp_data = AuthResponse(
        id=workspace.id,
        phone_number=workspace.phone_number,
        name=workspace.name or "",
        telegram_connected=workspace.telegram_connected,
        onboarding_completed=workspace.onboarding_completed,
        is_new=is_new,
    )
    response = Response(
        content=resp_data.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_201_CREATED if is_new else status.HTTP_200_OK,
    )
    _make_auth_response(response, workspace)
    _clear_temp_session_cookie(response)
    return response


# ---------------------------------------------------------------------------
# Legacy auth (kept for backward compatibility, will be removed)
# ---------------------------------------------------------------------------


@router.post("/complete-onboarding")
async def complete_onboarding(
    workspace: WorkspaceDep,
    session: SessionDep,
    body: CompleteOnboardingRequest | None = None,
):
    provision_profile = workspace.onboarding_profile or {}
    provision_preferences: dict = {}
    if body:
        provision_preferences = body.preferences or {}
        if body.phone_number and body.phone_number != workspace.phone_number:
            existing = await session.execute(
                select(Workspace).where(
                    Workspace.phone_number == body.phone_number,
                    Workspace.id != workspace.id,
                )
            )
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Phone number already registered",
                )
            workspace.phone_number = body.phone_number

        if body.password:
            workspace.password_hash = hash_password(body.password)
        if body.name:
            workspace.name = body.name
        if body.category:
            workspace.type = body.category
        if body.monthly_revenue_band:
            workspace.monthly_revenue_band = body.monthly_revenue_band
        profile = build_personalized_onboarding_profile(
            business_profile=body.business_profile,
            preferences=body.preferences,
            sources=body.sources,
            owner_rules=body.owner_rules,
        )
        if profile:
            provision_profile = profile
            workspace.onboarding_profile = profile
            workspace.description = (
                str(body.business_profile.get("offer_summary")).strip()
                if body.business_profile and body.business_profile.get("offer_summary")
                else workspace.description
            )
            workspace.trust_mode = trust_mode_from_onboarding_preferences(body.preferences)
            bootstrap_result = await OnboardingLearningBootstrapService(
                repository=CommercialSpineRepository(session),
            ).seed_business_brain(
                OnboardingLearningBootstrapInput(
                    workspace_id=workspace.id,
                    profile=profile,
                    actor_ref=f"workspace:{workspace.id}",
                )
            )
            if bootstrap_result.queued_source_count:
                await OnboardingSourceLearningRuntimeService(
                    repository=CommercialSpineRepository(session),
                ).process_workspace_sources(
                    workspace_id=workspace.id,
                    correlation_id=f"complete-onboarding-source-learning:{workspace.id}",
                    limit=min(max(bootstrap_result.queued_source_count, 1), 10),
                    force=True,
                )

    # Provision agents/grants/triggers/skills only. The three-document content
    # (BUSINESS.md / AGENT.md / SKILL.md) comes from the workbench doc-gen
    # orchestrator, so legacy document provisioning is intentionally skipped.
    await WorkspaceOSProvisioner(session).provision(
        workspace=workspace,
        profile=provision_profile,
        preferences=provision_preferences,
        documents=False,
    )

    # First-launch choice: "start" activates the default agents immediately,
    # "later" leaves them provisioned but inactive until the owner turns them on.
    launch_mode = body.launch_mode if body else "start"
    activate = launch_mode != "later"
    default_agents = (
        await session.scalars(
            select(Agent).where(
                Agent.workspace_id == workspace.id,
                Agent.is_default.is_(True),
            )
        )
    ).all()
    for default_agent in default_agents:
        default_agent.is_active = activate
        session.add(default_agent)

    workspace.onboarding_completed = True
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)


@router.get("/me", response_model=WorkspaceResponse)
async def get_me(
    workspace: WorkspaceDep,
):
    return WorkspaceResponse.model_validate(workspace)


@router.get("/session", response_model=AuthSessionProjection)
async def get_auth_session_projection(
    workspace: WorkspaceDep,
):
    return build_auth_session_projection(workspace)


@router.patch("/workspace", response_model=WorkspaceResponse)
async def update_workspace(
    body: WorkspaceUpdate,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(workspace, field, value)
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)
