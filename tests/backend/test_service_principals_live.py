"""Live-Postgres suite for V026 service principals (Fleet Phase A, Task 1).

Proves, against a REAL Postgres, that the api_keys → agents binding actually
isolates tenants and enforces its constraints the way production runs them —
the thing a mocked suite cannot prove (see tests/backend/test_rls_live.py and
tests/mcp_server/test_mcp_rls_live.py, whose fixtures/patterns this module
reuses). Skipped unless GTM_TEST_PG_ADMIN_DSN is set (see tests/conftest.py).
Each test wraps an async body in asyncio.run so the tier needs no
pytest-asyncio dependency.

All queries that model the runtime run as gtm_api (the non-owner role) —
either through resolve_api_key() directly, through workspace_scope(), or
through mcp_server.auth.validate_api_key(). Seeding uses the admin/owner
connection (mirrors _seed_key in test_mcp_rls_live.py), since RLS is not the
thing under test when inserting fixture rows.

Lens coverage:
  - resolve_api_key() function metadata (SECURITY DEFINER, ownership, grants)
  - the resolver carries the bound agent_id through (bound / unbound / absent)
  - revoked/expired keys still return no row (negative controls: flip the
    field and show the row appears, so the check can discriminate)
  - the composite FK stops a key being bound to another workspace's agent
  - agents.read_scope / daily_dispatch_cap CHECK constraints
  - runs.principal_kind CHECK constraint (rejects the reserved 'delegated_agent')
  - RLS still hides the new columns cross-workspace
  - a workspace delete cascades cleanly with a bound key present
  - mcp_server.auth.validate_api_key still works against the widened function
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

from gtm_core.db import workspace_scope  # noqa: E402


async def _register(conn, email: str) -> str:
    """Insert a user (fires the SECURITY DEFINER bootstrap trigger); return workspace id."""
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name, password_hash) "
        "VALUES($1, $2, 'h') RETURNING id::text",
        email,
        email.split("@")[0],
    )
    return await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


async def _seed_agent(admin_conn, wid: str, name: str = "fleet-agent") -> str:
    """Seed an agents row (owner pool). Returns the agent id."""
    return await admin_conn.fetchval(
        "INSERT INTO agents(workspace_id, name, profile_name) "
        "VALUES($1::uuid, $2, 'acme') RETURNING id::text",
        wid,
        name,
    )


async def _seed_key(
    admin_conn,
    wid: str,
    raw: str,
    *,
    agent_id: str | None = None,
    entitlement: str = "pro",
    revoked: bool = False,
    expired: bool = False,
) -> str:
    """Seed an api_keys row (owner pool). Returns the key id."""
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    return await admin_conn.fetchval(
        "INSERT INTO api_keys(workspace_id, key_hash, prefix, entitlement, agent_id, "
        "                     revoked_at, expires_at) "
        "VALUES($1::uuid, $2, $3, $4, $5::uuid, "
        "       CASE WHEN $6 THEN now() ELSE NULL END, "
        "       CASE WHEN $7 THEN now() - interval '1 day' ELSE NULL END) "
        "RETURNING id::text",
        wid,
        key_hash,
        raw[:8],
        entitlement,
        agent_id,
        revoked,
        expired,
    )


async def _resolve(api_pool, raw: str):
    """Call resolve_api_key() directly on the gtm_api pool — no workspace context yet
    (mirrors mcp_server/auth.py: the resolver IS the pre-tenant bootstrap)."""
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    async with api_pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM resolve_api_key($1)", key_hash)


def test_resolve_api_key_function_metadata(clean_db):
    """SECURITY DEFINER, owned by gtm_bootstrap; gtm_api has EXECUTE, PUBLIC does not."""

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        try:
            async with admin.acquire() as c:
                row = await c.fetchrow(
                    "SELECT prosecdef, pg_get_userbyid(proowner) AS owner "
                    "FROM pg_proc WHERE proname = 'resolve_api_key'"
                )
                assert row["prosecdef"] is True
                assert row["owner"] == "gtm_bootstrap"

                has_api = await c.fetchval(
                    "SELECT has_function_privilege('gtm_api', 'resolve_api_key(text)', 'EXECUTE')"
                )
                has_public = await c.fetchval(
                    "SELECT has_function_privilege('public', 'resolve_api_key(text)', 'EXECUTE')"
                )
                assert has_api is True
                assert has_public is False
        finally:
            await admin.close()

    asyncio.run(body())


def test_resolve_api_key_returns_bound_agent(clean_db):
    """A key bound to an agent in the SAME workspace resolves that agent_id too."""

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                agent_a = await _seed_agent(c, wa)
                await _seed_key(c, wa, "sk-bound-key", agent_id=agent_a, entitlement="pro")

            row = await _resolve(api, "sk-bound-key")
            assert row is not None
            assert str(row["workspace_id"]) == wa
            assert row["entitlement"] == "pro"
            assert str(row["agent_id"]) == agent_a
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_resolve_api_key_unbound_key_has_null_agent(clean_db):
    """A key with no agent_id resolves with agent_id NULL — never falls over."""

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                await _seed_key(c, wa, "sk-unbound-key")

            row = await _resolve(api, "sk-unbound-key")
            assert row is not None
            assert str(row["workspace_id"]) == wa
            assert row["agent_id"] is None
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_revoked_and_expired_keys_return_no_row(clean_db):
    """Negative controls: flip revoked/expired off and show the row NOW appears,
    so a false 'no row' (e.g. a typo'd predicate) cannot pass silently."""

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                await _seed_key(c, wa, "sk-revoked-key", revoked=True)
                await _seed_key(c, wa, "sk-expired-key", expired=True)

            assert await _resolve(api, "sk-revoked-key") is None
            assert await _resolve(api, "sk-expired-key") is None

            # Discriminate: clear the fields and the SAME key now resolves.
            async with admin.acquire() as c:
                await c.execute(
                    "UPDATE api_keys SET revoked_at = NULL WHERE key_hash = $1",
                    hashlib.sha256(b"sk-revoked-key").hexdigest(),
                )
                await c.execute(
                    "UPDATE api_keys SET expires_at = NULL WHERE key_hash = $1",
                    hashlib.sha256(b"sk-expired-key").hexdigest(),
                )
            assert await _resolve(api, "sk-revoked-key") is not None
            assert await _resolve(api, "sk-expired-key") is not None
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_bind_key_to_agent_in_another_workspace_fails(clean_db):
    """The composite FK (agent_id, workspace_id) -> agents(id, workspace_id) refuses a
    cross-tenant bind. Inserted via the admin/owner connection so RLS is not what's
    stopping it — the FK constraint is."""

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                wb = await _register(c, "b@example.com")
                agent_a = await _seed_agent(c, wa)

                with pytest.raises(asyncpg.ForeignKeyViolationError):
                    await _seed_key(c, wb, "sk-cross-tenant", agent_id=agent_a)
        finally:
            await admin.close()

    asyncio.run(body())


def test_agents_read_scope_and_dispatch_cap_constraints(clean_db):
    """read_scope defaults to 'own'; an invalid value is rejected; a negative
    daily_dispatch_cap is rejected."""

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                agent_id = await _seed_agent(c, wa)

                read_scope = await c.fetchval(
                    "SELECT read_scope FROM agents WHERE id = $1::uuid", agent_id
                )
                assert read_scope == "own"

                with pytest.raises(asyncpg.CheckViolationError):
                    await c.execute(
                        "UPDATE agents SET read_scope = 'everyone' WHERE id = $1::uuid",
                        agent_id,
                    )
                with pytest.raises(asyncpg.CheckViolationError):
                    await c.execute(
                        "UPDATE agents SET daily_dispatch_cap = -1 WHERE id = $1::uuid",
                        agent_id,
                    )

                # A valid update of both still works (constraints don't over-reject).
                await c.execute(
                    "UPDATE agents SET read_scope = 'workspace', daily_dispatch_cap = 25 "
                    "WHERE id = $1::uuid",
                    agent_id,
                )
        finally:
            await admin.close()

    asyncio.run(body())


def test_runs_principal_kind_accepts_user_and_service_rejects_delegated_agent(clean_db):
    """principal_kind accepts 'user'/'service'; 'delegated_agent' is reserved and must
    not be storable in v1."""

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")

                for kind in ("user", "service"):
                    rid = await c.fetchval(
                        "INSERT INTO runs(workspace_id, profile_name, prompt, "
                        "                 principal_kind, principal_id) "
                        "VALUES($1::uuid, 'p', 'x', $2, 'id-1') RETURNING id::text",
                        wa,
                        kind,
                    )
                    assert rid is not None

                with pytest.raises(asyncpg.CheckViolationError):
                    await c.execute(
                        "INSERT INTO runs(workspace_id, profile_name, prompt, principal_kind) "
                        "VALUES($1::uuid, 'p', 'x', 'delegated_agent')",
                        wa,
                    )
        finally:
            await admin.close()

    asyncio.run(body())


def test_rls_hides_new_columns_cross_workspace(clean_db):
    """Under FORCE RLS as gtm_api scoped to workspace A, workspace B's api_keys.agent_id
    and runs.principal_id are invisible — the new columns ride the existing row-level
    policy, they don't need one of their own."""

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                wb = await _register(c, "b@example.com")
                agent_b = await _seed_agent(c, wb, "agent-b")
                await _seed_key(c, wb, "sk-key-b", agent_id=agent_b)
                await c.execute(
                    "INSERT INTO runs(workspace_id, profile_name, prompt, "
                    "                 principal_kind, principal_id) "
                    "VALUES($1::uuid, 'p', 'x', 'service', 'key-b')",
                    wb,
                )

            async with workspace_scope(api, wa) as c:
                assert await c.fetchval("SELECT count(*) FROM api_keys") == 0
                assert await c.fetchval("SELECT count(*) FROM runs") == 0

            # Sanity: workspace B sees its own rows, including the new columns.
            async with workspace_scope(api, wb) as c:
                agent_id = await c.fetchval("SELECT agent_id FROM api_keys")
                principal_id = await c.fetchval("SELECT principal_id FROM runs")
                assert str(agent_id) == agent_b
                assert principal_id == "key-b"
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_workspace_delete_cascades_with_bound_key(clean_db):
    """Deleting a workspace with a bound key present cascades cleanly — no FK
    ordering error between the agents cascade and the api_keys cascade."""

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                agent_a = await _seed_agent(c, wa)
                await _seed_key(c, wa, "sk-doomed-key", agent_id=agent_a)

                await c.execute("DELETE FROM workspaces WHERE id = $1::uuid", wa)

                assert (
                    await c.fetchval(
                        "SELECT count(*) FROM agents WHERE workspace_id = $1::uuid", wa
                    )
                    == 0
                )
                assert (
                    await c.fetchval(
                        "SELECT count(*) FROM api_keys WHERE workspace_id = $1::uuid", wa
                    )
                    == 0
                )
        finally:
            await admin.close()

    asyncio.run(body())


def test_mcp_auth_path_survives_extra_column(clean_db):
    """mcp_server.auth.validate_api_key still returns a context for a valid key —
    the extra resolve_api_key() column does not break the SELECT * caller."""

    async def body():
        from backend.database import create_pool
        from mcp_server.auth import validate_api_key

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                agent_a = await _seed_agent(c, wa)
                await _seed_key(c, wa, "sk-mcp-key", agent_id=agent_a, entitlement="pro_plus")

            ctx = await validate_api_key("sk-mcp-key", api)
            assert ctx is not None
            assert ctx.workspace_id == wa
            assert str(ctx.entitlement.value) == "pro_plus"
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


# ── require_principal end to end, on the runtime role ───────────────────────────
# The mocked suites cannot see RLS: a fake pool returns whatever row it is handed. These
# drive the REAL require_principal against gtm_api (FORCE RLS on agents) and read the
# REAL denials.jsonl it writes, so a query outside workspace_scope, or an audit record
# that drops the principal, fails here.


def _principal_request(pool, tmp_path, path: str = "/v1/runs"):
    from types import SimpleNamespace

    from agent.config import Config

    cfg = Config(
        repo_root=tmp_path,
        plugin_path=tmp_path / "plugin",
        profiles_root=tmp_path / "profiles",
        content_root=tmp_path / "content",
        default_profile="acme",
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(pool=pool, cfg=cfg)),
        state=SimpleNamespace(),
        url=SimpleNamespace(path=path),
    )


def _admission_denials(tmp_path, wid: str) -> list[dict]:
    import json

    path = tmp_path / "workspaces" / wid / "content" / "_admission" / "denials.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def _call_require_principal(api_pool, tmp_path, raw_key: str):
    from fastapi.security import HTTPAuthorizationCredentials

    from backend.callers.rest import require_principal

    request = _principal_request(api_pool, tmp_path)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=raw_key)
    return await require_principal(request, creds)


@pytest.fixture()
def _principal_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")
    return tmp_path


def test_require_principal_admits_a_bound_key_as_gtm_api(clean_db, _principal_env):
    tmp_path = _principal_env

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wid = await _register(c, "svc-admit@example.com")
                agent_id = await _seed_agent(c, wid)
                key_id = await _seed_key(c, wid, "sk-live-admit", agent_id=agent_id)
            principal = await _call_require_principal(api, tmp_path, "sk-live-admit")
            assert principal.kind == "service"
            assert principal.agent_id == agent_id
            assert principal.subject == key_id
            assert principal.workspace_id == wid
            assert _admission_denials(tmp_path, wid) == []
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_require_principal_paused_agent_writes_a_denial_naming_the_principal(
    clean_db, _principal_env
):
    from fastapi import HTTPException

    tmp_path = _principal_env

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wid = await _register(c, "svc-paused@example.com")
                agent_id = await _seed_agent(c, wid)
                key_id = await _seed_key(c, wid, "sk-live-paused", agent_id=agent_id)
                await c.execute("UPDATE agents SET status = 'paused' WHERE id = $1::uuid", agent_id)
            with pytest.raises(HTTPException) as exc_info:
                await _call_require_principal(api, tmp_path, "sk-live-paused")
            assert exc_info.value.status_code == 409
            assert exc_info.value.detail["code"] == "agent_paused"
            rows = _admission_denials(tmp_path, wid)
            assert len(rows) == 1
            assert rows[0]["principal_kind"] == "service"
            assert rows[0]["principal_id"] == key_id
            assert rows[0]["code"] == "agent_paused"
            assert rows[0]["decision"] == "deny"
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_require_principal_unbound_key_is_refused_and_recorded(clean_db, _principal_env):
    from fastapi import HTTPException

    tmp_path = _principal_env

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wid = await _register(c, "svc-unbound@example.com")
                key_id = await _seed_key(c, wid, "sk-live-unbound")
            with pytest.raises(HTTPException) as exc_info:
                await _call_require_principal(api, tmp_path, "sk-live-unbound")
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail["code"] == "api_key_unbound"
            # The key resolved, so its workspace IS known: the refusal is on its ledger.
            rows = _admission_denials(tmp_path, wid)
            assert [(r["principal_id"], r["code"]) for r in rows] == [(key_id, "api_key_unbound")]
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_require_principal_revoked_key_leaves_no_ledger_row(clean_db, _principal_env):
    from fastapi import HTTPException

    tmp_path = _principal_env

    async def body():
        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wid = await _register(c, "svc-revoked@example.com")
                agent_id = await _seed_agent(c, wid)
                await _seed_key(c, wid, "sk-live-revoked", agent_id=agent_id, revoked=True)
            with pytest.raises(HTTPException) as exc_info:
                await _call_require_principal(api, tmp_path, "sk-live-revoked")
            assert exc_info.value.status_code == 401
            assert exc_info.value.detail["code"] == "api_key_invalid"
            # Enumeration-safe resolver: no workspace is known, so nothing is written.
            assert _admission_denials(tmp_path, wid) == []
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())
