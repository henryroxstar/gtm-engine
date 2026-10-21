"""Tests for worker eviction/sweep loop (T4), worker compute concurrency cap & gate slot release (T5),
BackendSessionStore max_warm_sessions LRU cap (T6), compose/reconcile_gates separation (T13),
and graceful drain_background_tasks (T14).
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]


def _run(coro):
    return asyncio.run(coro)


# ── T4: backend/worker.py _evict() + release_stale_reservations(90000) ────────


def test_t4_worker_defines_evict_loop_with_90000s_reservation_ttl():
    """T4 (R4): backend/worker.py defines _evict calling close_idle and release_stale_reservations with RESERVATION_TTL_SECONDS=90000."""
    worker_src = (REPO / "backend" / "worker.py").read_text(encoding="utf-8")
    tree = ast.parse(worker_src)

    evict_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_evict":
            evict_fn = node
            break
    assert evict_fn is not None, "backend/worker.py must define async def _evict()"

    mentions: set[str] = set()
    for n in ast.walk(evict_fn):
        if isinstance(n, ast.Name):
            mentions.add(n.id)
        elif isinstance(n, ast.Attribute):
            mentions.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            mentions.add(n.value)

    assert "close_idle" in mentions
    assert any("release_stale_reservations" in m for m in mentions)
    assert "RESERVATION_TTL_SECONDS" in worker_src
    assert '"90000"' in worker_src, (
        "RESERVATION_TTL_SECONDS default in worker.py must be '90000' (25h in seconds, > GATE_TIMEOUT_S=86400)"
    )


# ── T5: claim_loop concurrency cap & hold_gate compute slot release ───────────


def test_t5_claim_loop_enforces_max_concurrent_runs_and_releases_on_empty_or_false(monkeypatch):
    """T5a/b (R5): claim_loop claims at most max_concurrent_runs concurrently, and does not leak slots on None or False."""
    from backend.services.runs import queue as qmod

    monkeypatch.setenv("GTM_WORKER_MAX_CONCURRENT_RUNS", "2")
    qmod.reset_worker_slots(2)

    active = 0
    max_observed = 0
    completed: list[str] = []
    release_events = {f"run-{i}": asyncio.Event() for i in range(4)}

    queued_rows = [
        # First row fails dispatch_claimed -> False (must NOT leak slot!)
        {"id": "run-rejected", "reject": True},
        {"id": "run-0"},
        {"id": "run-1"},
        {"id": "run-2"},
        {"id": "run-3"},
    ]

    async def _fake_claim_next(_pool):
        if queued_rows:
            return queued_rows.pop(0)
        await asyncio.sleep(0.01)
        return None

    async def _fake_dispatch_claimed(_pool, _repo_root, _sessions, row):
        nonlocal active, max_observed
        if row.get("reject"):
            return False

        run_id = row["id"]
        active += 1
        max_observed = max(max_observed, active)

        async def _job():
            nonlocal active
            try:
                await release_events[run_id].wait()
                completed.append(run_id)
            finally:
                active -= 1
                qmod.release_worker_slot()

        asyncio.create_task(_job())
        return True

    async def _scenario():
        with (
            patch.object(qmod, "claim_next", side_effect=_fake_claim_next),
            patch.object(qmod, "dispatch_claimed", side_effect=_fake_dispatch_claimed),
            patch.object(qmod, "wait_for_wakeup", AsyncMock(return_value=None)),
        ):
            loop_task = asyncio.create_task(qmod.claim_loop(MagicMock(), Path("/tmp"), None))
            # Let claim_loop run until 2 active runs are dispatched
            for _ in range(30):
                if active == 2:
                    break
                await asyncio.sleep(0.005)

            assert active == 2
            assert max_observed == 2
            # 2 runs ("run-2", "run-3") must still be waiting in queued_rows!
            assert [r["id"] for r in queued_rows] == ["run-2", "run-3"]

            # Finish run-0 -> run-2 should now be claimed
            release_events["run-0"].set()
            for _ in range(30):
                if [r["id"] for r in queued_rows] == ["run-3"]:
                    break
                await asyncio.sleep(0.005)
            assert [r["id"] for r in queued_rows] == ["run-3"]
            assert max_observed == 2

            # Finish remaining runs
            release_events["run-1"].set()
            release_events["run-2"].set()
            release_events["run-3"].set()
            for _ in range(40):
                if len(completed) == 4:
                    break
                await asyncio.sleep(0.005)

            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)
            assert completed == ["run-0", "run-1", "run-2", "run-3"]
            assert max_observed == 2

    _run(_scenario())


def test_t5c_hold_gate_releases_and_reacquires_worker_compute_slot(monkeypatch):
    """T5c (R5): When runs enter hold_gate (awaiting_approval), they release their compute slot so queued runs can execute."""
    from backend.services.runs import queue as qmod

    qmod.reset_worker_slots(1)

    async def _scenario():
        # Acquire the single worker slot as if a run was claimed
        await qmod.acquire_worker_slot()
        assert qmod.available_worker_slots() == 0

        # Simulate entering hold_gate via worker_slot_released_during_gate()
        async with qmod.worker_slot_released_during_gate():
            # While parked at awaiting_approval, the slot is free for another queued run!
            assert qmod.available_worker_slots() == 1

        # Once gate wakes and resumes, slot is re-held
        assert qmod.available_worker_slots() == 0
        qmod.release_worker_slot()
        assert qmod.available_worker_slots() == 1

    _run(_scenario())


# ── T6: BackendSessionStore max_warm_sessions LRU cap ─────────────────────────


def test_t6_backend_session_store_enforces_max_warm_sessions_lru():
    """T6 (R6): BackendSessionStore evicts and closes the oldest idle session outside self._lock when max_warm_sessions is exceeded."""
    from backend.session import BackendSessionStore, _BackendSession

    store = BackendSessionStore(max_warm_sessions=2)
    closed_keys: list[tuple[str, str]] = []

    def _make_fake_session(ws_id: str, profile: str, last_used: float) -> _BackendSession:
        s = MagicMock(spec=_BackendSession)
        s.last_used = last_used
        s.is_expired = MagicMock(return_value=False)
        s.touch = MagicMock()

        async def _close():
            # Verify self._lock is NOT held while awaiting close()!
            assert not store._lock.locked(), "close() must be awaited OUTSIDE store._lock"
            closed_keys.append((ws_id, profile))

        s.close = AsyncMock(side_effect=_close)
        return s

    async def _scenario():
        s1 = _make_fake_session("ws-1", "acme", 100.0)
        s2 = _make_fake_session("ws-2", "acme", 200.0)
        s3 = _make_fake_session("ws-3", "acme", 300.0)

        await store._return_session(("ws-1", "acme"), s1, epoch=0)
        await store._return_session(("ws-2", "acme"), s2, epoch=0)
        assert store.active_count == 2
        assert closed_keys == []

        # Returning a 3rd warm session must evict the LRU session (ws-1, last_used=100.0)
        await store._return_session(("ws-3", "acme"), s3, epoch=0)
        assert store.active_count == 2
        assert closed_keys == [("ws-1", "acme")]
        assert ("ws-1", "acme") not in store._sessions
        assert ("ws-2", "acme") in store._sessions
        assert ("ws-3", "acme") in store._sessions

    _run(_scenario())


# ── T13: Compose separation & reconcile_gates guard ───────────────────────────


def test_t13_compose_separates_api_and_worker_and_gates_reconcile():
    """T13 (R12): api defaults QUEUE_WORKER_ENABLED=false in compose, worker has stop_grace_period: 180s, and main.py gates reconcile_gates."""
    compose = REPO / "deploy" / "docker-compose.yml"
    env_example = REPO / "deploy" / ".env.example"
    if not compose.exists():
        pytest.skip("deploy/ not present in public carve")

    text = compose.read_text(encoding="utf-8")
    api_block = text.split("\n  api:\n", 1)[1].split("\n  worker:\n", 1)[0]
    worker_block = text.split("\n  worker:\n", 1)[1].split("\n  redis:\n", 1)[0]

    import re

    assert re.search(r"QUEUE_WORKER_ENABLED:\s+\$\{QUEUE_WORKER_ENABLED:-false\}", api_block)
    assert re.search(r'QUEUE_WORKER_ENABLED:\s+"true"', worker_block)
    assert "stop_grace_period: 180s" in worker_block
    assert re.search(r"GTM_TENANT_LAUNCHER:\s+\$\{GTM_TENANT_LAUNCHER:-\}", api_block)
    assert re.search(r"GTM_TENANT_LAUNCHER:\s+\$\{GTM_TENANT_LAUNCHER:-\}", worker_block)

    for var in (
        "GTM_WORKER_MAX_CONCURRENT_RUNS",
        "GTM_MAX_WARM_SESSIONS",
        "GTM_WORKER_DRAIN_TIMEOUT_S",
        "RESERVATION_TTL_SECONDS",
        "GTM_RUN_ISOLATION",
        "GTM_DOCKER_HOST",
    ):
        assert var in worker_block, f"worker in deploy/docker-compose.yml missing {var}"
        assert var in env_example.read_text(encoding="utf-8"), f"deploy/.env.example missing {var}"

    main_src = (REPO / "backend" / "main.py").read_text(encoding="utf-8")
    worker_src = (REPO / "backend" / "worker.py").read_text(encoding="utf-8")
    assert "0x67746D5F72636E63" in main_src
    assert "0x67746D5F72636E63" in worker_src


# ── T14: drain_background_tasks grace wait before cancel ──────────────────────


def test_t14_drain_background_tasks_waits_for_grace_before_cancelling(monkeypatch):
    """T14 (R12): drain_background_tasks waits up to timeout for natural completion before cancelling stragglers."""
    from backend.services.runs import state as smod

    monkeypatch.setenv("GTM_WORKER_DRAIN_TIMEOUT_S", "0.15")
    finished_naturally = False
    was_cancelled = False

    async def _fast_stage():
        nonlocal finished_naturally
        await asyncio.sleep(0.03)
        finished_naturally = True

    async def _slow_stage():
        nonlocal was_cancelled
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            was_cancelled = True
            raise

    async def _scenario():
        smod._background_tasks.clear()
        fast_t = asyncio.create_task(_fast_stage())
        slow_t = asyncio.create_task(_slow_stage())
        smod._background_tasks.add(fast_t)
        smod._background_tasks.add(slow_t)
        fast_t.add_done_callback(smod._background_tasks.discard)
        slow_t.add_done_callback(smod._background_tasks.discard)

        await smod.drain_background_tasks()
        assert finished_naturally is True, (
            "Fast in-flight stage should finish without CancelledError"
        )
        assert was_cancelled is True, "Slow stage exceeding drain timeout must be cancelled"

    _run(_scenario())
