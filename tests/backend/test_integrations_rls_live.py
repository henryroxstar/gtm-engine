"""Live-Postgres RLS coverage for the BYOK integrations routes (V003 encrypted_credentials).

The mocked ``tests/backend/test_integrations_api.py`` substitutes ``pool.acquire()`` with an
``AsyncMock`` for both the connection AND the transaction context manager, so a router that
forgot ``workspace_scope`` (no ``SET LOCAL app.current_workspace_id``) is indistinguishable
from one that used it — the mock has no RLS to enforce. This tier runs the real
``/v1/integrations`` routes against a REAL Postgres, as the non-owner ``gtm_api`` role under
FORCE ROW LEVEL SECURITY, so a regression back to a bare ``pool.acquire()`` fails loudly here
instead of shipping as a 500 (WITH CHECK reject) or a false-empty GET.

Skipped unless ``GTM_TEST_PG_ADMIN_DSN`` is set (see tests/conftest.py's ``live_db``/``clean_db``).
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture(autouse=True)
def _test_kek(monkeypatch):
    # A fixed, obviously-fake 32-byte KEK, set per test: get_kek() reads the environment on
    # every call, and an import-time write would leak into every later test in the suite.
    monkeypatch.setenv(
        "VAULT_KEK", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
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


def _make_app(pool):
    """A FastAPI app wired to a REAL pool, with require_auth overridden to a mutable
    workspace context — mirrors test_integrations_api.py's override, but the pool
    underneath is the genuine gtm_api asyncpg pool, not an AsyncMock."""
    from fastapi import FastAPI

    from backend.deps import Entitlement, WorkspaceCtx, require_auth
    from backend.routers import integrations

    app = FastAPI()
    app.include_router(integrations.router)
    app.state.pool = pool

    current = {"workspace_id": None}

    def override_require_auth():
        return WorkspaceCtx(
            user_id="test-user", workspace_id=current["workspace_id"], entitlement=Entitlement.FREE
        )

    app.dependency_overrides[require_auth] = override_require_auth
    return app, current


def test_put_get_delete_are_isolated_by_workspace_under_rls(clean_db):
    """T1-equivalent for BYOK credentials: A's PUT is invisible to B's GET, and B cannot
    DELETE a provider only A has configured — proven end-to-end through the real routes,
    not the service function directly, so a router that skips workspace_scope is caught
    even if the service layer underneath it is correct.

    Repo convention (no pytest-asyncio): the whole body — pool, ASGI client, and their
    teardown — runs inside ONE asyncio.run() call. asyncpg's pool.close() schedules a
    callback on the loop it was created on, so creating/closing it across two separate
    asyncio.run() calls (each with its own loop) raises "Event loop is closed"; httpx's
    ASGITransport (unlike TestClient's thread-per-request portal) drives the app on
    this same loop, so it stays compatible with that pool for the whole test.
    """
    import httpx

    from backend.database import create_pool

    async def body():
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with api.acquire() as c:
                _, wa = await _register(c, "byok-a@example.com")
                _, wb = await _register(c, "byok-b@example.com")

            app, current = _make_app(api)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # Workspace A stores a Saleshandy key.
                current["workspace_id"] = wa
                res = await client.put(
                    "/integrations/saleshandy", json={"api_key": "sk-workspace-a"}
                )
                assert res.status_code == 200, res.text
                assert res.json() == {"status": "configured", "provider": "saleshandy"}

                res = await client.get("/integrations")
                assert res.status_code == 200
                assert [r["provider"] for r in res.json()] == ["saleshandy"]

                # Workspace B must see none of A's rows (RLS isolation, not app-level filtering).
                current["workspace_id"] = wb
                res = await client.get("/integrations")
                assert res.status_code == 200
                assert res.json() == []

                # B cannot delete a provider it never configured, even though A has one —
                # RLS hides A's row entirely, so this is 404, never a cross-tenant delete.
                res = await client.delete("/integrations/saleshandy")
                assert res.status_code == 404

                # B configures its own, independent credential.
                res = await client.put(
                    "/integrations/rocketreach", json={"api_key": "sk-workspace-b"}
                )
                assert res.status_code == 200

                # Back to A: still exactly its own row, unaffected by B's writes.
                current["workspace_id"] = wa
                res = await client.get("/integrations")
                assert res.status_code == 200
                assert [r["provider"] for r in res.json()] == ["saleshandy"]

                # A can delete its own row.
                res = await client.delete("/integrations/saleshandy")
                assert res.status_code == 200
                res = await client.get("/integrations")
                assert res.json() == []
        finally:
            await api.close()

    asyncio.run(body())


def test_get_workspace_credentials_decrypts_only_the_calling_workspaces_rows(clean_db):
    """Service-level companion: agent/backend.session's runtime key-injection path
    (get_workspace_credentials) must resolve exactly the calling workspace's plaintext
    keys — never another workspace's, even when both share a pool with no request-level
    auth boundary in front of it (this is exactly how backend/session.py calls it)."""
    from backend.database import create_pool
    from backend.services.integrations import get_workspace_credentials, upsert_credential

    async def body():
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with api.acquire() as c:
                _, wa = await _register(c, "byok-svc-a@example.com")
                _, wb = await _register(c, "byok-svc-b@example.com")

            await upsert_credential(api, wa, "saleshandy", "default", {"api_key": "sk-a"})
            await upsert_credential(api, wb, "apollo", "default", {"api_key": "sk-b"})

            creds_a = await get_workspace_credentials(api, wa)
            creds_b = await get_workspace_credentials(api, wb)
            assert creds_a == {"saleshandy": "sk-a"}
            assert creds_b == {"apollo": "sk-b"}
        finally:
            await api.close()

    asyncio.run(body())


def test_upsert_credential_fails_closed_with_no_workspace_context(clean_db):
    """Positive control (same shape as test_rls_live.py's reconcile/claim controls): the bare
    ``pool.acquire()`` this suite exists to catch has no ``app.current_workspace_id`` set, so
    WITH CHECK on ``encrypted_credentials`` rejects the INSERT outright under FORCE RLS — it
    is not merely 'less scoped', it cannot write at all. This is the exact failure a caller
    that reverts ``upsert_credential``/the router back to a bare acquire will reproduce."""
    from backend.database import create_pool
    from backend.services.integrations import get_kek
    from backend.vault import encrypt

    async def body():
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            async with api.acquire() as c:
                _, wid = await _register(c, "byok-noscope@example.com")

            kek = get_kek()
            enc = encrypt('{"api_key":"sk-noscope"}', kek)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with api.acquire() as c, c.transaction():
                    await c.execute(
                        """
                        INSERT INTO encrypted_credentials
                            (workspace_id, provider, account_ref, encrypted_data, wrapped_dek,
                             iv, tag, key_version)
                        VALUES ($1::uuid, 'saleshandy', 'default', $2, $3, $4, $5, $6)
                        """,
                        wid,
                        enc["encrypted_data"],
                        enc["wrapped_dek"],
                        enc["iv"],
                        enc["tag"],
                        enc["key_version"],
                    )
        finally:
            await api.close()

    asyncio.run(body())
