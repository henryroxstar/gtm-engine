"""F8 — a cancel reaches a run executing in ANOTHER process (always-on hardening test plan F8-1, F8-2).

Unit half: the session store's `interrupt_run`, the "never returned to the warm pool" rule, the tick's use of
it, and the cadence. The same tick against a real Postgres (RLS scoping, no broker at all) is
tests/backend/test_cancel_tick_live.py (dbtest tier). The SDK's own `interrupt()` is verified live in
tests/live_sdk/test_stop_live.py; sessions are faked here.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import backend.session as bsess
from backend.services.runs import queue as runq
from backend.services.runs import state as runstate


class FakeSession:
    """Stands in for `_BackendSession`: records what the store does to it."""

    instances: list[FakeSession] = []

    def __init__(self, repo_root, profile, **kw):
        self.profile = profile
        self.connected = False
        self.poisoned = False
        self.last_used = 0.0
        self.closed = False
        self.interrupted = 0
        self.release = asyncio.Event()
        self._current_run_id = None
        FakeSession.instances.append(self)

    async def connect(self):
        self.connected = True

    def touch(self):
        self.last_used += 1

    async def interrupt(self):
        self.interrupted += 1
        self.poisoned = True
        self.release.set()
        return True

    async def run(self, prompt, run_id=None, agent_id=None):
        self._current_run_id = run_id
        yield "start"
        await self.release.wait()  # a long node: ends only when interrupted
        yield "end"

    async def close(self):
        self.closed = True
        self.connected = False


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    FakeSession.instances = []
    monkeypatch.setattr(bsess, "_BackendSession", FakeSession)


def _store():
    return bsess.BackendSessionStore(max_warm_sessions=4)


async def _start(store, ws, run_id, results):
    async def go():
        async for chunk in store.run(None, ws, "example", "do work", run_id):
            results.append((run_id, chunk))

    task = asyncio.create_task(go())
    await asyncio.sleep(0.05)
    return task


def test_interrupt_run_stops_exactly_the_session_running_that_run_and_it_is_not_pooled():
    async def go():
        store = _store()
        out: list = []
        a = await _start(store, "ws-1", "run-A", out)
        b = await _start(store, "ws-1", "run-B", out)
        assert await store.interrupt_run("run-A") is True
        await asyncio.wait_for(a, 2)
        assert [s.interrupted for s in FakeSession.instances] == [1, 0]  # run-B untouched (F8-2)
        assert FakeSession.instances[0].closed and not FakeSession.instances[1].closed
        assert store.active_count == 0  # the interrupted session was NOT returned to the warm pool
        FakeSession.instances[1].release.set()
        await asyncio.wait_for(b, 2)
        assert store.active_count == 1  # an uninterrupted session still is (the rule is specific)

    asyncio.run(go())


def test_the_next_run_on_that_workspace_gets_a_fresh_session():
    async def go():
        store = _store()
        out: list = []
        a = await _start(store, "ws-1", "run-A", out)
        await store.interrupt_run("run-A")
        await asyncio.wait_for(a, 2)
        n_before = len(FakeSession.instances)
        nxt = await _start(store, "ws-1", "run-C", out)
        assert (
            len(FakeSession.instances) == n_before + 1
        )  # a brand-new session, not the half-finished one
        FakeSession.instances[-1].release.set()
        await asyncio.wait_for(nxt, 2)

    asyncio.run(go())


def test_interrupt_run_for_a_run_nobody_is_executing_is_a_harmless_false():
    async def go():
        store = _store()
        assert await store.interrupt_run("never-ran") is False

    asyncio.run(go())


def test_the_tick_interrupts_only_runs_with_a_cancel_request_and_marks_them_cancelled(monkeypatch):
    class Store:
        def __init__(self):
            self.interrupted = []

        async def interrupt_run(self, run_id):
            self.interrupted.append(run_id)
            return True

    async def fake_requested(pool, held):
        return {
            "ws-1": {"run-A"}
        }  # only run-A has cancel_requested_at set; run-B and ws-2's do not

    monkeypatch.setattr(runq, "_cancel_requested", fake_requested)
    monkeypatch.setattr(
        runstate, "_workspace_runs", {"ws-1": {"run-A", "run-B"}, "ws-2": {"run-X"}}
    )
    monkeypatch.setattr(runq, "_workspace_runs", runstate._workspace_runs)
    store = Store()
    runstate._cancelled_runs.discard("run-A")
    got = asyncio.run(runq.cancel_tick(object(), store))
    assert got == ["run-A"] and store.interrupted == ["run-A"]
    assert "run-A" in runstate._cancelled_runs  # the executor's own guard now sees it too
    runstate._cancelled_runs.discard("run-A")


def test_a_failing_cancel_read_never_breaks_the_heartbeat(monkeypatch):
    async def boom(pool, held):
        raise OSError("db down")

    monkeypatch.setattr(runq, "_cancel_requested", boom)
    monkeypatch.setattr(runq, "_workspace_runs", {"ws-1": {"run-A"}})
    assert asyncio.run(runq.cancel_tick(object(), SimpleNamespace())) == []  # swallowed, logged


def test_cancel_latency_is_bounded_by_one_heartbeat_tick():
    assert runq.LEASE_S / 3 <= 30, "the tick the cancel rides on is at most ~30 s"


def test_the_heartbeat_loop_runs_the_cancel_tick_after_each_beat(monkeypatch):
    calls: list[str] = []

    async def beat(pool, *, worker_id=""):
        calls.append("beat")

    async def tick(pool, store=None):
        calls.append("cancel_tick")
        return []

    sleeps = {"n": 0}

    async def fake_sleep(_s):
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(runq, "heartbeat_once", beat)
    monkeypatch.setattr(runq, "cancel_tick", tick)
    monkeypatch.setattr(runq.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(runq, "_workspace_runs", {"ws-1": {"run-A"}})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runq.heartbeat_loop(object()))
    assert calls == ["beat", "cancel_tick"]
