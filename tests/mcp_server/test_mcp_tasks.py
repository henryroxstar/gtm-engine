"""Unit tests for G9 agent-facing MCP task surface (mcp_server/tasks.py)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import Context

from mcp_server.tasks import (
    artifacts_task,
    cost_task,
    describe_task,
    dispatch_task,
    require_task_principal,
    status_task,
)

_WS_ID = "11111111-1111-1111-1111-111111111111"
_AGENT_ID = "22222222-2222-2222-2222-222222222222"
_KEY_ID = "33333333-3333-3333-3333-333333333333"
_RUN_ID = "44444444-4444-4444-4444-444444444444"


def _run(coro):
    return asyncio.run(coro)


def _make_pool(resolve_row=None, agent_row=None, run_row=None):
    conn = MagicMock()
    conn.execute = AsyncMock()

    def _fetchrow(q, *args):
        if "agents" in q:
            return agent_row
        if "runs" in q:
            return run_row
        return resolve_row

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.fetchval = AsyncMock(return_value="pro")
    conn.fetch = AsyncMock(return_value=[])

    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_http_ctx(auth_header: str | None = None) -> Context:
    ctx = MagicMock(spec=Context)
    req = MagicMock()
    if auth_header:
        req.headers = {"authorization": auth_header}
    else:
        req.headers = {}
    ctx.request_context = MagicMock()
    ctx.request_context.request = req
    return ctx


def _make_stdio_ctx() -> Context:
    ctx = MagicMock(spec=Context)
    ctx.request_context = MagicMock()
    ctx.request_context.request = None
    return ctx


def test_header_auth_only_on_http_transport(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "sk-fallback-env-key")
    pool = _make_pool()

    # HTTP request missing Authorization header must fail and NOT fall back to env key
    ctx = _make_http_ctx(None)
    with pytest.raises(ValueError, match="Missing Authorization header"):
        _run(require_task_principal(ctx, pool))


def test_stdio_falls_back_to_env_key(monkeypatch):
    pool = _make_pool(
        resolve_row={
            "key_id": _KEY_ID,
            "workspace_id": _WS_ID,
            "entitlement": "pro",
            "agent_id": _AGENT_ID,
        },
        agent_row={
            "status": "active",
            "packs": None,
            "monthly_budget_usd": 100.0,
            "read_scope": "own",
            "daily_dispatch_cap": 50,
        },
    )
    monkeypatch.setenv("MCP_API_KEY", "sk-valid-stdio-key")

    ctx = _make_stdio_ctx()
    principal = _run(require_task_principal(ctx, pool))
    assert principal.kind == "service"
    assert principal.workspace_id == _WS_ID
    assert principal.agent_id == _AGENT_ID


def test_status_task_validates_uuid():
    pool = _make_pool()
    ctx = _make_http_ctx("Bearer sk-test")
    with pytest.raises(ValueError):
        _run(status_task("not-a-uuid", ctx, pool=pool))


def test_status_task_raises_on_unknown_run():
    pool = _make_pool(
        resolve_row={
            "key_id": _KEY_ID,
            "workspace_id": _WS_ID,
            "entitlement": "pro",
            "agent_id": _AGENT_ID,
        },
        agent_row={
            "status": "active",
            "packs": None,
            "monthly_budget_usd": 100.0,
            "read_scope": "own",
            "daily_dispatch_cap": 50,
        },
    )
    ctx = _make_http_ctx("Bearer sk-valid")

    # fetchrow returning None for run query
    with pytest.raises(ValueError, match="run_not_found"):
        _run(status_task(_RUN_ID, ctx, pool=pool))


def test_artifacts_task_validates_uuid():
    pool = _make_pool(
        resolve_row={
            "key_id": _KEY_ID,
            "workspace_id": _WS_ID,
            "entitlement": "pro",
            "agent_id": _AGENT_ID,
        },
        agent_row={"status": "active"},
    )
    ctx = _make_http_ctx("Bearer sk-test")
    with pytest.raises(ValueError, match="valid UUID"):
        _run(artifacts_task("invalid-uuid", ctx, pool=pool))


def test_cost_task_validates_uuid():
    pool = _make_pool(
        resolve_row={
            "key_id": _KEY_ID,
            "workspace_id": _WS_ID,
            "entitlement": "pro",
            "agent_id": _AGENT_ID,
        },
        agent_row={"status": "active"},
    )
    ctx = _make_http_ctx("Bearer sk-test")
    with pytest.raises(ValueError, match="valid UUID"):
        _run(cost_task("invalid-uuid", ctx, pool=pool))


def test_describe_task_raises_on_unknown_agent():
    pool = _make_pool(
        resolve_row={
            "key_id": _KEY_ID,
            "workspace_id": _WS_ID,
            "entitlement": "pro",
            "agent_id": _AGENT_ID,
        },
        agent_row=None,
    )
    ctx = _make_http_ctx("Bearer sk-valid")
    with pytest.raises(ValueError, match="agent_not_found"):
        _run(describe_task(ctx=ctx, pool=pool))


def test_dispatch_task_fails_closed_on_unknown_agent():
    pool = _make_pool(
        resolve_row={
            "key_id": _KEY_ID,
            "workspace_id": _WS_ID,
            "entitlement": "pro",
            "agent_id": _AGENT_ID,
        },
        agent_row=None,
    )
    ctx = _make_http_ctx("Bearer sk-valid")
    with pytest.raises(ValueError):
        _run(dispatch_task(pack="marketing", ctx=ctx, pool=pool))
