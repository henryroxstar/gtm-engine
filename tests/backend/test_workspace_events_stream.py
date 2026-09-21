"""Unit and contract tests for G3 (workspace event stream GET /v1/events/stream)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.callers.principal import Principal
from backend.services.runs.events import (
    _publish_workspace_event,
    _subscribe_workspace,
    _unsubscribe_workspace,
    publish_run_event,
)
from backend.services.runs.stream import workspace_event_stream
from gtm_core.capabilities import Entitlement

_WS_ID = "11111111-1111-1111-1111-111111111111"
_AGENT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_AGENT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_RUN_1 = "11111111-2222-3333-4444-555555555555"
_RUN_2 = "66666666-7777-8888-9999-000000000000"


def _run_coro(coro):
    return asyncio.run(coro)


def _make_pool(active_runs=None):
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=active_runs or [])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=1)

    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def test_subscribe_and_publish_workspace_event():
    async def _test():
        q = _subscribe_workspace(_WS_ID)
        try:
            _publish_workspace_event(_WS_ID, _RUN_1, "status", {"status": "running"})
            frame = q.get_nowait()
            event, data = frame
            assert event == "status"
            assert data["run_id"] == _RUN_1
            assert data["status"] == "running"
        finally:
            _unsubscribe_workspace(_WS_ID, q)

    _run_coro(_test())


def test_publish_run_event_delivers_to_workspace():
    async def _test():
        q = _subscribe_workspace(_WS_ID)
        try:
            publish_run_event(_WS_ID, _RUN_1, "status", {"run_id": _RUN_1, "status": "ok"})
            frame = q.get_nowait()
            event, data = frame
            assert event == "status"
            assert data["run_id"] == _RUN_1
            assert data["status"] == "ok"
        finally:
            _unsubscribe_workspace(_WS_ID, q)

    _run_coro(_test())


def test_workspace_event_stream_emits_snapshots_and_frames():
    async def _test():
        pool = _make_pool()
        principal = Principal(
            kind="user",
            subject="u1",
            workspace_id=_WS_ID,
            entitlement=Entitlement.PRO,
        )

        disconnect = AsyncMock(return_value=False)
        stream = workspace_event_stream(
            pool,
            _WS_ID,
            principal=principal,
            read_scope="workspace",
            is_disconnected=disconnect,
        )

        # First item is retry: 3000
        first = await anext(stream)
        assert "retry: 3000" in first

        # Publish an event to the workspace
        _publish_workspace_event(_WS_ID, _RUN_1, "status", {"status": "running"})

        frame_text = await anext(stream)
        assert "event: status" in frame_text
        assert "id: 1" in frame_text
        assert _RUN_1 in frame_text

        # Simulate disconnect
        disconnect.return_value = True
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    _run_coro(_test())


def test_workspace_event_stream_filters_for_service_principal_own_scope():
    async def _test():
        # Service principal with read_scope='own' should ignore events from other agents
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])

        # Return agent_id for run_1 matching agent_a, and run_2 matching agent_b
        async def _mock_fetchrow(query, run_id, ws_id):
            if run_id == _RUN_1:
                return {"agent_id": _AGENT_A}
            return {"agent_id": _AGENT_B}

        conn.fetchrow = AsyncMock(side_effect=_mock_fetchrow)

        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        principal = Principal(
            kind="service",
            subject="k1",
            workspace_id=_WS_ID,
            entitlement=Entitlement.PRO,
            agent_id=_AGENT_A,
        )

        disconnect = AsyncMock(return_value=False)
        stream = workspace_event_stream(
            pool,
            _WS_ID,
            principal=principal,
            read_scope="own",
            is_disconnected=disconnect,
        )

        await anext(stream)  # retry: 3000

        # Publish an event for agent B's run (should be filtered out)
        _publish_workspace_event(_WS_ID, _RUN_2, "status", {"status": "running"})
        # Publish an event for agent A's run (should be delivered)
        _publish_workspace_event(_WS_ID, _RUN_1, "status", {"status": "running"})

        frame_text = await anext(stream)
        assert "event: status" in frame_text
        assert _RUN_1 in frame_text
        assert _RUN_2 not in frame_text

        disconnect.return_value = True
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    _run_coro(_test())
