"""
Unit tests for JWT security — token creation, verification, edge cases.
No database needed.
"""

import time
from datetime import timedelta

import jwt
import pytest

from app.core.security import create_access_token, verify_token


class TestTokenCreation:
    def test_creates_valid_jwt(self):
        token = create_access_token(subject="42")
        assert isinstance(token, str)
        assert len(token) > 50  # JWTs are always long

    def test_subject_embedded_in_token(self):
        token = create_access_token(subject="123")
        result = verify_token(token)
        assert result == "123"

    def test_integer_subject_converted_to_string(self):
        token = create_access_token(subject=999)
        result = verify_token(token)
        assert result == "999"

    def test_custom_expiry(self):
        token = create_access_token(
            subject="1", expires_delta=timedelta(minutes=5)
        )
        assert verify_token(token) == "1"

    def test_expired_token_returns_none(self):
        token = create_access_token(
            subject="1", expires_delta=timedelta(seconds=-1)
        )
        assert verify_token(token) is None


class TestTokenVerification:
    def test_valid_token(self):
        token = create_access_token(subject="42")
        assert verify_token(token) == "42"

    def test_tampered_token_returns_none(self):
        token = create_access_token(subject="42")
        tampered = token[:-5] + "XXXXX"
        assert verify_token(tampered) is None

    def test_completely_invalid_string(self):
        assert verify_token("not-a-jwt") is None

    def test_empty_string(self):
        assert verify_token("") is None

    def test_wrong_secret_key(self):
        """Token signed with different key should fail."""
        payload = {"sub": "42", "exp": time.time() + 3600}
        token = jwt.encode(payload, "wrong-secret", algorithm="HS256")
        assert verify_token(token) is None

    def test_missing_subject(self):
        """Token without 'sub' claim returns None."""
        from app.core.config import get_settings
        settings = get_settings()
        payload = {"exp": time.time() + 3600}
        token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
        # verify_token returns payload.get("sub") which is None
        assert verify_token(token) is None

    def test_none_subject_in_token(self):
        from app.core.config import get_settings
        settings = get_settings()
        payload = {"sub": None, "exp": time.time() + 3600}
        token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
        assert verify_token(token) is None


class TestConfigSecurity:
    def test_dev_key_rejected_in_production(self):
        """Insecure keys must be rejected in production environments."""
        from app.core.config import Settings

        with pytest.raises(ValueError, match="SECRET_KEY must be set"):
            Settings(
                _env_file=None,
                APP_ENV="production",
                SECRET_KEY="dev-only-insecure-key",
                DATABASE_URL="postgresql+asyncpg://localhost/test",
            )

    def test_short_key_rejected_in_production(self):
        from app.core.config import Settings

        with pytest.raises(ValueError, match="at least 32 characters"):
            Settings(
                _env_file=None,
                APP_ENV="production",
                SECRET_KEY="short",
                DATABASE_URL="postgresql+asyncpg://localhost/test",
            )

    def test_secure_key_accepted_in_production(self):
        from app.core.config import Settings

        s = Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY="a" * 64,
            TELEGRAM_SESSION_KEY="dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleTA=",
            CORS_ORIGINS="https://your-domain.example",
            COOKIE_DOMAIN="your-domain.example",
            COOKIE_SECURE=True,
            DATABASE_URL="postgresql+asyncpg://localhost/test",
        )
        assert s.secret_key == "a" * 64

    def test_runtime_migration_flags_default_to_canonical_path(self, monkeypatch):
        from app.core.config import Settings

        monkeypatch.delenv("EVENT_SPINE_PERSIST_CONSUMER_ENABLED", raising=False)
        monkeypatch.delenv("EVENT_SPINE_PERSIST_MODE", raising=False)
        default_settings = Settings(
            _env_file=None,
            SECRET_KEY="dev-only-insecure-key",
            DATABASE_URL="postgresql+asyncpg://localhost/test",
        )
        legacy_settings = Settings(
            _env_file=None,
            SECRET_KEY="dev-only-insecure-key",
            DATABASE_URL="postgresql+asyncpg://localhost/test",
            EVENT_SPINE_PERSIST_MODE="shadow",
        )

        assert default_settings.event_spine_persist_consumer_enabled is True
        assert default_settings.is_event_spine_authoritative() is True
        assert all(
            "legacy_runtime" not in field_name
            for field_name in type(default_settings).model_fields
        )
        assert "reply_autopilot_enabled" not in type(default_settings).model_fields
        assert ("scheduled_" + "reply_sender_enabled") not in type(default_settings).model_fields
        assert ("scheduled_" + "reply_sender_max_claims_per_workspace") not in type(default_settings).model_fields
        assert default_settings.onboarding_runtime_worker_enabled is True
        assert default_settings.onboarding_runtime_worker_batch_size == 2
        assert default_settings.chat_memory_pair_index_worker_enabled is True
        assert default_settings.chat_memory_extraction_worker_enabled is False
        assert all(
            "intelligence_side_effects" not in field_name
            for field_name in type(default_settings).model_fields
        )
        assert default_settings.telegram_presence_online_enabled is True
        assert default_settings.telegram_presence_read_enabled is True
        assert legacy_settings.is_event_spine_authoritative() is False

    def test_onboarding_runtime_worker_batch_size_is_configurable(self):
        from app.core.config import Settings

        settings = Settings(
            _env_file=None,
            SECRET_KEY="dev-only-insecure-key",
            DATABASE_URL="postgresql+asyncpg://localhost/test",
            ONBOARDING_RUNTIME_WORKER_BATCH_SIZE=5,
        )

        assert settings.onboarding_runtime_worker_batch_size == 5
