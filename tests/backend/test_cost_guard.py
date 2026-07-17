"""Backend pre-call cost-cap guard (the P2 hole the cost PRD closes).

Drives _execute_run directly (asyncio.run — repo convention) with a fake pool /
sessions, asserting that an over-cap workspace is refused BEFORE any paid brain call:
the run is marked failed and sessions.run is never invoked. Mirrors the SDK-free,
mock-pool style of the other backend tests.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from backend.routers import runs as runs_router

WS_ID = "00000000-0000-0000-0000-000000000001"
RUN_ID = "00000000-0000-0000-0000-000000000010"


class _Conn:
    def __init__(self) -> None:
        self.executed: list[tuple] = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


class _Sessions:
    """Fake BackendSessionStore: records run() calls, streams nothing."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def run(self, *args):
        self.calls.append(args)

        async def _agen():
            return
            yield ""  # pragma: no cover — makes this an async generator

        return _agen()


def _fake_scope_factory(conn):
    @asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    return _scope


def _drive(over_cap: bool):
    """Run _execute_run with acheck_budget stubbed; return (conn, sessions)."""
    conn = _Conn()
    sessions = _Sessions()

    async def _go():
        with (
            patch.object(runs_router, "workspace_scope", _fake_scope_factory(conn)),
            patch.object(runs_router, "acheck_budget", AsyncMock(return_value=not over_cap)),
        ):
            await runs_router._execute_run(
                MagicMock(),  # pool
                sessions,
                WS_ID,
                RUN_ID,
                "example2",
                "do the thing",
                False,
            )

    asyncio.run(_go())
    return conn, sessions


def test_over_cap_run_refused_before_brain_call():
    conn, sessions = _drive(over_cap=True)
    # The run was marked failed with a cost-cap reason...
    joined = " ".join(sql for sql, _ in conn.executed)
    assert "failed" in joined
    assert "monthly cost cap reached" in joined
    # ...and the paid session was never started.
    assert sessions.calls == []


def test_under_cap_run_proceeds_to_session():
    conn, sessions = _drive(over_cap=False)
    # Under cap → status set to running and the session stream is started.
    joined = " ".join(sql for sql, _ in conn.executed)
    assert "running" in joined
    assert len(sessions.calls) == 1
