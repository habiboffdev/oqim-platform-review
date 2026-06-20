"""
Schema validation tests — Pydantic model robustness.
Tests input validation, defaults, serialization, edge cases.
"""

import pytest
from pydantic import ValidationError

from app.schemas.agent import AgentCreate, AgentUpdate
from app.schemas.auth import BridgeLoginRequest, LoginRequest, RegisterRequest
from app.schemas.customer import CustomerCreate, CustomerUpdate
from app.schemas.workspace import WorkspaceResponse, WorkspaceUpdate


class TestAuthSchemas:
    def test_register_valid(self):
        r = RegisterRequest(phone_number="+998901234567", name="Test", password="securepass123")
        assert r.phone_number == "+998901234567"

    def test_register_invalid_phone(self):
        with pytest.raises(ValidationError):
            RegisterRequest(phone_number="12345", name="Test", password="securepass123")

    def test_register_phone_too_short(self):
        with pytest.raises(ValidationError):
            RegisterRequest(phone_number="+1234", name="Test", password="securepass123")

    def test_register_name_too_short(self):
        with pytest.raises(ValidationError):
            RegisterRequest(phone_number="+998901234567", name="A", password="securepass123")

    def test_register_name_too_long(self):
        with pytest.raises(ValidationError):
            RegisterRequest(phone_number="+998901234567", name="A" * 256, password="securepass123")

    def test_login_valid(self):
        r = LoginRequest(phone_number="+998901234567", password="test")
        assert r.phone_number == "+998901234567"

    def test_login_invalid_phone(self):
        with pytest.raises(ValidationError):
            LoginRequest(phone_number="not-a-phone")

    def test_bridge_login_uses_snake_case_fields_with_camel_case_wire_aliases(self):
        request = BridgeLoginRequest(
            userId="42",
            phone="+998901234567",
            firstName="Ali",
            lastName="Valiyev",
            tempSessionId="temp-1",
            authMethod="phone",
        )

        assert request.user_id == "42"
        assert request.first_name == "Ali"
        assert request.last_name == "Valiyev"
        assert request.temp_session_id == "temp-1"
        assert request.auth_method == "phone"
        assert request.model_dump(by_alias=True)["tempSessionId"] == "temp-1"


class TestCustomerSchemas:
    def test_create_minimal(self):
        c = CustomerCreate(display_name="Test")
        assert c.language == "uz"
        assert c.tags == []
        assert c.phone_number is None

    def test_create_full(self):
        c = CustomerCreate(
            display_name="Full Customer",
            phone_number="+998912345678",
            language="ru",
            tags=["VIP"],
            notes="Important",
        )
        assert c.language == "ru"
        assert c.tags == ["VIP"]

    def test_update_partial(self):
        u = CustomerUpdate(display_name="New Name")
        dumped = u.model_dump(exclude_unset=True)
        assert "display_name" in dumped
        assert "phone_number" not in dumped

    def test_update_empty_is_valid(self):
        """Empty update should be allowed (no changes)."""
        u = CustomerUpdate()
        assert u.model_dump(exclude_unset=True) == {}


class TestAgentSchemas:
    def test_create_minimal(self):
        a = AgentCreate(name="Bot")
        assert a.trust_mode == "disabled"
        assert a.auto_send_threshold == 0.85
        assert a.persona == {}
        assert a.escalation_topics == []

    def test_create_full(self):
        a = AgentCreate(
            name="Full Bot",
            persona={"role": "Sales", "tone": "Friendly"},
            instructions="Be helpful.",
            trust_mode="autonomous",
            auto_send_threshold=0.90,
            escalation_topics=["refund", "complaint"],
            channel_config={"mode": "both", "chat_ids": [-100123]},
            example_responses=[{"in": "hi", "out": "Hello!"}],
        )
        # Legacy 'autonomous' coerces to 'disabled' (two-state trust model).
        assert a.trust_mode == "disabled"
        assert len(a.escalation_topics) == 2

    def test_update_partial(self):
        u = AgentUpdate(trust_mode="autopilot")
        dumped = u.model_dump(exclude_unset=True)
        assert dumped == {"trust_mode": "autopilot"}
        assert "name" not in dumped


class TestWorkspaceSchemas:
    def test_response_rejects_legacy_manual_trust_mode(self):
        payload = {
            "id": 1,
            "phone_number": "+998901234567",
            "name": "Test Do'kon",
            "type": "ecommerce",
            "pipeline_stages": ["new"],
            "subscription_tier": "free",
            "trust_mode": "manual",
            "telegram_connected": False,
            "created_at": "2026-06-08T00:00:00Z",
        }

        with pytest.raises(ValidationError):
            WorkspaceResponse.model_validate(payload)

    def test_update_rejects_legacy_manual_trust_mode(self):
        with pytest.raises(ValidationError):
            WorkspaceUpdate(trust_mode="manual")
