"""Unit tests for G6 (per-agent cost rollup GET /v1/ledger/agents)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.callers.principal import Principal
from backend.routers.ledger import get_agents_rollup
from gtm_core.capabilities import Entitlement

_WS_ID = "11111111-1111-1111-1111-111111111111"
_AGENT_1 = "22222222-2222-2222-2222-222222222222"
_AGENT_2 = "33333333-3333-3333-3333-333333333333"


def _run_coro(coro):
    return asyncio.run(coro)


def test_get_agents_rollup_groups_spend():
    async def _test():
        pool = MagicMock()
        conn = MagicMock()
        conn.execute = AsyncMock()

        rows = [
            {
                "agent_id": _AGENT_1,
                "principal_kind": "service",
                "principal_id": "key-1",
                "runs": 5,
                "calls": 20,
                "cost_usd": 1.25,
                "input_tokens": 10000,
                "output_tokens": 5000,
            },
            {
                "agent_id": _AGENT_2,
                "principal_kind": "user",
                "principal_id": "user-1",
                "runs": 2,
                "calls": 8,
                "cost_usd": 0.50,
                "input_tokens": 4000,
                "output_tokens": 2000,
            },
        ]
        conn.fetch = AsyncMock(return_value=rows)

        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        req = MagicMock()
        req.app.state.pool = pool

        principal = Principal(
            kind="user",
            subject="user-1",
            workspace_id=_WS_ID,
            entitlement=Entitlement.PRO,
        )

        resp = await get_agents_rollup(
            principal=principal,
            request=req,
            from_date="2026-09-01",
            to_date="2026-09-30",
        )

        assert resp.total_usd == 1.75
        assert len(resp.items) == 2
        assert resp.items[0].agent_id == _AGENT_1
        assert resp.items[0].cost_usd == 1.25
        assert resp.items[1].agent_id == _AGENT_2
        assert resp.items[1].cost_usd == 0.50

    _run_coro(_test())


def test_get_agents_rollup_rejects_invalid_dates():
    async def _test():
        req = MagicMock()
        principal = Principal(
            kind="user",
            subject="user-1",
            workspace_id=_WS_ID,
            entitlement=Entitlement.PRO,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_agents_rollup(
                principal=principal,
                request=req,
                from_date="invalid-date",
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "invalid_from_date"

        with pytest.raises(HTTPException) as exc_info:
            await get_agents_rollup(
                principal=principal,
                request=req,
                to_date="not-a-date",
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "invalid_to_date"

        with pytest.raises(HTTPException) as exc_info:
            await get_agents_rollup(
                principal=principal,
                request=req,
                from_date="2026-09-17T12:00:00Z",
                to_date="2026-09-16T12:00:00Z",
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "invalid_date_range"

    _run_coro(_test())


def test_get_agents_rollup_forces_own_scope_for_service_principal():
    async def _test():
        pool = MagicMock()
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])

        # Mock agent read_scope query to return 'own'
        conn.fetchval = AsyncMock(return_value="own")

        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        req = MagicMock()
        req.app.state.pool = pool

        principal = Principal(
            kind="service",
            subject="key-1",
            workspace_id=_WS_ID,
            entitlement=Entitlement.PRO,
            agent_id=_AGENT_1,
        )

        # Passing a different agent_id should be forced to principal.agent_id
        await get_agents_rollup(
            principal=principal,
            request=req,
            agent_id=_AGENT_2,
        )

        # Assert conn.fetch was called with principal.agent_id (_AGENT_1), NOT _AGENT_2
        call_args = conn.fetch.call_args[0]
        assert _AGENT_1 in call_args
        assert _AGENT_2 not in call_args

    _run_coro(_test())
