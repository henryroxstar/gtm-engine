"""F8-1 against a real Postgres: the cancel request is a COLUMN, the worker reads it on its tick, scoped by
workspace, with no broker at all (test plan F8-1, F8-2). Skipped unless GTM_TEST_PG_ADMIN_DSN is set.

What a mocked pool cannot see: RLS (workspace B's request must not reach workspace A's worker), the column
itself, and that `cancel()` really writes it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.dbtest

asyncpg = pytest.importorskip("asyncpg")

from backend.services.runs import queue as runq  # noqa: E402
from backend.services.runs import state as runstate  # noqa: E402
from backend.services.runs.decisions import cancel  # noqa: E402
from gtm_core.db import workspace_scope  # noqa: E402


async def _workspace(conn, email: str) -> str:
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name) VALUES($1, $2) RETURNING id",
        email,
        email.split("@")[0],
    )
    return await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


async def _run(conn, ws: str, status: str = "running") -> str:
    return await conn.fetchval(
        "INSERT INTO runs (workspace_id, profile_name, status, prompt, claimed_by, heartbeat_at) "
        "VALUES ($1::uuid, 'example', $2, 'fictional prompt', 'w1', now()) RETURNING id::text",
        ws,
        status,
    )


class Store:
    def __init__(self):
        self.interrupted: list[str] = []

    async def interrupt_run(self, run_id):
        self.interrupted.append(run_id)
        return True


def test_a_cancel_sets_the_column_and_the_tick_interrupts_only_that_workspaces_run(
    clean_db, monkeypatch
):
    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=4)
        try:
            async with admin.acquire() as c:
                ws_a = await _workspace(c, f"a-{uuid.uuid4().hex[:8]}@example.test")
                ws_b = await _workspace(c, f"b-{uuid.uuid4().hex[:8]}@example.test")
                run_a = await _run(c, ws_a)
                run_a2 = await _run(c, ws_a)  # same workspace, NOT cancelled
                run_b = await _run(c, ws_b)
            monkeypatch.setattr(runstate, "_workspace_runs", {ws_a: {run_a, run_a2}, ws_b: {run_b}})
            monkeypatch.setattr(runq, "_workspace_runs", runstate._workspace_runs)
            store = Store()

            # nothing requested yet: the tick does nothing (the one permissive default: no request is not a request)
            assert await runq.cancel_tick(api, store) == []

            await cancel(api, ws_a, run_a)  # the REAL cancel — it must write the column
            async with workspace_scope(api, ws_a) as conn:
                row = await conn.fetchrow(
                    "SELECT status, cancel_requested_at FROM runs WHERE id = $1::uuid", run_a
                )
                assert row["status"] == "canceled" and row["cancel_requested_at"] is not None
                other = await conn.fetchval(
                    "SELECT cancel_requested_at FROM runs WHERE id = $1::uuid", run_a2
                )
                assert other is None  # a different run in the same workspace is untouched (F8-2)

            got = await runq.cancel_tick(api, store)
            assert got == [run_a] and store.interrupted == [run_a]  # exactly the cancelled run
            assert run_b not in store.interrupted and run_a2 not in store.interrupted
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_one_workspaces_request_never_reaches_another_workspaces_worker(clean_db, monkeypatch):
    """The worker holds ws_b's run. A cancel for ws_a's run (same worker process, different tenant) must not
    flag ws_b's — and a request row for ws_b is invisible when the tick is scoped to ws_a."""

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=4)
        try:
            async with admin.acquire() as c:
                ws_a = await _workspace(c, f"a-{uuid.uuid4().hex[:8]}@example.test")
                ws_b = await _workspace(c, f"b-{uuid.uuid4().hex[:8]}@example.test")
                run_a = await _run(c, ws_a)
                run_b = await _run(c, ws_b)
                await c.execute(
                    "UPDATE runs SET cancel_requested_at = now() WHERE id = $1::uuid", run_b
                )
            # the worker (wrongly or maliciously) believes ws_a owns BOTH ids: RLS must still hide ws_b's
            monkeypatch.setattr(runstate, "_workspace_runs", {ws_a: {run_a, run_b}})
            monkeypatch.setattr(runq, "_workspace_runs", runstate._workspace_runs)
            store = Store()
            assert await runq.cancel_tick(api, store) == []
            assert store.interrupted == []
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_the_tick_needs_no_broker(clean_db, monkeypatch):
    """Redis is latency, never correctness: with no broker configured the DB read alone does it."""
    import backend.broker as broker

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            async with admin.acquire() as c:
                ws = await _workspace(c, f"a-{uuid.uuid4().hex[:8]}@example.test")
                run = await _run(c, ws)
                await c.execute(
                    "UPDATE runs SET cancel_requested_at = now() WHERE id = $1::uuid", run
                )
            monkeypatch.setattr(runstate, "_workspace_runs", {ws: {run}})
            monkeypatch.setattr(runq, "_workspace_runs", runstate._workspace_runs)

            async def refuse(*a, **k):
                raise AssertionError("the cancel tick must not touch the broker")

            monkeypatch.setattr(broker, "publish_gate_wake", refuse, raising=False)
            store = Store()
            assert await runq.cancel_tick(api, store) == [run]
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())
