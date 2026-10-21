"""Integration tests for MCP and REST API load-shedding and queue starvation protection.

Ensures that autonomous agents executing tight loops are shed at the gateway/edge/admission
layer with HTTP 429 before flooding the Postgres queue or starving worker processes.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.callers.limits import enforce_agent_daily_cap, enforce_principal_rate
from backend.callers.ports import Refusal
from backend.callers.principal import Principal
from backend.ratelimit import limiter
from mcp_server.tasks import require_task_principal


def _make_service_principal(subject: str = "key-load-test-123") -> Principal:
    return Principal(
        kind="service",
        subject=subject,
        workspace_id="11111111-1111-1111-1111-111111111111",
        entitlement="pro",
        agent_id="22222222-2222-2222-2222-222222222222",
    )


def _make_user_principal(subject: str = "user-load-test-456") -> Principal:
    return Principal(
        kind="user",
        subject=subject,
        workspace_id="11111111-1111-1111-1111-111111111111",
        entitlement="pro",
    )


def test_principal_rate_limiter_sheds_burst_at_ceiling(monkeypatch):
    """Verify that rapid automated calls are shed with 429 principal_rate_limited."""
    monkeypatch.setattr(limiter, "enabled", True)
    principal = _make_service_principal("svc-burst-test-unique")

    # 60 calls per minute are allowed
    for _ in range(60):
        enforce_principal_rate(principal)

    # 61st call must be shed with Refusal(429, "principal_rate_limited")
    with pytest.raises(Refusal) as exc_info:
        enforce_principal_rate(principal)

    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "principal_rate_limited"


def test_human_user_principal_exempt_from_service_ceiling(monkeypatch):
    """Verify that human user callers are never subject to the service principal rate limit."""
    monkeypatch.setattr(limiter, "enabled", True)
    human = _make_user_principal("human-burst-test")

    # A human principal can pass 100 times without tripping service rate limiter
    for _ in range(100):
        enforce_principal_rate(human)


def test_mcp_task_principal_enforces_load_shedding(monkeypatch):
    """Verify that FastMCP require_task_principal trips load shedding on rapid calls."""
    monkeypatch.setattr(limiter, "enabled", True)
    principal = _make_service_principal("mcp-burst-key-unique")

    # Mock DB pool returning service principal
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetchrow = AsyncMock(
        return_value={
            "key_id": "key-id-123",
            "workspace_id": principal.workspace_id,
            "entitlement": "pro",
            "agent_id": principal.agent_id,
        }
    )
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    ctx = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": "Bearer sk-valid-mcp-key"}

    # Mock verify_chain and admit_all to return our test principal
    monkeypatch.setattr(
        "mcp_server.tasks.verify_chain",
        AsyncMock(return_value=principal),
    )
    monkeypatch.setattr(
        "mcp_server.tasks.admit_all",
        AsyncMock(return_value=None),
    )

    # Exhaust quota
    for _ in range(60):
        asyncio.run(require_task_principal(ctx, mock_pool))

    # 61st call must raise ValueError containing principal_rate_limited
    with pytest.raises(ValueError, match="principal_rate_limited"):
        asyncio.run(require_task_principal(ctx, mock_pool))


def test_agent_daily_cap_sheds_load_before_queue_insert():
    """Verify that daily dispatch cap sheds load before runs enter Postgres queue."""
    principal = _make_service_principal("cap-test-key")
    agent_row = {
        "agent_id": principal.agent_id,
        "daily_dispatch_cap": 5,
    }

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()
    # Mock acheck_agent_daily_cap returning False (cap reached)
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_cfg = MagicMock()
    mock_cfg.repo_root = "/tmp"

    async def _run_cap():
        from unittest.mock import patch

        with patch("backend.callers.limits.acheck_agent_daily_cap", AsyncMock(return_value=False)):
            with patch("backend.callers.limits._audit"):
                await enforce_agent_daily_cap(
                    mock_pool, mock_cfg, principal, agent_row, "mcp/dispatch"
                )

    with pytest.raises(Refusal) as exc_info:
        asyncio.run(_run_cap())

    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "agent_daily_cap_reached"
