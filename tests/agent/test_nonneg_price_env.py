"""Unit tests for ``agent.mcp.nonneg_price_env`` — the metered workers' price guard.

Prices feed the §R2 monthly cost cap. A negative override would make a metered call
*reduce* recorded spend, letting a run stay nominally under cap while spending past
it; a non-numeric one would blow up at the first generation rather than at boot.
Both must fail fast, at import, so a misconfigured deployment refuses to start.
"""

from __future__ import annotations

import pytest

from agent.mcp import nonneg_price_env


def test_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("GTM_TEST_PRICE", raising=False)
    assert nonneg_price_env("GTM_TEST_PRICE", "0.134") == 0.134


def test_env_override_is_parsed(monkeypatch):
    monkeypatch.setenv("GTM_TEST_PRICE", "0.42")
    assert nonneg_price_env("GTM_TEST_PRICE", "0.134") == 0.42


def test_zero_is_allowed(monkeypatch):
    """A free tier is a legitimate configuration — only negatives are rejected."""
    monkeypatch.setenv("GTM_TEST_PRICE", "0")
    assert nonneg_price_env("GTM_TEST_PRICE", "0.134") == 0.0


@pytest.mark.parametrize("raw", ["-0.5", "-1", "-0.000001"])
def test_negative_is_rejected(monkeypatch, raw):
    monkeypatch.setenv("GTM_TEST_PRICE", raw)
    with pytest.raises(ValueError, match="must be non-negative"):
        nonneg_price_env("GTM_TEST_PRICE", "0.134")


@pytest.mark.parametrize("raw", ["free", "0.1.2", "1e", "$0.13"])
def test_non_numeric_is_rejected(monkeypatch, raw):
    monkeypatch.setenv("GTM_TEST_PRICE", raw)
    with pytest.raises(ValueError, match="must be a number"):
        nonneg_price_env("GTM_TEST_PRICE", "0.134")


def test_empty_env_falls_back_to_default(monkeypatch):
    """An unset-but-present env var (common in compose) must not read as 0.0."""
    monkeypatch.setenv("GTM_TEST_PRICE", "")
    assert nonneg_price_env("GTM_TEST_PRICE", "0.35") == 0.35


def test_both_workers_route_their_prices_through_the_guard():
    """Pin the wiring, not just the helper — a worker reverting to bare float() is the bug."""
    gemini = pytest.importorskip("agent.mcp.gemini_image.server")
    higgsfield = pytest.importorskip("agent.mcp.higgsfield_video.server")
    assert gemini._PRICE_1K_USD >= 0
    assert gemini._PRICE_2K_USD >= 0
    assert gemini._PRICE_4K_USD >= 0
    assert higgsfield._VIDEO_EST_USD >= 0

    import inspect

    for module in (gemini, higgsfield):
        src = inspect.getsource(module)
        assert "nonneg_price_env(" in src, f"{module.__name__} must use the shared guard"
        assert "float(os.getenv" not in src, f"{module.__name__} has an unguarded price read"
