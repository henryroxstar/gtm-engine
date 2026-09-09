"""A2 — protocol-1 stream: every frame schema-valid, snapshot rebuild, overflow.

Protocol-0 regression is tests/backend/test_phase_f4_sse.py, which must stay
green UNMODIFIED in this PR — no assertion here relaxes anything it pins.
Convention: no pytest-asyncio (asyncio.run bodies); DB mocked; frames validated
against schemas/run-event.schema.json via tests/backend/_protocol1.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from tests.backend._protocol1 import (  # noqa: E402  # noqa: E402
    REPO,
    SCOPE_MODULES,
    FakeRequest,
    StateConn,
    drive_gate,
    fake_executor,
    pack_run_harness,
    patch_everywhere,
    validate_frame,
)
from tests.backend.test_packs_api import PROFILE, _provision  # noqa: E402

WS_ID = "00000000-0000-0000-0000-000000000001"
RUN_ID = "00000000-0000-0000-0000-000000000010"


def _drain(q: asyncio.Queue) -> list[tuple[str, dict]]:
    frames = []
    while True:
        try:
            frames.append(q.get_nowait())
        except asyncio.QueueEmpty:
            return frames


def _assert_all_valid(frames: list[tuple[str, dict]]) -> None:
    for event, data in frames:
        errs = validate_frame(event, data)
        assert not errs, (event, errs)


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


# ── full-run frame validation ─────────────────────────────────────────────────


def test_pack_run_every_frame_schema_valid(ws_env):
    """The A2 acceptance: a clean pack run emits only schema-valid frames —
    status, per-node lifecycle, one markdown content block per node, done ok."""
    from agent.pipeline import STAGES

    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id = str(uuid.uuid4())
    conn = StateConn()
    recorded: list[str] = []

    runs_router._run_subscribers.pop(run_id, None)
    q = runs_router._subscribe(run_id)
    try:
        asyncio.run(_run_pack(ws_env, conn, fake_executor(recorded), run_id))
        frames = _drain(q)
    finally:
        runs_router._unsubscribe(run_id, q)

    _assert_all_valid(frames)
    kinds = [e for e, _ in frames]
    assert kinds[0] == "status" and frames[0][1]["status"] == "running"
    assert kinds[-1] == "done" and frames[-1][1]["status"] == "ok"

    node_frames = [d for e, d in frames if e == "node"]
    for stage in STAGES:
        states = [d["state"] for d in node_frames if d["node_id"] == stage]
        assert states == ["running", "completed"], (stage, states)

    blocks = [d["block"] for e, d in frames if e == "content"]
    assert {b["id"] for b in blocks} == {f"node-{s}" for s in STAGES}
    for b in blocks:
        assert b["type"] == "markdown"
        assert b["fallback_text"]
        # Emitters produce flat blocks — trivially inside the normative depth ≤ 8.
        assert not b.get("children")


def test_gate_pause_frame_carries_protocol1_fields(ws_env):
    """awaiting_approval on a pack run carries gate/node_id/pending_content_sha,
    and the sha is of the EXACT draft bytes shown (H9 binding input)."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    pending = ws_env.content_root / PROFILE / "plans" / ".pending"
    pending.mkdir(parents=True)
    draft = json.dumps([{"id": "item-1", "status": "draft"}])
    (pending / "2026-32.draft.json").write_text(draft)

    run_id = str(uuid.uuid4())
    conn = StateConn()
    recorded: list[str] = []

    async def _go():
        task = asyncio.create_task(
            _run_pack(ws_env, conn, fake_executor(recorded, awaiting_on="plan"), run_id)
        )
        await drive_gate(run_id, "approve")
        await asyncio.wait_for(task, timeout=10)

    runs_router._run_subscribers.pop(run_id, None)
    q = runs_router._subscribe(run_id)
    try:
        asyncio.run(_go())
        frames = _drain(q)
    finally:
        runs_router._unsubscribe(run_id, q)

    _assert_all_valid(frames)
    gate = next(d for e, d in frames if e == "awaiting_approval")
    assert gate["gate"] == "plan"
    assert gate["node_id"] == "plan"
    assert gate["pending_content"] == draft
    assert gate["pending_content_sha"] == hashlib.sha256(draft.encode()).hexdigest()
    # Resume after approve: another running status, then the run completes ok.
    kinds = [e for e, _ in frames]
    assert kinds.count("status") >= 2
    assert frames[-1][0] == "done" and frames[-1][1]["status"] == "ok"


# ── gate_resolved emission (endpoint-driven) ──────────────────────────────────


def _gate_client(conn) -> TestClient:
    app = FastAPI()
    app.include_router(runs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    ctx = WorkspaceCtx(user_id=str(uuid.uuid4()), workspace_id=WS_ID, entitlement="pro")
    app.dependency_overrides[require_auth] = lambda: ctx

    @asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    client = TestClient(app)
    # Every module that binds it — the gate/cancel handlers read through the decisions
    # service since Phase 1b, so the router alias alone leaves the real pool live.
    client._scope_patch = patch_everywhere(  # type: ignore[attr-defined]
        SCOPE_MODULES, "workspace_scope", _scope
    )
    return client


def _awaiting_row(content: str) -> dict:
    return {
        "status": "awaiting_approval",
        "pending_gate": "⟦GATE:plan⟧",
        "pending_content": content,
    }


def test_decide_gate_emits_gate_resolved():
    content = "draft body"
    conn = AsyncMock()
    conn.fetchrow.return_value = _awaiting_row(content)
    client = _gate_client(conn)

    runs_router._run_subscribers.pop(RUN_ID, None)
    runs_router._gate_events[RUN_ID] = asyncio.Event()
    runs_router._gate_decisions.pop(RUN_ID, None)
    q = runs_router._subscribe(RUN_ID)
    sha = hashlib.sha256(content.encode()).hexdigest()
    try:
        with client._scope_patch:  # type: ignore[attr-defined]
            resp = client.post(
                f"/v1/runs/{RUN_ID}/gate", json={"decision": "approve", "content_sha": sha}
            )
        assert resp.status_code == 200
        frames = _drain(q)
        _assert_all_valid(frames)
        event, data = frames[-1]
        assert event == "gate_resolved"
        assert data["decision"] == "approve"
        assert data["content_sha"] == sha
        assert data["edited"] is False
    finally:
        runs_router._unsubscribe(RUN_ID, q)
        runs_router._gate_events.pop(RUN_ID, None)
        runs_router._gate_decisions.pop(RUN_ID, None)


def test_decide_gate_edit_resolved_sha_is_edited_bytes():
    """On edit, gate_resolved.content_sha is the sha of the SUBSTITUTED bytes —
    the content of record — not of the draft the operator started from."""
    content, edited = "draft body", "operator-edited body"
    conn = AsyncMock()
    conn.fetchrow.return_value = _awaiting_row(content)
    client = _gate_client(conn)

    runs_router._run_subscribers.pop(RUN_ID, None)
    runs_router._gate_events[RUN_ID] = asyncio.Event()
    runs_router._gate_decisions.pop(RUN_ID, None)
    q = runs_router._subscribe(RUN_ID)
    try:
        with client._scope_patch:  # type: ignore[attr-defined]
            resp = client.post(
                f"/v1/runs/{RUN_ID}/gate",
                json={
                    "decision": "edit",
                    "edited_content": edited,
                    "content_sha": hashlib.sha256(content.encode()).hexdigest(),
                },
            )
        assert resp.status_code == 200
        event, data = _drain(q)[-1]
        assert event == "gate_resolved"
        assert not validate_frame(event, data)
        assert data["content_sha"] == hashlib.sha256(edited.encode()).hexdigest()
        assert data["edited"] is True
    finally:
        runs_router._unsubscribe(RUN_ID, q)
        runs_router._gate_events.pop(RUN_ID, None)
        runs_router._gate_decisions.pop(RUN_ID, None)


def test_cancel_open_gate_emits_gate_resolved_reject():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"status": "awaiting_approval"}
    client = _gate_client(conn)

    runs_router._run_subscribers.pop(RUN_ID, None)
    runs_router._gate_events[RUN_ID] = asyncio.Event()
    runs_router._gate_decisions.pop(RUN_ID, None)
    q = runs_router._subscribe(RUN_ID)
    try:
        with client._scope_patch:  # type: ignore[attr-defined]
            resp = client.post(f"/v1/runs/{RUN_ID}/cancel")
        assert resp.status_code == 200
        frames = _drain(q)
        _assert_all_valid(frames)
        assert [e for e, _ in frames] == ["gate_resolved", "done"]
        assert frames[0][1]["decision"] == "reject"
        assert frames[1][1]["status"] == "rejected"
    finally:
        runs_router._unsubscribe(RUN_ID, q)
        runs_router._gate_events.pop(RUN_ID, None)
        runs_router._gate_decisions.pop(RUN_ID, None)
        runs_router._cancelled_runs.discard(RUN_ID)


# ── snapshot rebuild + overflow close ─────────────────────────────────────────


def _scope_factory(conn):
    @asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    return _scope


def _ws() -> WorkspaceCtx:
    return WorkspaceCtx("u", WS_ID, "pro")


def test_snapshot_carries_protocol1_nodes_and_content():
    """A (re)connecting client rebuilds the DAG + content pane from the snapshot
    alone (kill-connection recovery — there is no replay). JSONB may surface as
    dict or str depending on codec; both must land as objects on the wire."""
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"?column?": 1},
        {
            "id": RUN_ID,
            "status": "running",
            "output": None,
            "error": None,
            "pending_gate": None,
            "pending_content": None,
        },
    ]
    block_a = {"id": "node-radar", "type": "markdown", "props": {"text": "x"}, "fallback_text": "x"}
    block_b = {"id": "file-1", "type": "file", "props": {"artifact_id": "1"}, "fallback_text": "f"}
    conn.fetch.side_effect = [
        [{"node_id": "plan", "state": "running"}, {"node_id": "radar", "state": "completed"}],
        [{"block": json.dumps(block_a)}, {"block": block_b}],
    ]
    runs_router._run_subscribers.pop(RUN_ID, None)
    runs_router._workspace_stream_count.pop(WS_ID, None)

    async def _go():
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope_factory(conn)):
            resp = await runs_router.stream_run(RUN_ID, _ws(), FakeRequest(MagicMock()))
            it = resp.body_iterator
            await anext(it)  # retry preamble
            frame = await anext(it)
            await it.aclose()
        assert "event: snapshot" in frame
        data = json.loads(frame.split("data: ", 1)[1].strip())
        assert not validate_frame("snapshot", data)
        # protocol-0 fields intact (additive evolution rule)
        for key in ("run_id", "status", "pending_gate", "pending_content"):
            assert key in data
        assert data["protocol"] == 2  # A5: durable seq + ?since= resume
        assert data["nodes"] == [
            {"id": "plan", "state": "running"},
            {"id": "radar", "state": "completed"},
        ]
        assert data["content"] == [block_a, block_b]

    asyncio.run(_go())
    assert RUN_ID not in runs_router._run_subscribers


def test_overflow_closes_stream_instead_of_dropping():
    """Backpressure contract: a client that fell behind gets a CLOSED stream (and
    resyncs from a fresh snapshot) — never a silently thinned event sequence."""
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"?column?": 1},
        {
            "id": RUN_ID,
            "status": "running",
            "output": None,
            "error": None,
            "pending_gate": None,
            "pending_content": None,
        },
    ]
    conn.fetch.side_effect = [[], []]
    runs_router._run_subscribers.pop(RUN_ID, None)
    runs_router._workspace_stream_count.pop(WS_ID, None)

    async def _go():
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope_factory(conn)):
            resp = await runs_router.stream_run(RUN_ID, _ws(), FakeRequest(MagicMock()))
            it = resp.body_iterator
            await anext(it)  # retry preamble
            await anext(it)  # snapshot — subscription is live now
            for i in range(runs_router._STREAM_QUEUE_MAX + 1):  # one past capacity
                runs_router._publish_event(
                    RUN_ID, "status", {"run_id": RUN_ID, "status": "running"}
                )
            delivered = 0
            with pytest.raises(StopAsyncIteration):
                while True:
                    chunk = await anext(it)
                    assert "event: done" not in chunk
                    delivered += 1
        # Oldest event was dropped to make room for the poison → close.
        assert delivered == runs_router._STREAM_QUEUE_MAX - 1

    asyncio.run(_go())
    # Generator finally ran: no subscriber leak after the forced close.
    assert RUN_ID not in runs_router._run_subscribers
