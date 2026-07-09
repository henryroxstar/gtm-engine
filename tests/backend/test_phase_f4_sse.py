"""Unit tests for Phase F.3 — run-progress SSE streaming (GET /v1/runs/{id}/stream).

Repo convention: no pytest-asyncio. Async coroutines are driven with asyncio.run();
the DB is mocked (AsyncMock conn + workspace_scope patched at both
backend.routers.runs and backend.deps). The streaming endpoint can't be exercised
with the sync TestClient (it doesn't stream bodies), so streaming assertions use
httpx.AsyncClient + httpx.ASGITransport + aiter_lines(). Pre-stream error paths
(404, 429) use the sync TestClient since the exception is raised before the body.

All DB and pool calls are mocked — no live Postgres required.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")
os.environ.setdefault("BACKEND_JWT_EXPIRE_MINUTES", "60")
os.environ.setdefault("BACKEND_REFRESH_EXPIRE_DAYS", "30")

from backend.auth import create_access_token
from backend.deps import WorkspaceCtx
from backend.routers import runs as runs_router

WS_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
RUN_ID = "00000000-0000-0000-0000-000000000010"

_ENTITLEMENT_ROW = {"entitlement": "pro"}
_EXISTS_ROW = {"?column?": 1}


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(USER_ID, WS_ID)}"}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(runs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    return app


def _scope_factory(conn):
    @asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    return _scope


def _patched(conn):
    scope = _scope_factory(conn)
    return patch.multiple(
        "backend.routers.runs",
        workspace_scope=scope,
    ), patch("backend.deps.workspace_scope", scope)


class _FakeRequest:
    """Minimal Request stand-in for calling stream_run() directly.

    httpx's ASGITransport buffers the whole response before yielding any bytes,
    which deadlocks a never-ending stream — so the streaming tests drive the
    StreamingResponse.body_iterator directly for full, incremental event-loop
    control. The endpoint only needs request.app.state.pool and is_disconnected().
    """

    def __init__(self, pool):
        self.app = SimpleNamespace(state=SimpleNamespace(pool=pool))
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


def _ws() -> WorkspaceCtx:
    return WorkspaceCtx(USER_ID, WS_ID, "pro")


def _parse_frames(text: str) -> list[tuple[str, dict]]:
    """Parse accumulated text/event-stream output into (event, data) pairs."""
    events: list[tuple[str, dict]] = []
    cur: str | None = None
    for line in text.split("\n"):
        if line.startswith("event:"):
            cur = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and cur is not None:
            events.append((cur, json.loads(line.split(":", 1)[1].strip())))
            cur = None
    return events


# ── pure helpers ────────────────────────────────────────────────────────────────


def test_sse_frame_format():
    frame = runs_router._sse_frame("status", {"run_id": RUN_ID, "status": "running"}, seq=3)
    assert frame.startswith("id: 3\nevent: status\ndata: ")
    assert frame.endswith("\n\n")
    # data line is compact JSON
    data_line = frame.splitlines()[2]
    assert json.loads(data_line[len("data: ") :])["status"] == "running"


def test_subscribe_publish_unsubscribe_roundtrip():
    runs_router._run_subscribers.pop(RUN_ID, None)
    q = runs_router._subscribe(RUN_ID)
    runs_router._publish_event(
        RUN_ID,
        "awaiting_approval",
        {"run_id": RUN_ID, "pending_gate": "⟦GATE:plan⟧", "pending_content": "draft bytes"},
    )
    event, data = q.get_nowait()
    assert event == "awaiting_approval"
    assert data["pending_content"] == "draft bytes"
    runs_router._unsubscribe(RUN_ID, q)
    # key dropped once the last subscriber leaves — no leak
    assert RUN_ID not in runs_router._run_subscribers


def test_publish_with_no_subscribers_is_noop():
    runs_router._run_subscribers.pop(RUN_ID, None)
    # Must not raise even though nobody is listening.
    runs_router._publish_event(RUN_ID, "status", {"run_id": RUN_ID, "status": "running"})


def test_publish_never_raises_drops_oldest_when_full():
    runs_router._run_subscribers.pop(RUN_ID, None)
    q = runs_router._subscribe(RUN_ID)
    # Fill beyond capacity — drop-oldest, never raise into the publisher.
    for i in range(runs_router._STREAM_QUEUE_MAX + 10):
        runs_router._publish_event(RUN_ID, "status", {"i": i})
    assert q.qsize() == runs_router._STREAM_QUEUE_MAX
    runs_router._unsubscribe(RUN_ID, q)


# ── streaming endpoint ────────────────────────────────────────────────────────────


def test_stream_snapshot_then_live_delivery_then_cleanup():
    """Non-terminal run: snapshot on connect, then a published live `status` and a
    terminal `done` are delivered, and the generator's finally deregisters the queue
    (no subscriber/stream-count leak)."""
    conn = AsyncMock()
    # stream_run is called directly (require_auth bypassed): existence-check, then snapshot.
    conn.fetchrow.side_effect = [
        _EXISTS_ROW,
        {
            "id": RUN_ID,
            "status": "running",
            "output": None,
            "error": None,
            "pending_gate": None,
            "pending_content": None,
        },
    ]
    runs_router._run_subscribers.pop(RUN_ID, None)
    runs_router._workspace_stream_count.pop(WS_ID, None)

    async def _go():
        with patch("backend.routers.runs.workspace_scope", _scope_factory(conn)):
            req = _FakeRequest(MagicMock())
            resp = await runs_router.stream_run(RUN_ID, _ws(), req)
            it = resp.body_iterator
            chunks = [await anext(it), await anext(it)]  # retry preamble, then snapshot frame
            assert "event: snapshot" in chunks[1]
            # subscription is live now → live events reach the stream
            runs_router._publish_event(
                RUN_ID, "status", {"run_id": RUN_ID, "status": "running", "ts": "x"}
            )
            chunks.append(await anext(it))
            assert "event: status" in chunks[-1]
            runs_router._publish_event(RUN_ID, "done", {"run_id": RUN_ID, "status": "ok"})
            chunks.append(await anext(it))
            assert "event: done" in chunks[-1]
            with pytest.raises(StopAsyncIteration):
                await anext(it)  # generator returns after `done`

        events = _parse_frames("".join(chunks))
        assert [e for e, _ in events] == ["snapshot", "status", "done"]
        assert events[0][1]["status"] == "running"
        # finally ran on return → no leak
        assert RUN_ID not in runs_router._run_subscribers
        assert WS_ID not in runs_router._workspace_stream_count

    asyncio.run(_go())


def test_stream_terminal_run_emits_done_and_closes():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _EXISTS_ROW,
        {
            "id": RUN_ID,
            "status": "ok",
            "output": "final output",
            "error": None,
            "pending_gate": None,
            "pending_content": None,
        },
    ]
    runs_router._run_subscribers.pop(RUN_ID, None)
    runs_router._workspace_stream_count.pop(WS_ID, None)

    async def _go():
        with patch("backend.routers.runs.workspace_scope", _scope_factory(conn)):
            req = _FakeRequest(MagicMock())
            resp = await runs_router.stream_run(RUN_ID, _ws(), req)
            chunks = [c async for c in resp.body_iterator]
        events = _parse_frames("".join(chunks))
        assert [e for e, _ in events] == ["snapshot", "done"]
        assert events[1][1]["status"] == "ok"
        assert events[1][1]["output"] == "final output"
        # terminal run never registered a long-lived subscriber-leak
        assert RUN_ID not in runs_router._run_subscribers
        assert WS_ID not in runs_router._workspace_stream_count

    asyncio.run(_go())


def test_stream_heartbeat_on_idle():
    """An idle non-terminal stream emits a `ping` when the queue wait times out."""
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _EXISTS_ROW,
        {
            "id": RUN_ID,
            "status": "running",
            "output": None,
            "error": None,
            "pending_gate": None,
            "pending_content": None,
        },
    ]
    runs_router._run_subscribers.pop(RUN_ID, None)
    runs_router._workspace_stream_count.pop(WS_ID, None)

    async def _go():
        with (
            patch("backend.routers.runs.workspace_scope", _scope_factory(conn)),
            patch.object(runs_router, "_STREAM_HEARTBEAT_S", 0.05),
        ):
            req = _FakeRequest(MagicMock())
            resp = await runs_router.stream_run(RUN_ID, _ws(), req)
            it = resp.body_iterator
            await anext(it)  # retry preamble
            await anext(it)  # snapshot
            ping = await anext(it)  # idle → heartbeat after 0.05s
            assert "event: ping" in ping
            await it.aclose()  # closing runs the generator's finally
        assert RUN_ID not in runs_router._run_subscribers

    asyncio.run(_go())


def test_stream_unknown_run_404():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, None]  # entitlement, then no run row
    app = _make_app()
    p1, p2 = _patched(conn)
    with p1, p2, TestClient(app) as client:
        resp = client.get(f"/v1/runs/{RUN_ID}/stream", headers=_auth_header())
    assert resp.status_code == 404


def test_stream_per_workspace_cap_429():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _ENTITLEMENT_ROW
    ]  # require_auth only — cap rejects before any read
    app = _make_app()
    p1, p2 = _patched(conn)
    runs_router._workspace_stream_count[WS_ID] = runs_router._MAX_STREAMS_PER_WORKSPACE
    try:
        with p1, p2, TestClient(app) as client:
            resp = client.get(f"/v1/runs/{RUN_ID}/stream", headers=_auth_header())
        assert resp.status_code == 429
    finally:
        runs_router._workspace_stream_count.pop(WS_ID, None)


def test_cancel_publishes_done_to_stream():
    conn = AsyncMock()
    # require_auth entitlement, then cancel_run's SELECT status (non-terminal)
    conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "running"}]
    app = _make_app()
    p1, p2 = _patched(conn)

    runs_router._run_subscribers.pop(RUN_ID, None)
    q = runs_router._subscribe(RUN_ID)
    try:
        with p1, p2, TestClient(app) as client:
            resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        event, data = q.get_nowait()
        assert event == "done"
        assert data["status"] == "rejected"
    finally:
        runs_router._unsubscribe(RUN_ID, q)


def test_get_run_polling_unchanged_regression():
    """Polling GET /runs/{id} must still return status + pending_content (SSE is additive)."""
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _ENTITLEMENT_ROW,
        {
            "id": RUN_ID,
            "status": "awaiting_approval",
            "profile_name": "test",
            "output": None,
            "error": None,
            "pending_gate": "⟦GATE:publish⟧",
            "pending_content": "draft for approval",
        },
    ]
    app = _make_app()
    p1, p2 = _patched(conn)
    with p1, p2, TestClient(app) as client:
        resp = client.get(f"/v1/runs/{RUN_ID}", headers=_auth_header())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "awaiting_approval"
    assert body["pending_content"] == "draft for approval"
