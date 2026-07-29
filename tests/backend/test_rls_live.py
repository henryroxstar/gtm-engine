"""Live-Postgres tenant-isolation suite — Phase 1 (T1 + T2 + T3).

Proves, against a REAL Postgres, that the FORCE-RLS + non-owner-role change
actually isolates tenants — the thing the mocked suites and the JWT-only
test_tenant_isolation.py cannot prove. Skipped unless GTM_TEST_PG_ADMIN_DSN is
set (see conftest.py). Each test wraps an async body in asyncio.run so the tier
needs no pytest-asyncio dependency.

Lens coverage (per the hardening PRD verification plan):
  - components touched: backend.database (two-role pool), V009 schema, auth bootstrap
  - what could break: signup/login under FORCE RLS (test_auth_bootstrap_*)
  - user features: register, run create/read (via the SQL those endpoints issue)
  - silent errors: cross-tenant read returning data, no-context read/write leaking
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

_TENANT_TABLES = (
    "workspaces",
    "workspace_members",
    "subscriptions",
    "profiles",
    "api_keys",
    "encrypted_credentials",
    "cost_records",
    "runs",
    "mcp_calls",
    "push_tokens",
    "entitlement_sync_events",
)


async def _register(conn, email: str) -> tuple[str, str]:
    """Insert a user (fires the SECURITY DEFINER bootstrap trigger); return (uid, wid)."""
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name, password_hash) "
        "VALUES($1, $2, 'h') RETURNING id::text",
        email,
        email.split("@")[0],
    )
    wid = await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)
    return uid, wid


async def _seed_run(api, workspace_scope, wid: str) -> str:
    async with workspace_scope(api, wid) as c:
        return await c.fetchval(
            "INSERT INTO runs(workspace_id, profile_name, prompt) "
            "VALUES($1::uuid,'p','x') RETURNING id::text",
            wid,
        )


def test_force_rls_and_role_least_privilege(clean_db):
    """V009 arms FORCE RLS on all tenant tables and gtm_api can't bypass it."""

    async def body():
        pool = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        try:
            async with pool.acquire() as c:
                forced = {
                    r["relname"]: r["relforcerowsecurity"]
                    for r in await c.fetch(
                        "SELECT relname, relforcerowsecurity FROM pg_class WHERE relname = ANY($1)",
                        list(_TENANT_TABLES),
                    )
                }
                missing = [t for t in _TENANT_TABLES if not forced.get(t)]
                assert not missing, f"FORCE ROW LEVEL SECURITY missing on: {missing}"
                role = await c.fetchrow(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname='gtm_api'"
                )
                assert (role["rolsuper"], role["rolbypassrls"]) == (False, False)
        finally:
            await pool.close()

    asyncio.run(body())


def test_auth_bootstrap_creates_isolated_workspaces(clean_db):
    """T2: signup under the non-owner role still provisions workspace+member+subscription."""

    async def body():
        from backend.database import create_pool

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            async with api.acquire() as c:
                _, wa = await _register(c, "a@example.com")
                _, wb = await _register(c, "b@example.com")
            assert wa and wb and wa != wb

            admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
            try:
                async with admin.acquire() as c:
                    assert await c.fetchval("SELECT count(*) FROM subscriptions") == 2
                    assert await c.fetchval("SELECT count(*) FROM workspace_members") == 2
            finally:
                await admin.close()
        finally:
            await api.close()

    asyncio.run(body())


def test_read_and_by_id_isolation(clean_db):
    """T1 + T3: each workspace sees only its own runs; A cannot fetch B's even by id."""

    async def body():
        from backend.database import create_pool, workspace_scope

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with api.acquire() as c:
                _, wa = await _register(c, "a@example.com")
                _, wb = await _register(c, "b@example.com")
            ra = await _seed_run(api, workspace_scope, wa)
            rb = await _seed_run(api, workspace_scope, wb)

            async with workspace_scope(api, wa) as c:
                assert [r["id"] for r in await c.fetch("SELECT id::text FROM runs")] == [ra]
                # by-id BOLA: B's run is invisible even when addressed directly
                assert await c.fetchrow("SELECT id FROM runs WHERE id=$1::uuid", rb) is None
            async with workspace_scope(api, wb) as c:
                assert [r["id"] for r in await c.fetch("SELECT id::text FROM runs")] == [rb]
        finally:
            await api.close()

    asyncio.run(body())


def test_fail_closed_without_workspace_context(clean_db):
    """No workspace context → zero rows on read and WITH CHECK rejects on write."""

    async def body():
        from backend.database import create_pool, workspace_scope

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            async with api.acquire() as c:
                _, wa = await _register(c, "a@example.com")
            await _seed_run(api, workspace_scope, wa)

            # read: nullif-hardened helper → 0 rows, consistently (no uuid-parse error)
            async with api.acquire() as c, c.transaction():
                assert await c.fetchval("SELECT count(*) FROM runs") == 0
            # write: WITH CHECK rejects an insert with no workspace context
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with api.acquire() as c, c.transaction():
                    await c.execute(
                        "INSERT INTO runs(workspace_id, profile_name, prompt) "
                        "VALUES($1::uuid,'p','x')",
                        wa,
                    )
        finally:
            await api.close()

    asyncio.run(body())


def test_with_check_blocks_cross_workspace_insert(clean_db):
    """Scope A cannot forge a row tagged for workspace B (WITH CHECK)."""

    async def body():
        from backend.database import create_pool, workspace_scope

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with api.acquire() as c:
                _, wa = await _register(c, "a@example.com")
                _, wb = await _register(c, "b@example.com")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with workspace_scope(api, wa) as c:
                    await c.execute(
                        "INSERT INTO runs(workspace_id, profile_name, prompt) "
                        "VALUES($1::uuid,'p','x')",
                        wb,
                    )
        finally:
            await api.close()

    asyncio.run(body())
