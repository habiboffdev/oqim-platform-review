"""Tests for production config validation (M9, M25)."""
import os
import pytest
from unittest.mock import patch

from app.core.config import Settings


# Base valid production env for all tests
VALID_PROD_ENV = {
    "APP_ENV": "production",
    "SECRET_KEY": "a" * 32,
    "TELEGRAM_SESSION_KEY": "dGVzdC1mZXJuZXQta2V5LWZvci10ZXN0aW5n",
    "CORS_ORIGINS": "https://your-domain.example",
    "COOKIE_DOMAIN": "your-domain.example",
    "COOKIE_SECURE": "true",
    "DATABASE_URL": "postgresql+asyncpg://localhost:5434/test",
    "REDIS_URL": "redis://localhost:6381/0",
    "GEMINI_API_KEY": "fake-gemini-key",
}


def _make_settings(**overrides):
    """Create Settings with production defaults + overrides.

    Uses patch.dict so the existing conftest.py env vars don't interfere.
    We explicitly set clear=False and let overrides win.
    """
    env = {**VALID_PROD_ENV, **overrides}
    with patch.dict(os.environ, env, clear=False):
        return Settings()


def test_valid_production_config_passes():
    """Sanity: valid production config should not raise."""
    _make_settings()  # Should not raise


def test_production_rejects_localhost_cors():
    """M25: Production must reject localhost-only CORS origins."""
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _make_settings(CORS_ORIGINS="http://localhost:4200")


def test_production_requires_cookie_domain():
    """Production must have COOKIE_DOMAIN set."""
    with pytest.raises(ValueError, match="COOKIE_DOMAIN"):
        _make_settings(COOKIE_DOMAIN="")


def test_production_requires_cookie_secure():
    """Production must have COOKIE_SECURE=true."""
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        _make_settings(COOKIE_SECURE="false")
