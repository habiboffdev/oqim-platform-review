"""Tests for Cerebras configuration fields in Settings."""

from unittest.mock import patch

from app.core.config import Settings


def test_cerebras_api_key_none_by_default():
    """Settings loads with CEREBRAS_API_KEY=None when no env var is set."""
    with patch.dict("os.environ", {}, clear=False):
        s = Settings()
        assert s.cerebras_api_key is None


def test_cerebras_base_url_default():
    """Settings loads cerebras_base_url as Cerebras API endpoint by default."""
    s = Settings()
    assert s.cerebras_base_url == "https://api.cerebras.ai/v1"


def test_cerebras_timeout_default():
    """Settings loads cerebras_timeout as 5.0 by default."""
    s = Settings()
    assert s.cerebras_timeout == 5.0


def test_cerebras_default_model():
    """Settings loads cerebras_default_model as qwen-3 by default."""
    s = Settings()
    assert s.cerebras_default_model == "qwen-3-235b-a22b-instruct-2507"
