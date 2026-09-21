"""Unit tests for mcp_server.server."""

import asyncio
from unittest.mock import MagicMock

import pytest

from mcp_server.server import _require_key, draft_post


def _run(coro):
    return asyncio.run(coro)


def _make_ctx(headers=None):
    ctx = MagicMock()
    if headers is not None:
        ctx.request_context.request = MagicMock()

        def _get(key, default=""):
            return headers.get(key.lower(), default)

        ctx.request_context.request.headers.get.side_effect = _get
    else:
        ctx.request_context = MagicMock()
        ctx.request_context.request = None
    return ctx


def test_draft_post_prompt_size_cap():
    """Verify draft_post enforces the 100,000 character limit on brief."""
    ctx = _make_ctx(headers={"authorization": "Bearer sk-123"})

    # 100,001 chars
    huge_brief = "a" * 100001

    with pytest.raises(ValueError, match="exceeds maximum allowed"):
        _run(draft_post(brief=huge_brief, profile="test", ctx=ctx))

    # 100,000 chars should NOT raise the prompt size ValueError (might raise auth/db error next, which is fine)
    exact_limit = "a" * 100000
    try:
        _run(draft_post(brief=exact_limit, profile="test", ctx=ctx))
    except Exception as e:
        assert "exceeds maximum allowed" not in str(e)


def test_require_key_gateway_secret(monkeypatch):
    """Verify X-Gateway-Secret enforcement when MCP_GATEWAY_SECRET is set."""
    monkeypatch.setenv("MCP_GATEWAY_SECRET", "supersecret123")

    # 1. Missing header
    ctx1 = _make_ctx(headers={})
    with pytest.raises(ValueError, match="Unauthorized gateway bypass"):
        _run(_require_key(ctx1))

    # 2. Invalid header
    ctx2 = _make_ctx(headers={"x-gateway-secret": "wrongsecret", "authorization": "Bearer sk-test"})
    with pytest.raises(ValueError, match="Unauthorized gateway bypass"):
        _run(_require_key(ctx2))

    # 3. Valid header (will fail later in API key validation, which is expected)
    ctx3 = _make_ctx(
        headers={"x-gateway-secret": "supersecret123", "authorization": "Bearer sk-test"}
    )
    try:
        _run(_require_key(ctx3))
    except Exception as e:
        assert "Unauthorized gateway bypass" not in str(e)
