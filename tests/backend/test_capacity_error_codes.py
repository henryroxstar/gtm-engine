"""The three 429s a client meets are distinguishable by ``error.code`` (M-02).

Each needs a different reaction: ``too_many_concurrent_runs`` — don't retry, show the in-flight
runs; ``too_many_streams`` — close a stream; ``rate_limit_exceeded`` (the per-IP window) — back
off. Before this, all three rendered as ``rate_limit_exceeded``. Covered here: the two
per-workspace caps. The per-IP window is slowapi's own handler, pinned by
``tests/backend/test_error_envelope.py``. No DB — mocked throughout.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")
os.environ.setdefault("BACKEND_JWT_EXPIRE_MINUTES", "60")
os.environ.setdefault("BACKEND_REFRESH_EXPIRE_DAYS", "30")

from backend.auth import create_access_token  # noqa: E402
from backend.errors import http_exception_handler, register_error_handlers  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from backend.services.runs import admission  # noqa: E402
from backend.services.runs.state import (  # noqa: E402
    _MAX_CONCURRENT_RUNS_PER_WORKSPACE,
    _MAX_STREAMS_PER_WORKSPACE,
)

WS_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
RUN_ID = "00000000-0000-0000-0000-000000000010"


def _envelope(exc: HTTPException) -> dict:
    return json.loads(asyncio.run(http_exception_handler(MagicMock(), exc)).body)


def test_concurrent_run_cap_is_too_many_concurrent_runs():
    conn = AsyncMock()
    conn.fetchval.return_value = _MAX_CONCURRENT_RUNS_PER_WORKSPACE
    with pytest.raises(HTTPException) as refused:
        asyncio.run(admission._reserve_cap(conn, WS_ID))
    # The advisory lock is taken BEFORE the count, or two admissions both read 2 and both
    # insert — read-committed hands each an unchanged snapshot of the other's uncommitted row.
    calls = [name for name, *_ in conn.mock_calls if name in ("execute", "fetchval")]
    assert calls[:2] == ["execute", "fetchval"]
    assert "pg_advisory_xact_lock" in conn.execute.await_args.args[0]
    body = _envelope(refused.value)
    assert refused.value.status_code == 429
    assert body["error"]["code"] == "too_many_concurrent_runs"
    assert body["error"]["details"] == {
        "max": _MAX_CONCURRENT_RUNS_PER_WORKSPACE,
        "in_flight": _MAX_CONCURRENT_RUNS_PER_WORKSPACE,
    }


def test_under_the_run_cap_admits():
    conn = AsyncMock()
    conn.fetchval.return_value = _MAX_CONCURRENT_RUNS_PER_WORKSPACE - 1
    asyncio.run(admission._reserve_cap(conn, WS_ID))


def test_stream_cap_is_too_many_streams():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"entitlement": "pro"}

    @asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    app = FastAPI()
    app.include_router(runs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    register_error_handlers(app)
    runs_router._workspace_stream_count[WS_ID] = _MAX_STREAMS_PER_WORKSPACE
    try:
        with patch("backend.deps.workspace_scope", _scope), TestClient(app) as client:
            resp = client.get(
                f"/v1/runs/{RUN_ID}/stream",
                headers={"Authorization": f"Bearer {create_access_token(USER_ID, WS_ID)}"},
            )
    finally:
        runs_router._workspace_stream_count.pop(WS_ID, None)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "too_many_streams"
    assert resp.json()["error"]["details"] == {"max": _MAX_STREAMS_PER_WORKSPACE}
