"""Phase 2 hardening — dependency-light unit guards (no live DB / server).

Covers the highest-value P2 regressions:
  - budget guard fail-closed vs fail-open (H5)          → TestBudgetPolicy
  - warm-session isolation under concurrency (B6)       → TestSessionIsolation
  - gate content-hash binding helper (H9)               → test_content_sha
These run in the fast `tests` CI job (mocked); the live-DB tier covers isolation.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import gtm_core.metering as metering


def _fixed(cap: float, spent: float):
    async def _f(self, workspace_id, *, conn=None):
        return (cap, spent)

    return _f


class TestBudgetPolicy:
    def test_fail_open_default_allows_on_error(self, monkeypatch):
        async def _boom(self, workspace_id, *, conn=None):
            raise RuntimeError("db down")

        monkeypatch.setattr(metering.PgSink, "cap_and_spent", _boom)

        async def body():
            # default (VPS/MCP) fails OPEN; fail_closed (backend paid) DENIES
            assert await metering.acheck_budget(None, "ws", table="cost_records") is True
            assert (
                await metering.acheck_budget(None, "ws", table="cost_records", fail_closed=True)
                is False
            )

        asyncio.run(body())

    def test_missing_subscription_respects_fail_closed(self, monkeypatch):
        async def _none(self, workspace_id, *, conn=None):
            return None

        monkeypatch.setattr(metering.PgSink, "cap_and_spent", _none)

        async def body():
            assert await metering.acheck_budget(None, "ws", table="cost_records") is True
            assert (
                await metering.acheck_budget(None, "ws", table="cost_records", fail_closed=True)
                is False
            )

        asyncio.run(body())

    def test_cap_boundary(self, monkeypatch):
        async def body():
            monkeypatch.setattr(metering.PgSink, "cap_and_spent", _fixed(10.0, 5.0))
            assert await metering.acheck_budget(None, "ws", table="cost_records") is True
            monkeypatch.setattr(metering.PgSink, "cap_and_spent", _fixed(10.0, 10.0))
            assert await metering.acheck_budget(None, "ws", table="cost_records") is False
            # hard ceiling (2x) denies even fail_closed=False
            monkeypatch.setattr(metering.PgSink, "cap_and_spent", _fixed(10.0, 25.0))
            assert await metering.acheck_budget(None, "ws", table="cost_records") is False

        asyncio.run(body())


class _FakeSession:
    """Stand-in for _BackendSession that records which run_ids it served."""

    instances: list = []

    def __init__(self, repo_root, profile_name, *, pool=None, workspace_id=None):
        self.connected = False
        self.last_used = 0.0
        self.run_ids: list = []
        _FakeSession.instances.append(self)

    def touch(self):
        self.last_used = 1.0

    async def connect(self):
        self.connected = True

    async def run(self, prompt, run_id=None):
        self.run_ids.append(run_id)
        await asyncio.sleep(0.01)  # force interleaving so concurrency is exercised
        yield "chunk"

    async def close(self):
        self.connected = False


class TestSessionIsolation:
    def _store(self, monkeypatch):
        import backend.session as session_mod

        monkeypatch.setattr(session_mod, "_BackendSession", _FakeSession)
        _FakeSession.instances = []
        return session_mod.BackendSessionStore(Path("."))

    async def _drain(self, agen):
        return [c async for c in agen]

    def test_serial_runs_reuse_one_warm_session(self, monkeypatch):
        store = self._store(monkeypatch)

        async def body():
            await self._drain(store.run(None, "ws", "prof", "p", "r1"))
            await self._drain(store.run(None, "ws", "prof", "p", "r2"))

        asyncio.run(body())
        # one warm session, popped-and-returned across both serial runs
        assert len(_FakeSession.instances) == 1
        assert _FakeSession.instances[0].run_ids == ["r1", "r2"]

    def test_concurrent_same_key_runs_get_isolated_sessions(self, monkeypatch):
        store = self._store(monkeypatch)

        async def body():
            await asyncio.gather(
                self._drain(store.run(None, "ws", "prof", "p", "rA")),
                self._drain(store.run(None, "ws", "prof", "p", "rB")),
            )

        asyncio.run(body())
        # two concurrent runs for one (workspace, profile) MUST NOT share a client
        assert len(_FakeSession.instances) == 2
        served = sorted(rid for s in _FakeSession.instances for rid in s.run_ids)
        assert served == ["rA", "rB"]


def test_content_sha():
    from backend.routers.runs import _content_sha

    assert _content_sha(None) is None
    assert (
        _content_sha("⟦GATE:publish⟧ hello")
        == hashlib.sha256("⟦GATE:publish⟧ hello".encode()).hexdigest()
    )
