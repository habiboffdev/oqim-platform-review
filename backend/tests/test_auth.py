"""
Auth endpoint tests — register, login, me, workspace update.
Tests validation, duplicate prevention, auth guards, password verification,
and workspace isolation.
"""

from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, verify_password
from app.models.agent import Agent
from app.models.agent_document import AgentDocumentSection
from app.models.workspace import Workspace


class TestRegister:
    async def test_register_success(self, client: AsyncClient):
        res = await client.post("/api/auth/register", json={
            "phone_number": "+998991112233",
            "name": "Yangi Do'kon",
            "password": "securepass123",
        })
        assert res.status_code == 201
        data = res.json()
        assert data["phone_number"] == "+998991112233"
        assert data["name"] == "Yangi Do'kon"
        assert "access_token" not in data

    async def test_register_creates_default_agent(self, client: AsyncClient):
        res = await client.post("/api/auth/register", json={
            "phone_number": "+998992223344",
            "name": "Agent Test",
            "password": "securepass123",
        })
        assert res.status_code == 201
        workspace_id = res.json()["id"]

        token = create_access_token(subject=str(workspace_id))
        agents = await client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert agents.status_code == 200
        agent_list = agents.json()
        assert len(agent_list) >= 1
        assert agent_list[0]["is_default"] is True

    async def test_register_creates_two_agents(self, client: AsyncClient):
        """Registration must create exactly two agents: customer + business."""
        res = await client.post("/api/auth/register", json={
            "phone_number": "+998994445566",
            "name": "Two Agent Test",
            "password": "securepass123",
        })
        assert res.status_code == 201
        workspace_id = res.json()["id"]

        token = create_access_token(subject=str(workspace_id))
        agents_res = await client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert agents_res.status_code == 200
        agent_list = agents_res.json()

        assert len(agent_list) == 2

        types = {a["agent_type"] for a in agent_list}
        assert types == {"customer", "business"}

        customer_agent = next(a for a in agent_list if a["agent_type"] == "customer")
        business_agent = next(a for a in agent_list if a["agent_type"] == "business")

        assert customer_agent["is_default"] is True
        assert customer_agent["contact_scope"] == "business"
        assert "undo_delay_seconds" not in customer_agent
        assert customer_agent["tools_config"]["enabled_tools"] == ["knowledge_search_catalog"]

        assert business_agent["is_default"] is False
        assert business_agent["contact_scope"] == "all"
        assert "undo_delay_seconds" not in business_agent
        assert business_agent["tools_config"]["enabled_tools"] == []

    async def test_register_duplicate_phone_409(self, client: AsyncClient, workspace: Workspace):
        res = await client.post("/api/auth/register", json={
            "phone_number": workspace.phone_number,
            "name": "Duplicate",
            "password": "securepass123",
        })
        assert res.status_code == 409
        assert "already registered" in res.json()["detail"]

    async def test_register_invalid_phone_format(self, client: AsyncClient):
        res = await client.post("/api/auth/register", json={
            "phone_number": "12345",
            "name": "Bad Phone",
            "password": "securepass123",
        })
        assert res.status_code == 422

    async def test_register_short_name(self, client: AsyncClient):
        res = await client.post("/api/auth/register", json={
            "phone_number": "+998990000001",
            "name": "A",
            "password": "securepass123",
        })
        assert res.status_code == 422

    async def test_register_short_password(self, client: AsyncClient):
        res = await client.post("/api/auth/register", json={
            "phone_number": "+998991112244",
            "name": "Short Pass",
            "password": "short",
        })
        assert res.status_code == 422

    async def test_register_missing_password(self, client: AsyncClient):
        res = await client.post("/api/auth/register", json={
            "phone_number": "+998991112255",
            "name": "No Pass",
        })
        assert res.status_code == 422

    async def test_register_empty_body(self, client: AsyncClient):
        res = await client.post("/api/auth/register", json={})
        assert res.status_code == 422


class TestLogin:
    async def test_login_success(self, client: AsyncClient, workspace: Workspace):
        res = await client.post("/api/auth/login", json={
            "phone_number": workspace.phone_number,
            "password": "testpass123",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == workspace.id
        assert "access_token" not in data

    async def test_login_wrong_password(self, client: AsyncClient, workspace: Workspace):
        res = await client.post("/api/auth/login", json={
            "phone_number": workspace.phone_number,
            "password": "wrongpassword",
        })
        assert res.status_code == 401
        assert "Invalid credentials" in res.json()["detail"]

    async def test_login_unknown_phone(self, client: AsyncClient):
        res = await client.post("/api/auth/login", json={
            "phone_number": "+998999999999",
            "password": "anypassword",
        })
        assert res.status_code == 401
        assert "Invalid credentials" in res.json()["detail"]

    async def test_login_missing_password(self, client: AsyncClient, workspace: Workspace):
        res = await client.post("/api/auth/login", json={
            "phone_number": workspace.phone_number,
        })
        assert res.status_code == 422

    async def test_login_invalid_phone_format(self, client: AsyncClient):
        res = await client.post("/api/auth/login", json={
            "phone_number": "bad",
            "password": "whatever",
        })
        assert res.status_code == 422


class TestMe:
    async def test_get_me_authenticated(self, client: AsyncClient, workspace: Workspace, auth_headers: dict):
        res = await client.get("/api/auth/me", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == workspace.id
        assert data["name"] == workspace.name

    async def test_get_me_uses_durable_telegram_connection_without_sidecar(
        self,
        client: AsyncClient,
        workspace: Workspace,
        auth_headers: dict,
        db_session: AsyncSession,
    ):
        workspace.telegram_connected = True
        workspace.telegram_user_id = 424242
        db_session.add(workspace)
        await db_session.commit()

        res = await client.get("/api/auth/me", headers=auth_headers)

        assert res.status_code == 200
        assert res.json()["telegram_connected"] is True
        await db_session.refresh(workspace)
        assert workspace.telegram_connected is True

    async def test_auth_session_projection_marks_reconnect_without_sidecar(
        self,
        client: AsyncClient,
        workspace: Workspace,
        auth_headers: dict,
        db_session: AsyncSession,
    ):
        workspace.telegram_connected = False
        workspace.telegram_user_id = 777001
        db_session.add(workspace)
        await db_session.commit()

        res = await client.get("/api/auth/session", headers=auth_headers)

        assert res.status_code == 200
        data = res.json()
        assert data["schema_version"] == "auth_session_projection.v1"
        assert data["workspace"]["id"] == workspace.id
        telegram = next(
            item for item in data["integrations"]
            if item["provider"] == "telegram_personal"
        )
        assert telegram["identity_linked"] is True
        assert telegram["durable_connected"] is False
        assert telegram["needs_reconnect"] is True
        assert telegram["state"] == "needs_reconnect"
        assert telegram["live_state"] == "not_checked"
        await db_session.refresh(workspace)
        assert workspace.telegram_connected is False

    async def test_auth_session_projection_exposes_founder_role_from_allowlist(
        self,
        client: AsyncClient,
        workspace: Workspace,
        auth_headers: dict,
        monkeypatch,
    ):
        monkeypatch.setenv("ADMIN_WORKSPACE_IDS", str(workspace.id))
        get_settings.cache_clear()
        try:
            res = await client.get("/api/auth/session", headers=auth_headers)

            assert res.status_code == 200
            data = res.json()
            assert data["platform_role"] == "founder"
            assert data["is_founder"] is True
        finally:
            get_settings.cache_clear()

    async def test_get_me_no_token_401(self, client: AsyncClient):
        res = await client.get("/api/auth/me")
        assert res.status_code == 401

    async def test_get_me_invalid_token_401(self, client: AsyncClient):
        res = await client.get("/api/auth/me", headers={
            "Authorization": "Bearer fake.invalid.token"
        })
        assert res.status_code == 401

    async def test_get_me_expired_token_401(self, client: AsyncClient):
        from datetime import timedelta
        expired = create_access_token(subject="1", expires_delta=timedelta(seconds=-1))
        res = await client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {expired}"
        })
        assert res.status_code == 401


class TestWorkspaceUpdate:
    async def test_update_name(self, client: AsyncClient, workspace: Workspace, auth_headers: dict):
        res = await client.patch("/api/auth/workspace", json={
            "name": "Yangilangan Do'kon",
        }, headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["name"] == "Yangilangan Do'kon"

    async def test_update_pipeline_stages(self, client: AsyncClient, auth_headers: dict):
        res = await client.patch("/api/auth/workspace", json={
            "pipeline_stages": ["yangi", "tayyorlanmoqda", "yuborilgan"],
        }, headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["pipeline_stages"] == ["yangi", "tayyorlanmoqda", "yuborilgan"]

    async def test_partial_update(self, client: AsyncClient, workspace: Workspace, auth_headers: dict):
        original_name = workspace.name
        res = await client.patch("/api/auth/workspace", json={
            "type": "service",
        }, headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["type"] == "service"
        assert res.json()["name"] == original_name

    async def test_update_requires_auth(self, client: AsyncClient):
        res = await client.patch("/api/auth/workspace", json={"name": "Hacked"})
        assert res.status_code == 401

    async def test_update_rejects_legacy_manual_trust_mode(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ):
        res = await client.patch(
            "/api/auth/workspace",
            json={"trust_mode": "manual"},
            headers=auth_headers,
        )
        assert res.status_code == 422


class TestCookieAuth:
    async def test_register_sets_cookies(self, client: AsyncClient):
        res = await client.post("/api/auth/register", json={
            "phone_number": "+998993334455",
            "name": "Cookie Test",
            "password": "securepass123",
        })
        assert res.status_code == 201
        assert "oqim_session" in res.cookies
        assert "oqim_csrf" in res.cookies

    async def test_login_sets_cookies(self, client: AsyncClient, workspace: Workspace):
        res = await client.post("/api/auth/login", json={
            "phone_number": workspace.phone_number,
            "password": "testpass123",
        })
        assert res.status_code == 200
        assert "oqim_session" in res.cookies
        assert "oqim_csrf" in res.cookies

    async def test_me_with_cookie(self, client: AsyncClient, workspace: Workspace):
        login = await client.post("/api/auth/login", json={
            "phone_number": workspace.phone_number,
            "password": "testpass123",
        })
        session_cookie = login.cookies.get("oqim_session")

        res = await client.get("/api/auth/me", cookies={"oqim_session": session_cookie})
        assert res.status_code == 200
        assert res.json()["id"] == workspace.id

    async def test_logout_clears_cookies(self, client: AsyncClient):
        res = await client.post("/api/auth/logout")
        assert res.status_code == 200
        assert "oqim_session" in res.headers.get("set-cookie", "")


class TestCSRF:
    async def test_mutation_without_csrf_blocked(self, client: AsyncClient, auth_cookies: dict):
        """PATCH with cookie but no matching CSRF header should be blocked."""
        res = await client.patch(
            "/api/auth/workspace",
            json={"name": "Hacked"},
            cookies=auth_cookies,
        )
        assert res.status_code == 403

    async def test_mutation_with_csrf_passes(self, client: AsyncClient, auth_cookies: dict):
        """PATCH with matching CSRF cookie + header should pass."""
        csrf_token = auth_cookies["oqim_csrf"]
        res = await client.patch(
            "/api/auth/workspace",
            json={"name": "Updated"},
            cookies=auth_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200


class TestLogoutCSRF:
    async def test_logout_without_csrf_blocked_when_cookie_present(
        self, client: AsyncClient, auth_cookies: dict
    ):
        """POST /auth/logout with session cookie but no CSRF token must return 403.

        Logout is a state-changing mutation. An attacker could craft a link that
        logs the seller out by exploiting the missing CSRF protection. This test
        ensures the endpoint requires a valid CSRF token when cookie auth is used.
        """
        res = await client.post(
            "/api/auth/logout",
            cookies=auth_cookies,
        )
        assert res.status_code == 403
        assert res.json()["detail"] == "CSRF validation failed"

    async def test_logout_with_csrf_succeeds(
        self, client: AsyncClient, auth_cookies: dict
    ):
        """POST /auth/logout with valid CSRF token must clear cookies and return 200."""
        csrf_token = auth_cookies["oqim_csrf"]
        res = await client.post(
            "/api/auth/logout",
            cookies=auth_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        assert "oqim_session" in res.headers.get("set-cookie", "")

    async def test_logout_without_cookie_skips_csrf(self, client: AsyncClient):
        """POST /auth/logout with no session cookie must skip CSRF (unauthenticated call).

        The CSRF middleware only validates cookie-authenticated requests. A logout
        request with no session cookie is already a no-op and should not be blocked.
        """
        res = await client.post("/api/auth/logout")
        assert res.status_code == 200


class TestBridgeLogin:
    @patch("app.api.routes.auth._register_sidecar_session", new_callable=AsyncMock)
    async def test_new_user_creates_workspace(
        self,
        mock_register: AsyncMock,
        client: AsyncClient,
    ):
        """Bridge login with new phone creates workspace + agents, returns 201."""
        mock_register.return_value = {
            "workspaceId": 1,
            "user": {"userId": "12345", "firstName": "Alisher"},
        }
        res = await client.post("/api/auth/bridge-login", json={
            "userId": "12345",
            "phone": "+998991234567",
            "firstName": "Alisher",
            "lastName": "Karimov",
            "tempSessionId": "temp-123",
        })
        assert res.status_code == 201
        data = res.json()
        assert data["is_new"] is True
        assert data["name"] == "Alisher"
        assert data["phone_number"] == "+998991234567"
        assert data["telegram_connected"] is True
        assert "oqim_session" in res.cookies
        assert "oqim_csrf" in res.cookies
        mock_register.assert_awaited_once_with("temp-123", data["id"], "12345")

    @patch("app.api.routes.auth._register_sidecar_session", new_callable=AsyncMock)
    async def test_new_user_creates_agents(
        self,
        mock_register: AsyncMock,
        client: AsyncClient,
    ):
        """Bridge login creates customer + business agents."""
        mock_register.return_value = {
            "workspaceId": 1,
            "user": {"userId": "67890", "firstName": "Malika"},
        }
        res = await client.post("/api/auth/bridge-login", json={
            "userId": "67890",
            "phone": "+998997654321",
            "firstName": "Malika",
            "lastName": "",
            "tempSessionId": "temp-456",
        })
        assert res.status_code == 201
        workspace_id = res.json()["id"]

        token = create_access_token(subject=str(workspace_id))
        agents = await client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert agents.status_code == 200
        types = {a["agent_type"] for a in agents.json()}
        assert types == {"customer", "business"}
        mock_register.assert_awaited_once_with("temp-456", workspace_id, "67890")

    @patch("app.api.routes.auth._register_sidecar_session", new_callable=AsyncMock)
    async def test_returning_user_finds_workspace(
        self,
        mock_register: AsyncMock,
        client: AsyncClient,
        workspace: Workspace,
    ):
        """Bridge login with existing phone returns 200, is_new=false."""
        mock_register.return_value = {
            "workspaceId": workspace.id,
            "user": {"userId": "99999", "firstName": "Different Name"},
        }
        res = await client.post("/api/auth/bridge-login", json={
            "userId": "99999",
            "phone": workspace.phone_number,
            "firstName": "Different Name",
            "lastName": "",
            "tempSessionId": "temp-999",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["is_new"] is False
        assert data["id"] == workspace.id
        assert "oqim_session" in res.cookies
        mock_register.assert_awaited_once_with("temp-999", workspace.id, "99999")

    @patch("app.api.routes.auth._register_sidecar_session", new_callable=AsyncMock)
    async def test_returning_user_finds_workspace_by_telegram_user_id(
        self,
        mock_register: AsyncMock,
        client: AsyncClient,
        workspace: Workspace,
        db_session,
    ):
        workspace.telegram_user_id = 777001
        workspace.phone_number = "+998900000777"
        db_session.add(workspace)
        await db_session.flush()

        mock_register.return_value = {
            "workspaceId": workspace.id,
            "user": {"userId": "777001", "firstName": "Ali"},
        }
        res = await client.post("/api/auth/bridge-login", json={
            "userId": "777001",
            "phone": "+998911112222",
            "firstName": "Ali",
            "lastName": "",
            "tempSessionId": "temp-777",
        })

        assert res.status_code == 200
        assert res.json()["id"] == workspace.id
        mock_register.assert_awaited_once_with("temp-777", workspace.id, "777001")

    @patch("app.api.routes.auth._register_sidecar_session", new_callable=AsyncMock)
    async def test_bridge_login_uses_durable_registration_truth_in_response(
        self,
        mock_register: AsyncMock,
        client: AsyncClient,
    ):
        mock_register.return_value = {
            "workspaceId": 1,
            "user": {"userId": "12345", "firstName": "Alisher"},
        }

        res = await client.post("/api/auth/bridge-login", json={
            "userId": "12345",
            "phone": "+998991234567",
            "firstName": "Alisher",
            "lastName": "Karimov",
            "tempSessionId": "temp-123",
        })

        assert res.status_code == 201
        assert res.json()["telegram_connected"] is True

    @patch("app.api.routes.auth._register_sidecar_session", new_callable=AsyncMock)
    async def test_sidecar_registration_failure_returns_502(
        self,
        mock_register: AsyncMock,
        client: AsyncClient,
    ):
        mock_register.side_effect = HTTPException(
            status_code=502,
            detail="Telegram session registration failed",
        )

        res = await client.post("/api/auth/bridge-login", json={
            "userId": "12345",
            "phone": "+998991234567",
            "firstName": "Alisher",
            "lastName": "Karimov",
            "tempSessionId": "temp-123",
        })

        assert res.status_code == 502

    @patch("app.api.routes.auth._bind_bootstrap_sidecar_session", new_callable=AsyncMock)
    async def test_qr_auth_binds_bootstrap_session(
        self,
        mock_bind: AsyncMock,
        client: AsyncClient,
    ):
        mock_bind.return_value = {
            "workspaceId": 1,
            "user": {"userId": "12345", "firstName": "Alisher"},
        }

        res = await client.post("/api/auth/bridge-login", json={
            "userId": "12345",
            "phone": "+998991234567",
            "firstName": "Alisher",
            "lastName": "Karimov",
            "authMethod": "qr",
        })

        assert res.status_code == 201
        mock_bind.assert_awaited_once_with(res.json()["id"], "12345")

    async def test_missing_temp_session_id_returns_400(self, client: AsyncClient):
        res = await client.post("/api/auth/bridge-login", json={
            "userId": "12345",
            "phone": "+998991234567",
            "firstName": "Alisher",
            "lastName": "Karimov",
        })

        assert res.status_code == 400

    @patch("app.api.routes.auth._register_sidecar_session", new_callable=AsyncMock)
    async def test_temp_session_cookie_is_used_when_body_omits_it(
        self,
        mock_register: AsyncMock,
        client: AsyncClient,
    ):
        mock_register.return_value = {
            "workspaceId": 1,
            "user": {"userId": "12345", "firstName": "Alisher"},
        }

        client.cookies.set("oqim_tg_temp_session", "temp-cookie-1")
        res = await client.post("/api/auth/bridge-login", json={
            "userId": "12345",
            "phone": "+998991234567",
            "firstName": "Alisher",
            "lastName": "Karimov",
        })

        assert res.status_code == 201
        mock_register.assert_awaited_once_with("temp-cookie-1", res.json()["id"], "12345")

    async def test_missing_phone_422(self, client: AsyncClient):
        """Missing phone returns 422."""
        res = await client.post("/api/auth/bridge-login", json={
            "userId": "12345",
            "firstName": "Test",
        })
        assert res.status_code == 422

    async def test_invalid_phone_format_422(self, client: AsyncClient):
        """Invalid phone format returns 422."""
        res = await client.post("/api/auth/bridge-login", json={
            "userId": "12345",
            "phone": "bad",
            "firstName": "Test",
        })
        assert res.status_code == 422


class TestCompleteOnboarding:
    async def test_complete_onboarding_updates_credentials_and_business_basics(
        self,
        client: AsyncClient,
        workspace: Workspace,
        auth_headers: dict,
        db_session,
    ):
        workspace.password_hash = ""
        res = await client.post(
            "/api/auth/complete-onboarding",
            json={
                "name": "OQIM Mobile",
                "category": "electronics",
                "monthly_revenue_band": "50m-100m",
                "phone_number": "+998998887766",
                "password": "newsecure123",
            },
            headers=auth_headers,
        )

        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "OQIM Mobile"
        assert data["type"] == "electronics"
        assert data["monthly_revenue_band"] == "50m-100m"
        assert data["phone_number"] == "+998998887766"
        assert data["onboarding_completed"] is True

        await db_session.refresh(workspace)
        assert verify_password("newsecure123", workspace.password_hash)

    async def test_complete_onboarding_rejects_duplicate_phone(
        self,
        client: AsyncClient,
        workspace: Workspace,
        workspace_b: Workspace,
        auth_headers: dict,
    ):
        res = await client.post(
            "/api/auth/complete-onboarding",
            json={
                "phone_number": workspace_b.phone_number,
                "password": "newsecure123",
            },
            headers=auth_headers,
        )

        assert res.status_code == 409

    async def test_complete_onboarding_launch_start_activates_default_agents(
        self,
        client: AsyncClient,
        workspace: Workspace,
        auth_headers: dict,
        db_session: AsyncSession,
    ):
        res = await client.post(
            "/api/auth/complete-onboarding",
            json={"launch_mode": "start"},
            headers=auth_headers,
        )

        assert res.status_code == 200
        assert res.json()["onboarding_completed"] is True

        default_agents = (
            await db_session.scalars(
                select(Agent).where(
                    Agent.workspace_id == workspace.id,
                    Agent.is_default.is_(True),
                )
            )
        ).all()
        assert {a.agent_type for a in default_agents} >= {
            "seller",
            "support",
            "catalog_update",
            "follow_up",
            "bi",
        }
        assert all(a.is_active for a in default_agents)

        # complete-onboarding no longer writes legacy document sections.
        section_count = await db_session.scalar(
            select(func.count())
            .select_from(AgentDocumentSection)
            .where(AgentDocumentSection.workspace_id == workspace.id)
        )
        assert section_count == 0

    async def test_complete_onboarding_launch_later_deactivates_default_agents(
        self,
        client: AsyncClient,
        workspace: Workspace,
        auth_headers: dict,
        db_session: AsyncSession,
    ):
        res = await client.post(
            "/api/auth/complete-onboarding",
            json={"launch_mode": "later"},
            headers=auth_headers,
        )

        assert res.status_code == 200

        default_agents = (
            await db_session.scalars(
                select(Agent).where(
                    Agent.workspace_id == workspace.id,
                    Agent.is_default.is_(True),
                )
            )
        ).all()
        assert default_agents
        assert all(not a.is_active for a in default_agents)

    async def test_complete_onboarding_defaults_to_start(
        self,
        client: AsyncClient,
        workspace: Workspace,
        auth_headers: dict,
        db_session: AsyncSession,
    ):
        # No launch_mode provided → defaults to "start" (agents active).
        res = await client.post(
            "/api/auth/complete-onboarding",
            json={},
            headers=auth_headers,
        )
        assert res.status_code == 200

        default_agents = (
            await db_session.scalars(
                select(Agent).where(
                    Agent.workspace_id == workspace.id,
                    Agent.is_default.is_(True),
                )
            )
        ).all()
        assert default_agents
        assert all(a.is_active for a in default_agents)

    async def test_complete_onboarding_requires_auth(self, client: AsyncClient):
        res = await client.post(
            "/api/auth/complete-onboarding",
            json={"launch_mode": "start"},
        )
        assert res.status_code == 401

    async def test_complete_onboarding_launch_is_workspace_isolated(
        self,
        client: AsyncClient,
        workspace: Workspace,
        workspace_b: Workspace,
        auth_headers: dict,
        db_session: AsyncSession,
    ):
        # Workspace A finalizes with agents inactive.
        res = await client.post(
            "/api/auth/complete-onboarding",
            json={"launch_mode": "later"},
            headers=auth_headers,
        )
        assert res.status_code == 200

        # Workspace B is never provisioned or mutated by A's call.
        b_agents = await db_session.scalar(
            select(func.count())
            .select_from(Agent)
            .where(Agent.workspace_id == workspace_b.id)
        )
        assert b_agents == 0
