from __future__ import annotations

from app.modules.agent_runtime_v2.context_config import (
    CONTEXT_WINDOW_DEFAULT,
    CONTEXT_WINDOW_MAX,
    CONTEXT_WINDOW_MIN,
    resolve_context_window,
)


def test_constants_match_gemini_true_window_and_hermes_floor():
    assert CONTEXT_WINDOW_MIN == 64_000          # Hermes MINIMUM_CONTEXT_LENGTH
    assert CONTEXT_WINDOW_MAX == 1_048_576        # gemini's true window
    assert CONTEXT_WINDOW_DEFAULT == 1_048_576


def test_absent_or_empty_config_returns_default():
    assert resolve_context_window(None) == CONTEXT_WINDOW_DEFAULT
    assert resolve_context_window({}) == CONTEXT_WINDOW_DEFAULT
    assert resolve_context_window({"context": {}}) == CONTEXT_WINDOW_DEFAULT


def test_valid_in_range_value_passes_through():
    assert resolve_context_window({"context": {"window_tokens": 200_000}}) == 200_000


def test_clamps_below_floor_up_to_min():
    assert resolve_context_window({"context": {"window_tokens": 10_000}}) == CONTEXT_WINDOW_MIN
    assert resolve_context_window({"context": {"window_tokens": 64_000}}) == 64_000


def test_clamps_above_ceiling_down_to_max():
    assert resolve_context_window({"context": {"window_tokens": 5_000_000}}) == CONTEXT_WINDOW_MAX


def test_ignores_invalid_types_and_bool():
    assert resolve_context_window({"context": {"window_tokens": "200000"}}) == CONTEXT_WINDOW_DEFAULT
    assert resolve_context_window({"context": {"window_tokens": True}}) == CONTEXT_WINDOW_DEFAULT
    assert resolve_context_window({"context": {"window_tokens": 3.5}}) == CONTEXT_WINDOW_DEFAULT
    assert resolve_context_window({"context": "nope"}) == CONTEXT_WINDOW_DEFAULT
