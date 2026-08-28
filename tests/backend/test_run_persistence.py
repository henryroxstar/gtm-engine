"""A2 — V015 persistence: run_nodes/run_blocks rows mirror the stream events.

The snapshot's nodes[]/content[] are READS of these tables, so the mirror
property (rows == what was published) is what makes kill-and-reconnect recovery
sound. StateConn (tests/backend/_protocol1.py) emulates the exact upsert SQL.
Convention: no pytest-asyncio; DB emulated in-memory.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from tests.backend._protocol1 import (  # noqa: E402
    REPO,
    StateConn,
    drive_gate,
    fake_executor,
    pack_run_harness,
)
from tests.backend.test_packs_api import PROFILE, _provision  # noqa: E402

_WIRE_STATES = {"queued", "running", "completed", "failed", "skipped"}


def _drain(q: asyncio.Queue) -> list[tuple[str, dict]]:
    frames = []
    while True:
        try:
            frames.append(q.get_nowait())
        except asyncio.QueueEmpty:
            return frames


async def _run_pack(ws_env, conn, executor, run_id: str):
    with pack_run_harness(conn, executor):
        await runs_router._execute_pack_run(
            MagicMock(),
            REPO,
            ws_env.ws_id,
            run_id,
            PROFILE,
            "marketing",
            "linkedin-post",
            {},
            entitlement="pro_plus",
        )


def test_rows_mirror_stream_events(ws_env):
    """After a clean run: one run_nodes row per stage in the WIRE vocabulary, one
    markdown run_blocks row per stage with first-insert ord, and the persisted
    blocks are byte-identical to the blocks that went out as content events."""
    from agent.pipeline import STAGES

    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id = str(uuid.uuid4())
    conn = StateConn()

    runs_router._run_subscribers.pop(run_id, None)
    q = runs_router._subscribe(run_id)
    try:
        asyncio.run(_run_pack(ws_env, conn, fake_executor([]), run_id))
        frames = _drain(q)
    finally:
        runs_router._unsubscribe(run_id, q)

    # Nodes: every stage completed; only wire-vocabulary states ever stored.
    assert set(conn.nodes) == set(STAGES)
    assert all(row["state"] == "completed" for row in conn.nodes.values())
    assert {row["state"] for row in conn.nodes.values()} <= _WIRE_STATES

    # Blocks: one per stage, ord = completion order (linear graph ⇒ STAGES order).
    ordered = sorted(conn.blocks.values(), key=lambda b: b["ord"])
    assert [b["block"]["id"] for b in ordered] == [f"node-{s}" for s in STAGES]
    assert [b["ord"] for b in ordered] == list(range(len(STAGES)))

    # Mirror: persisted rows == published events, both directions.
    node_events = [d for e, d in frames if e == "node"]
    assert {d["node_id"]: d["state"] for d in node_events if d["state"] == "completed"} == {
        n: row["state"] for n, row in conn.nodes.items()
    }
    published_blocks = {d["block"]["id"]: d["block"] for e, d in frames if e == "content"}
    assert published_blocks == {bid: b["block"] for bid, b in conn.blocks.items()}
    assert conn.run_status[-1] == "ok"


def test_gate_pause_persists_running_not_awaiting(ws_env):
    """A gate-paused node stores 'running' (awaiting_approval is a RUN status, not
    a node state) — asserted WHILE paused, then the run resumes to completion."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    pending = ws_env.content_root / PROFILE / "plans" / ".pending"
    pending.mkdir(parents=True)
    (pending / "2026-32.draft.json").write_text("[]")

    run_id = str(uuid.uuid4())
    conn = StateConn()
    paused_state: dict = {}

    async def _go():
        task = asyncio.create_task(
            _run_pack(ws_env, conn, fake_executor([], awaiting_on="plan"), run_id)
        )
        for _ in range(800):
            if run_id in runs_router._gate_events:
                break
            await asyncio.sleep(0.005)
        paused_state.update(conn.nodes.get("plan") or {})
        await drive_gate(run_id, "approve")
        await asyncio.wait_for(task, timeout=10)

    asyncio.run(_go())

    assert paused_state.get("state") == "running", "paused node must read 'running' on the wire"
    assert conn.nodes["plan"]["state"] == "completed"
    assert conn.run_status[-1] == "ok"


def test_failed_node_persists_error_and_stops(ws_env):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id = str(uuid.uuid4())
    conn = StateConn()

    asyncio.run(_run_pack(ws_env, conn, fake_executor([], fail_on="research"), run_id))

    assert conn.nodes["research"]["state"] == "failed"
    assert "research exploded" in (conn.nodes["research"]["error"] or "")
    # Never-dispatched downstream nodes have no row (absent = not started).
    assert "studio" not in conn.nodes and "publish" not in conn.nodes
    assert conn.run_status[-1] == "failed"


def test_get_run_returns_protocol1_nodes_and_content():
    """Polling parity: GET /runs/{id} exposes the same nodes/content a stream
    snapshot would; None (not []) when nothing is persisted (prompt/pre-A2 runs)."""
    ws_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    run_row = {
        "id": run_id,
        "status": "running",
        "profile_name": "p",
        "output": None,
        "error": None,
        "pending_gate": None,
        "pending_content": None,
    }
    block = {"id": "node-radar", "type": "markdown", "props": {"text": "t"}, "fallback_text": "t"}

    conn = AsyncMock()
    conn.fetchrow.return_value = run_row
    conn.fetch.side_effect = [
        [{"node_id": "radar", "state": "completed"}],
        [{"block": block}],
        [],  # second request: no node rows
        [],  # second request: no block rows
    ]

    from contextlib import asynccontextmanager
    from unittest.mock import patch

    @asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    app = FastAPI()
    app.include_router(runs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    ctx = WorkspaceCtx(user_id="u", workspace_id=ws_id, entitlement="pro")
    app.dependency_overrides[require_auth] = lambda: ctx

    with patch.object(runs_router, "workspace_scope", _scope), TestClient(app) as client:
        body = client.get(f"/v1/runs/{run_id}").json()
        assert body["nodes"] == [{"id": "radar", "state": "completed"}]
        assert body["content"] == [block]

        body2 = client.get(f"/v1/runs/{run_id}").json()
        assert body2["nodes"] is None
        assert body2["content"] is None
