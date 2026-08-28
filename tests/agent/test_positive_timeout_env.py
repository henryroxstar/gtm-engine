"""Unit tests for ``agent.mcp.positive_timeout_env`` — the workers' HTTP ceiling guard.

A ceiling below a model's real render time makes the worker unusable while looking
like a provider outage: ``gemini-3-pro-image-preview`` ran past the old hardcoded
120s on every attempt, so each generation returned ``ReadTimeout`` after the request
had already reached Google. A zero or negative override reproduces that failure at
the extreme — aborting before the request is sent — so both are rejected at import,
the same way a negative price is.
"""

from __future__ import annotations

import pytest

from agent.mcp import positive_timeout_env


def test_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("GTM_TEST_TIMEOUT", raising=False)
    assert positive_timeout_env("GTM_TEST_TIMEOUT", "600") == 600.0


def test_env_override_is_parsed(monkeypatch):
    monkeypatch.setenv("GTM_TEST_TIMEOUT", "45.5")
    assert positive_timeout_env("GTM_TEST_TIMEOUT", "600") == 45.5


@pytest.mark.parametrize("raw", ["0", "-1", "-0.001"])
def test_non_positive_is_rejected(monkeypatch, raw):
    monkeypatch.setenv("GTM_TEST_TIMEOUT", raw)
    with pytest.raises(ValueError, match="must be positive"):
        positive_timeout_env("GTM_TEST_TIMEOUT", "600")


@pytest.mark.parametrize("raw", ["soon", "60s", "1e"])
def test_non_numeric_is_rejected(monkeypatch, raw):
    monkeypatch.setenv("GTM_TEST_TIMEOUT", raw)
    with pytest.raises(ValueError, match="must be a number"):
        positive_timeout_env("GTM_TEST_TIMEOUT", "600")


def test_empty_env_falls_back_to_default(monkeypatch):
    """An unset-but-present env var (common in compose) must not read as 0.0."""
    monkeypatch.setenv("GTM_TEST_TIMEOUT", "")
    assert positive_timeout_env("GTM_TEST_TIMEOUT", "600") == 600.0
