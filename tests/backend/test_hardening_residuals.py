"""Regression tests for the 2026-07-05 hardening follow-ups (flagged residuals).

Covers the behaviour-changing fixes:
  - content_sha is REQUIRED and enforced on every gate decision (H9 bypass closed)
  - the per-workspace run slot is freed idempotently AND even when a run task is
    cancelled before its body runs (the slot-leak fix)
  - the entitlement-sync handler records + applies a sync in ONE transaction, short-
    circuiting duplicate/stale syncs before the entitlement UPDATE (reorder TOCTOU).
    (Same guarantees the former RevenueCat webhook had; billing now owned by the billing service —
    see the billing-boundary design doc.)

SDK-free / mock-pool, asyncio.run per repo convention (see test_cost_guard.py).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.routers import entitlement as entitlement_router
from backend.routers import runs as runs_router
from backend.schemas import GateRequest


def _scope(conn):
    @asynccontextmanager
    async def _s(pool, workspace_id):
        yield conn

    return _s


# ── #1 content_sha REQUIRED + enforced ────────────────────────────────────────


def test_gate_request_requires_content_sha():
    # Omitting content_sha is now a schema error (was silently optional → bypass).
    with pytest.raises(ValidationError):
        GateRequest(decision="approve")
    with pytest.raises(ValidationError):
        GateRequest(decision="approve", content_sha="")  # min_length=1


def _drive_decide_gate(pending_content: str, content_sha: str):
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "status": "awaiting_approval",
        "pending_gate": "⟦GATE:publish⟧",
        "pending_content": pending_content,
    }
    request = MagicMock()
    request.app.state.pool = MagicMock()
    ws = MagicMock()
    ws.workspace_id = "ws1"
    body = GateRequest(decision="approve", content_sha=content_sha)

    async def _go():
        runs_router._gate_events["run1"] = asyncio.Event()
        runs_router._gate_decisions.pop("run1", None)
        try:
            with patch.object(runs_router, "workspace_scope", _scope(conn)):
                return await runs_router.decide_gate("run1", body, ws, request)
        finally:
            runs_router._gate_events.pop("run1", None)
            runs_router._gate_decisions.pop("run1", None)

    return asyncio.run(_go())


def test_decide_gate_accepts_matching_sha():
    good = runs_router._content_sha("the exact bytes")
    result = _drive_decide_gate("the exact bytes", good)
    assert result["decision"] == "approve"


def test_decide_gate_rejects_sha_mismatch():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        _drive_decide_gate("the exact bytes", "deadbeef" * 8)
    assert ei.value.status_code == 409


# ── #6 run slot: idempotent release + freed on pre-start cancel ────────────────


def test_release_run_slot_idempotent():
    runs_router._workspace_runs.clear()
    runs_router._workspace_runs["ws"] = {"r1", "r2"}
    runs_router._release_run_slot("ws", "r1")
    assert runs_router._workspace_runs["ws"] == {"r2"}
    runs_router._release_run_slot("ws", "r1")  # double release — no-op
    assert runs_router._workspace_runs["ws"] == {"r2"}
    runs_router._release_run_slot("ws", "r2")  # last one → key popped
    assert "ws" not in runs_router._workspace_runs
    runs_router._release_run_slot("ws", "never-reserved")  # unknown — safe


def test_track_run_frees_slot_on_completion():
    runs_router._workspace_runs.clear()
    runs_router._workspace_runs["ws"] = {"r1"}

    async def _go():
        async def _noop():
            return

        # pool arg is unused unless COST_RESERVATION_ENABLED (off here).
        t = runs_router._track_run(asyncio.create_task(_noop()), None, "ws", "r1")
        await t
        await asyncio.sleep(0)  # let the done-callback run

    asyncio.run(_go())
    assert "ws" not in runs_router._workspace_runs


def test_track_run_frees_slot_on_precancel():
    # The leak this fixes: a task cancelled by shutdown-drain BEFORE its body runs.
    runs_router._workspace_runs.clear()
    runs_router._workspace_runs["ws"] = {"r1"}

    async def _go():
        async def _sleep():
            await asyncio.sleep(10)

        t = runs_router._track_run(asyncio.create_task(_sleep()), None, "ws", "r1")
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)

    asyncio.run(_go())
    assert "ws" not in runs_router._workspace_runs


# ── #5 entitlement sync: record + apply in one transaction, short-circuit dup/stale ──


def _drive_apply_sync(*, insert_result: str, newer: int, version):
    conn = AsyncMock()
    conn.fetchrow.return_value = {"lock": 1}  # FOR UPDATE lock row (truthy)
    conn.execute.side_effect = [insert_result, "UPDATE 1"]
    conn.fetchval.return_value = newer
    request = MagicMock()
    request.app.state.pool = MagicMock()

    async def _go():
        with patch.object(entitlement_router, "workspace_scope", _scope(conn)):
            return (
                await entitlement_router._apply_sync(
                    request, "ws1", "pro", 50.0, "active", "sync1", version
                ),
                conn,
            )

    return asyncio.run(_go())


def test_apply_sync_duplicate_skips_update():
    outcome, conn = _drive_apply_sync(insert_result="INSERT 0 0", newer=0, version=1000)
    assert outcome == "duplicate"
    assert conn.execute.await_count == 1  # INSERT only — no entitlement UPDATE


def test_apply_sync_stale_skips_update():
    outcome, conn = _drive_apply_sync(insert_result="INSERT 0 1", newer=1, version=1000)
    assert outcome == "stale"
    assert conn.execute.await_count == 1  # a newer sync already applied — no UPDATE


def test_apply_sync_new_applies_update():
    outcome, conn = _drive_apply_sync(insert_result="INSERT 0 1", newer=0, version=2000)
    assert outcome == "new"
    assert conn.execute.await_count == 2  # INSERT + entitlement UPDATE
    # The FOR UPDATE lock was taken before any write (serializes concurrent syncs).
    lock_sql = conn.fetchrow.call_args.args[0]
    assert "FOR UPDATE" in lock_sql
