"""Live-Postgres tenant-isolation suite for the MCP runtime (follow-up (c)).

Proves, against a REAL Postgres, that moving the MCP surface to the non-owner
``gtm_api`` role + FORCE RLS actually isolates tenants — the thing the mocked
``test_mcp_auth.py`` cannot. Skipped unless ``GTM_TEST_PG_ADMIN_DSN`` is set
(see the root tests/conftest.py). Each test wraps an async body in ``asyncio.run``
so the tier needs no pytest-asyncio dependency.

IMPORTANT: this module imports only ``mcp_server.auth`` + ``mcp_server.meter`` +
``gtm_core.db`` — never ``mcp_server.server`` (which pulls in ``mcp``/``httpx``) —
so it runs in the minimal pytest+asyncpg CI job.

Lens coverage:
  - the pre-tenant bootstrap: resolve_api_key resolves only the presented key (V012)
  - post-auth queries run RLS-subject: a metered call is visible only under its scope
  - WITH CHECK blocks a forged cross-workspace mcp_calls insert
  - the boot guard fires when connected as the owner role
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

from gtm_core.db import assert_runtime_role_least_privilege, workspace_scope  # noqa: E402
from mcp_server.auth import validate_api_key  # noqa: E402
from mcp_server.meter import meter_call  # noqa: E402


async def _register(conn, email: str) -> str:
    """Insert a user (fires the SECURITY DEFINER bootstrap trigger); return workspace id."""
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name, password_hash) "
        "VALUES($1, $2, 'h') RETURNING id::text",
        email,
        email.split("@")[0],
    )
    return await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


async def _seed_key(
    admin_conn,
    wid: str,
    raw: str,
    *,
    entitlement: str = "pro",
    revoked: bool = False,
    expired: bool = False,
) -> str:
    """Seed an api_keys row (owner pool). Returns the key id."""
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    return await admin_conn.fetchval(
        "INSERT INTO api_keys(workspace_id, key_hash, prefix, entitlement, revoked_at, expires_at) "
        "VALUES($1::uuid, $2, $3, $4, "
        "       CASE WHEN $5 THEN now() ELSE NULL END, "
        "       CASE WHEN $6 THEN now() - interval '1 day' ELSE NULL END) "
        "RETURNING id::text",
        wid,
        key_hash,
        raw[:8],
        entitlement,
        revoked,
        expired,
    )


def test_resolve_api_key_isolates_and_bumps_last_used(clean_db):
    """resolve_api_key (V012) resolves only the presented key; empty for bad keys."""

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                wb = await _register(c, "b@example.com")
                await _seed_key(c, wa, "sk-key-a", entitlement="pro")
                await _seed_key(c, wb, "sk-key-b", entitlement="pro_plus")
                await _seed_key(c, wa, "sk-key-revoked", revoked=True)
                await _seed_key(c, wa, "sk-key-expired", expired=True)

            # Key A resolves to A only (runs with NO workspace context — the definer
            # function is the pre-tenant bypass; gtm_api can't read api_keys otherwise).
            ctx_a = await validate_api_key("sk-key-a", api)
            assert ctx_a is not None and ctx_a.workspace_id == wa
            assert str(ctx_a.entitlement.value) == "pro"

            ctx_b = await validate_api_key("sk-key-b", api)
            assert ctx_b is not None and ctx_b.workspace_id == wb

            # Forged / revoked / expired keys resolve to nothing (enumeration-safe).
            assert await validate_api_key("sk-not-a-real-key", api) is None
            assert await validate_api_key("sk-key-revoked", api) is None
            assert await validate_api_key("sk-key-expired", api) is None

            # last_used_at advanced from NULL after the resolve (folded-in bump).
            async with admin.acquire() as c:
                last_used = await c.fetchval(
                    "SELECT last_used_at FROM api_keys WHERE key_hash = $1",
                    hashlib.sha256(b"sk-key-a").hexdigest(),
                )
            assert last_used is not None
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_metered_call_is_rls_scoped(clean_db):
    """A metered call writes an mcp_calls row visible ONLY under its own workspace scope."""

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                wb = await _register(c, "b@example.com")
                key_a = await _seed_key(c, wa, "sk-key-a")

            await meter_call(
                workspace_id=wa,
                api_key_id=key_a,
                tool_name="draft_post",
                profile_name="acme",
                model="deepseek-v4-flash",
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.01,
                pool=api,
            )

            # Visible under A's scope, invisible under B's (RLS), 1 row total (admin).
            async with workspace_scope(api, wa) as c:
                assert await c.fetchval("SELECT count(*) FROM mcp_calls") == 1
            async with workspace_scope(api, wb) as c:
                assert await c.fetchval("SELECT count(*) FROM mcp_calls") == 0
            async with admin.acquire() as c:
                assert await c.fetchval("SELECT count(*) FROM mcp_calls") == 1
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_with_check_blocks_cross_workspace_mcp_insert(clean_db):
    """Scope A cannot forge an mcp_calls row tagged for workspace B (WITH CHECK)."""

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                wb = await _register(c, "b@example.com")
                key_b = await _seed_key(c, wb, "sk-key-b")

            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with workspace_scope(api, wa) as c:
                    await c.execute(
                        "INSERT INTO mcp_calls(workspace_id, api_key_id, tool_name, "
                        "profile_name, model, prompt_tokens, completion_tokens, cost_usd) "
                        "VALUES($1::uuid, $2::uuid, 'draft_post', 'acme', 'm', 1, 1, 0.01)",
                        wb,  # forge B's workspace while scoped to A
                        key_b,
                    )
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_boot_guard_raises_on_owner_role(clean_db, monkeypatch):
    """assert_runtime_role_least_privilege raises when connected as the owner in prod."""

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        try:
            with pytest.raises(RuntimeError, match="RLS is INERT"):
                await assert_runtime_role_least_privilege(admin)
        finally:
            await admin.close()

    monkeypatch.setenv("ENV", "production")
    asyncio.run(body())
