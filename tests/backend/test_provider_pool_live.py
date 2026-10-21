"""Live-Postgres proof of MM4 (decision D2-A): per-workspace provider shares can never exceed the pool.

A tenant spending the platform's pooled provider key gets a static monthly SHARE of a pool the operator
sizes. The shares are allocated by ``allocate_provider_share`` (V045) under a row lock on the pool, so
the sum of shares is bounded by construction — not by workers agreeing with each other. Mocks cannot
see a lock, RLS or a SECURITY DEFINER boundary, so every check runs against a REAL Postgres as
``gtm_api`` (non-owner, FORCE RLS), with real concurrent connections for the race.

What is pinned:
  - the pool is a ceiling on the SUM of shares: the allocation that would breach it is refused, and
    leaves nothing behind; a smaller one that fits is accepted;
  - two allocations racing for the last of the pool cannot both win (the lock);
  - the call is idempotent per workspace and refuses a missing pool, a negative or NULL size;
  - the SECURITY DEFINER function cannot be pointed at another workspace, and a tenant can neither read
    the pool nor write a share directly — the function is the only way in.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

from gtm_core.db import workspace_scope  # noqa: E402
from tests.backend._live_helpers import seed_workspace  # noqa: E402

RR = "rocketreach"


async def _set_pool(admin, provider: str, units: float | None) -> None:
    await admin.execute("DELETE FROM provider_pool_shares")
    if units is None:
        await admin.execute("DELETE FROM provider_pools WHERE provider = $1", provider)
        return
    await admin.execute(
        "INSERT INTO provider_pools(provider, monthly_units) VALUES($1, $2) "
        "ON CONFLICT (provider) DO UPDATE SET monthly_units = EXCLUDED.monthly_units",
        provider,
        units,
    )


async def _allocate(api, ws: str, provider: str, units, *, as_ws: str | None = None) -> float:
    async with workspace_scope(api, as_ws or ws) as c:
        return float(
            await c.fetchval(
                "SELECT allocate_provider_share($1::uuid, $2, $3)", ws, provider, units
            )
        )


async def _shares(admin, provider: str) -> dict[str, float]:
    rows = await admin.fetch(
        "SELECT workspace_id::text AS w, units FROM provider_pool_shares WHERE provider = $1",
        provider,
    )
    return {r["w"]: float(r["units"]) for r in rows}


def _with_pools(clean_db, body):
    async def run():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=6)
        try:
            await body(admin, api)
        finally:
            await api.close()
            await admin.close()

    asyncio.run(run())


async def _three(admin) -> tuple[str, str, str]:
    async with admin.acquire() as c:
        return (
            await seed_workspace(c, "a@example.com", credits=0.0),
            await seed_workspace(c, "b@example.com", credits=0.0),
            await seed_workspace(c, "c@example.com", credits=0.0),
        )


# ── 5.1 the pool is a ceiling on the sum ──────────────────────────────────────


def test_a_share_that_would_breach_the_pool_is_refused_and_a_smaller_one_fits(clean_db):
    async def body(admin, api):
        a, b, c = await _three(admin)
        await _set_pool(admin, RR, 100)

        assert await _allocate(api, a, RR, 60) == 60
        assert await _allocate(api, b, RR, 60) == 0  # 60 + 60 > 100
        assert await _shares(admin, RR) == {a: 60}  # the refusal left nothing behind
        assert await _allocate(api, b, RR, 40) == 40  # 60 + 40 = 100: exactly the pool
        assert await _allocate(api, c, RR, 1) == 0  # the pool is spent
        assert sum((await _shares(admin, RR)).values()) == 100

    _with_pools(clean_db, body)


def test_two_allocations_racing_for_the_last_of_the_pool_cannot_both_win(clean_db):
    """The lock is what makes the ceiling real: a second allocation arriving while the first is
    uncommitted must WAIT, then see the first's share, and be refused."""

    async def body(admin, api):
        a, b, _c = await _three(admin)
        await _set_pool(admin, RR, 100)

        async with workspace_scope(api, a) as conn_a:
            got_a = float(
                await conn_a.fetchval("SELECT allocate_provider_share($1::uuid, $2, $3)", a, RR, 60)
            )
            assert got_a == 60  # A holds its share UNCOMMITTED, and so the pool row lock

            racer = asyncio.create_task(_allocate(api, b, RR, 60))
            await asyncio.sleep(0.5)
            assert not racer.done(), "B was not made to wait for A: nothing serialises the pool"
            # leaving the block commits A

        assert await asyncio.wait_for(racer, timeout=10) == 0
        assert sum((await _shares(admin, RR)).values()) <= 100

    _with_pools(clean_db, body)


def test_many_concurrent_allocations_never_exceed_the_pool(clean_db):
    async def body(admin, api):
        async with admin.acquire() as c:
            wss = [await seed_workspace(c, f"w{i}@example.com", credits=0.0) for i in range(6)]
        await _set_pool(admin, RR, 100)

        results = await asyncio.gather(*(_allocate(api, w, RR, 30) for w in wss))

        assert sorted(results) == [0, 0, 0, 30, 30, 30]  # 3 × 30 = 90 fit; a 4th would be 120
        assert sum((await _shares(admin, RR)).values()) == 90

    _with_pools(clean_db, body)


# ── the call itself ───────────────────────────────────────────────────────────


def test_allocation_is_idempotent_per_workspace(clean_db):
    async def body(admin, api):
        a, _b, _c = await _three(admin)
        await _set_pool(admin, RR, 100)
        assert await _allocate(api, a, RR, 60) == 60
        assert await _allocate(api, a, RR, 10) == 60  # the share it holds, not a new smaller one
        assert await _allocate(api, a, RR, 95) == 60  # nor an enlarged one
        assert await _shares(admin, RR) == {a: 60}

    _with_pools(clean_db, body)


def test_no_pool_means_no_share(clean_db):
    """Absence refuses: a provider the operator never sized has a pool of nothing."""

    async def body(admin, api):
        a, _b, _c = await _three(admin)
        await _set_pool(admin, RR, None)
        assert await _allocate(api, a, RR, 10) == 0
        assert await _shares(admin, RR) == {}

    _with_pools(clean_db, body)


def test_a_zero_share_is_not_stored(clean_db):
    async def body(admin, api):
        a, _b, _c = await _three(admin)
        await _set_pool(admin, RR, 100)
        assert await _allocate(api, a, RR, 0) == 0
        assert await _shares(admin, RR) == {}

    _with_pools(clean_db, body)


def test_a_pool_lowered_below_what_is_allocated_keeps_the_holders_and_refuses_new_ones(clean_db):
    async def body(admin, api):
        a, b, _c = await _three(admin)
        await _set_pool(admin, RR, 100)
        assert await _allocate(api, a, RR, 80) == 80
        await admin.execute("UPDATE provider_pools SET monthly_units = 50 WHERE provider = $1", RR)
        assert await _allocate(api, a, RR, 80) == 80  # an existing holder is not clawed back
        assert await _allocate(api, b, RR, 1) == 0  # nobody new fits

    _with_pools(clean_db, body)


@pytest.mark.parametrize("units", [-1, None])
def test_a_negative_or_missing_share_size_is_an_error_not_a_grant(clean_db, units):
    async def body(admin, api):
        a, _b, _c = await _three(admin)
        await _set_pool(admin, RR, 100)
        with pytest.raises(asyncpg.PostgresError):
            await _allocate(api, a, RR, units)
        assert await _shares(admin, RR) == {}

    _with_pools(clean_db, body)


# ── the boundary: the function is the only way in ─────────────────────────────


def test_the_definer_function_cannot_be_pointed_at_another_workspace(clean_db):
    async def body(admin, api):
        a, b, _c = await _three(admin)
        await _set_pool(admin, RR, 100)
        with pytest.raises(asyncpg.PostgresError):
            await _allocate(api, b, RR, 10, as_ws=a)  # scoped as A, asking for B
        assert await _shares(admin, RR) == {}
        # positive control: the same call for one's OWN workspace works
        assert await _allocate(api, a, RR, 10) == 10

    _with_pools(clean_db, body)


def test_a_tenant_sees_only_its_own_share_and_cannot_write_or_read_the_pool(clean_db):
    async def body(admin, api):
        a, b, _c = await _three(admin)
        await _set_pool(admin, RR, 100)
        await _allocate(api, a, RR, 60)
        await _allocate(api, b, RR, 40)

        async with workspace_scope(api, a) as conn:
            rows = await conn.fetch(
                "SELECT workspace_id::text AS w, units FROM provider_pool_shares"
            )
            assert [r["w"] for r in rows] == [a]  # RLS: B's share is invisible to A

        async with workspace_scope(api, a) as conn:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "INSERT INTO provider_pool_shares(workspace_id, provider, units) "
                    "VALUES($1::uuid, $2, 1000)",
                    a,
                    RR,
                )
        async with workspace_scope(api, a) as conn:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "UPDATE provider_pool_shares SET units = 1000 WHERE workspace_id = $1::uuid", a
                )
        async with workspace_scope(api, a) as conn:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch("SELECT * FROM provider_pools")

    _with_pools(clean_db, body)


def test_the_resolver_asks_for_the_registrys_default_share_and_the_pool_bounds_it(clean_db):
    """The real resolver -> the real function -> the real pool, with the shipped registry."""
    from backend.services.provider_pool import resolve_provider_shares
    from gtm_core.provider_registry import default_share

    pro = default_share(RR, "pro")
    assert pro > 0  # the shipped registry gives a pro workspace something to spend

    async def body(admin, api):
        a, b, c = await _three(admin)
        for w in (a, b, c):
            await admin.execute(
                "UPDATE subscriptions SET entitlement = 'pro' WHERE workspace_id = $1::uuid", w
            )
        await _set_pool(admin, RR, pro * 2)  # room for exactly two pro workspaces

        assert await resolve_provider_shares(api, a, (RR,)) == {RR: pro}
        assert await resolve_provider_shares(api, b, (RR,)) == {RR: pro}
        assert await resolve_provider_shares(api, c, (RR,)) == {RR: 0}  # the pool is spent
        assert await resolve_provider_shares(api, a, (RR,)) == {RR: pro}  # stable on repeat

        # a free workspace asks for nothing, and so holds nothing
        await admin.execute(
            "UPDATE subscriptions SET entitlement = 'free' WHERE workspace_id = $1::uuid", c
        )
        assert await resolve_provider_shares(api, c, (RR,)) == {RR: 0}
        assert c not in await _shares(admin, RR)

    _with_pools(clean_db, body)


def test_a_temp_table_cannot_stand_in_for_the_pool(clean_db):
    """TEMP is granted to PUBLIC, and a relation in ``pg_temp`` is searched FIRST unless the function's
    search_path names it LAST. A session that shadows ``provider_pools`` with its own huge pool must
    still be held to the REAL one — the definer function reads ``public.provider_pools``."""

    async def body(admin, api):
        a, _b, _c = await _three(admin)
        await _set_pool(admin, RR, 10)  # the real pool: 10 units
        async with workspace_scope(api, a) as conn:
            await conn.execute(
                "CREATE TEMP TABLE provider_pools(provider text, monthly_units numeric) ON COMMIT DROP"
            )
            await conn.execute("INSERT INTO provider_pools VALUES($1, 1000000)", RR)
            granted = float(
                await conn.fetchval("SELECT allocate_provider_share($1::uuid, $2, 500)", a, RR)
            )
        assert granted == 0  # held to the real pool of 10, not the shadow pool of a million
        assert await _shares(admin, RR) == {}

    _with_pools(clean_db, body)
