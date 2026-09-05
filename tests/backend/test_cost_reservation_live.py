"""Live-Postgres tests for atomic cost reservation (follow-up (f), Phase 1).

Proves against a REAL Postgres that areserve_budget() makes cap enforcement EXACT
under concurrency — the thing acheck_budget alone cannot (a check-then-act read that
concurrent runs race). Skipped unless GTM_TEST_PG_ADMIN_DSN is set (root conftest).
Each test wraps an async body in asyncio.run (repo convention; no pytest-asyncio dep).

Cases (PRD §4):
  - Concurrency (core): N parallel reserves for one workspace, cap admits only K<N →
    exactly K succeed, N-K denied, Σ approved ≤ cap. A control shows the plain
    acheck_budget read would admit all N (the overshoot this closes).
  - Crash safety: an open reservation past the TTL → release_stale_reservations flips
    it to 'released' and headroom returns.
  - Fail-closed: a DB error on the reserve path denies (matches fail_closed=True).
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

from gtm_core.metering import acheck_budget, areserve_budget  # noqa: E402


async def _register(conn, email: str) -> str:
    """Insert a user (fires the bootstrap trigger); return the workspace id."""
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name, password_hash) "
        "VALUES($1, $2, 'h') RETURNING id::text",
        email,
        email.split("@")[0],
    )
    return await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


async def _set_cap(admin_conn, wid: str, cap: float) -> None:
    # Fresh workspaces default to cap 0 (V011 fail-safe), so tests set it explicitly.
    await admin_conn.execute(
        "UPDATE subscriptions SET monthly_cost_cap_usd = $2 WHERE workspace_id = $1::uuid",
        wid,
        cap,
    )


def test_concurrent_reserves_never_exceed_cap(clean_db):
    """N parallel reserves for one workspace: exactly K<N admitted, Σ approved ≤ cap."""

    async def body():
        from backend.database import create_pool, workspace_scope

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=8)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                await _set_cap(c, wa, 10.0)  # cap=$10, estimate=$3 → K=3 (0,3,6→9; 12>10 denies)

            n, estimate = 6, 3.0

            # Control: N concurrent plain acheck_budget reads all see spent=0 < cap → all
            # pass. That is the overshoot (would authorize 6 * spend) the reservation closes.
            async def _check():
                async with workspace_scope(api, wa) as conn:
                    return await acheck_budget(
                        api, wa, table="cost_records", conn=conn, fail_closed=True
                    )

            checks = await asyncio.gather(*[_check() for _ in range(n)])
            assert sum(1 for ok in checks if ok) == n, "TOCTOU control: all N reads admit"

            # Reservation path: fire N concurrent reserves; the FOR UPDATE lock serializes
            # them so the open-sum can never let the total exceed the cap.
            async def _reserve():
                async with workspace_scope(api, wa) as conn:
                    return await areserve_budget(
                        conn, wa, run_id=None, estimate=estimate, fail_closed=True
                    )

            results = await asyncio.gather(*[_reserve() for _ in range(n)])
            admitted = [r for r in results if r is not None]
            denied = [r for r in results if r is None]

            assert len(admitted) == 3, f"expected 3 admitted, got {len(admitted)}"
            assert len(denied) == 3
            # Σ approved estimates ≤ cap, and the DB agrees on the open reservation count.
            assert len(admitted) * estimate <= 10.0
            async with admin.acquire() as c:
                open_rows = await c.fetchval(
                    "SELECT count(*) FROM cost_reservations "
                    "WHERE workspace_id=$1::uuid AND state='open'",
                    wa,
                )
                open_sum = await c.fetchval(
                    "SELECT COALESCE(SUM(estimated_usd),0) FROM cost_reservations "
                    "WHERE workspace_id=$1::uuid AND state='open'",
                    wa,
                )
            assert open_rows == 3
            assert float(open_sum) == 9.0
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_crash_sweep_releases_stale_open_reservation(clean_db):
    """An 'open' reservation older than the TTL flips to 'released'; headroom returns."""

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                await _set_cap(c, wa, 10.0)
                # A stranded 'open' reservation created 2h ago (done-callback skipped).
                await c.execute(
                    "INSERT INTO cost_reservations(workspace_id, estimated_usd, created_at) "
                    "VALUES($1::uuid, 5.0, now() - interval '2 hours')",
                    wa,
                )

            # Sweep anything open > 1h (SECURITY DEFINER; gtm_api has EXECUTE, no context).
            async with api.acquire() as c:
                released = await c.fetchval("SELECT release_stale_reservations($1)", 3600)
            assert released == 1

            async with admin.acquire() as c:
                state = await c.fetchval(
                    "SELECT state FROM cost_reservations WHERE workspace_id=$1::uuid", wa
                )
                open_sum = await c.fetchval(
                    "SELECT COALESCE(SUM(estimated_usd),0) FROM cost_reservations "
                    "WHERE workspace_id=$1::uuid AND state='open'",
                    wa,
                )
            assert state == "released"
            assert float(open_sum) == 0.0  # headroom fully returned
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_reserve_fails_closed_on_db_error():
    """A DB error on the reserve path denies (returns None), matching fail_closed=True."""

    class _BoomConn:
        async def fetchval(self, *a, **k):
            raise RuntimeError("db blip")

    async def body():
        rid = await areserve_budget(_BoomConn(), "ws", run_id=None, estimate=3.0, fail_closed=True)
        assert rid is None

    asyncio.run(body())
