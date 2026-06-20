"""Shared fixtures for AI quality evals.

These tests make real LLM calls and require API keys.
Run separately from unit tests: python -m pytest evals/ --timeout=120
"""
import pytest
