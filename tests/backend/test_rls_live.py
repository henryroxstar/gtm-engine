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
    "run_nodes",
    "run_blocks",
    "run_artifacts",
    "agents",
    "run_gates",
    "run_events",
    "workspace_publish_settings",
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


def test_reconcile_reads_awaiting_gates_across_tenants(clean_db):
    """A4 (V018): startup reconciliation runs on gtm_api with NO workspace context,
    so a plain cross-tenant `SELECT FROM runs` is blocked by FORCE RLS (0 rows) — that
    was the bug that left gated runs stranded after a restart. The SECURITY DEFINER
    awaiting_approval_runs() returns every tenant's awaiting_approval rows so the
    reconcile can re-dispatch them (each is then acted on under workspace_scope)."""

    async def body():
        from backend.database import create_pool, workspace_scope

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with api.acquire() as c:
                _, wa = await _register(c, "a@example.com")
                _, wb = await _register(c, "b@example.com")
            # one awaiting_approval run in each of two distinct workspaces
            for wid in (wa, wb):
                rid = await _seed_run(api, workspace_scope, wid)
                async with workspace_scope(api, wid) as c:
                    await c.execute(
                        "UPDATE runs SET status='awaiting_approval' WHERE id=$1::uuid", rid
                    )

            async with api.acquire() as c, c.transaction():
                # positive control: the bare SELECT the reconcile USED to run sees 0
                # rows with no workspace context (this is precisely the A4 bug).
                assert (
                    await c.fetchval("SELECT count(*) FROM runs WHERE status='awaiting_approval'")
                    == 0
                )
                # the fix: the definer function returns BOTH tenants' gated runs, with
                # exactly the columns reconcile_gates consumes.
                rows = await c.fetch(
                    "SELECT id::text AS id, workspace_id::text AS workspace_id, "
                    "profile_name, prompt, agent_id::text AS agent_id "
                    "FROM awaiting_approval_runs()"
                )
                assert len(rows) == 2
                assert {r["workspace_id"] for r in rows} == {wa, wb}
        finally:
            await api.close()

    asyncio.run(body())


def test_claim_next_run_hands_one_queued_row_to_exactly_one_worker(clean_db):
    """A5/T27 — the exactly-once acceptance criterion, on a REAL engine.

    The fakes cannot prove this: `FOR UPDATE SKIP LOCKED` is a property of Postgres, not
    of the caller, and a single-threaded fake serialises the two claims anyway. Here the
    two claims are genuinely concurrent — the first transaction holds the row lock while
    the second runs — so a second worker must skip it and come back empty.

    Like the queue itself this runs on gtm_api with NO workspace context, which is why
    the positive control below matters: the bare SELECT the claim would otherwise use
    sees zero rows under FORCE RLS, exactly as it did for reconciliation before V018.
    """

    async def body():
        from backend.database import create_pool, workspace_scope

        api = await create_pool(clean_db["api_dsn"], min_size=2, max_size=4)
        try:
            async with api.acquire() as c:
                _, wid = await _register(c, "queue@example.com")
            rid = await _seed_run(api, workspace_scope, wid)
            async with workspace_scope(api, wid) as c:
                await c.execute("UPDATE runs SET status='queued' WHERE id=$1::uuid", rid)

            async with api.acquire() as c, c.transaction():
                # Positive control: with no workspace context a plain read of the queue
                # returns NOTHING under FORCE RLS. Without the SECURITY DEFINER function
                # the worker loop would quietly claim zero runs forever.
                assert await c.fetchval("SELECT count(*) FROM runs WHERE status='queued'") == 0

            # Two concurrent claims, the first still holding its transaction open.
            async with api.acquire() as ca, ca.transaction():
                first = await ca.fetch("SELECT * FROM claim_next_run($1, $2)", "worker-a", 90)
                async with api.acquire() as cb, cb.transaction():
                    second = await cb.fetch("SELECT * FROM claim_next_run($1, $2)", "worker-b", 90)
            assert len(first) == 1, "the first worker must claim the queued row"
            assert second == [], "SKIP LOCKED must hand the row to nobody else"
            assert str(first[0]["id"]) == rid
            assert first[0]["prev_status"] == "queued"
            assert first[0]["attempts"] == 1

            async with workspace_scope(api, wid) as c:
                row = await c.fetchrow(
                    "SELECT status, claimed_by, heartbeat_at FROM runs WHERE id=$1::uuid", rid
                )
            assert row["status"] == "running", "a claimed queued run is promoted"
            assert row["claimed_by"] == "worker-a"
            assert row["heartbeat_at"] is not None
        finally:
            await api.close()

    asyncio.run(body())


def test_a_live_lease_hides_a_gated_run_from_the_claim(clean_db):
    """A5/T4 on the real engine — the property that protects the 24h gate. A run parked
    at awaiting_approval with a FRESH heartbeat is invisible to the claim; the same run
    with an expired one is reclaimed AND keeps its awaiting_approval status."""

    async def body():
        from backend.database import create_pool, workspace_scope

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with api.acquire() as c:
                _, wid = await _register(c, "lease@example.com")
            rid = await _seed_run(api, workspace_scope, wid)
            async with workspace_scope(api, wid) as c:
                await c.execute(
                    """UPDATE runs SET status='awaiting_approval', claimed_by='worker-a',
                                       heartbeat_at = now()
                       WHERE id=$1::uuid""",
                    rid,
                )
            async with api.acquire() as c:
                assert await c.fetch("SELECT * FROM claim_next_run($1, $2)", "worker-b", 90) == []

            # Now let the lease expire. Liveness, never elapsed time: nothing about how
            # long the gate has been open changed, only whether its worker is alive.
            async with workspace_scope(api, wid) as c:
                await c.execute(
                    "UPDATE runs SET heartbeat_at = now() - interval '10 minutes' "
                    "WHERE id=$1::uuid",
                    rid,
                )
            async with api.acquire() as c:
                claimed = await c.fetch("SELECT * FROM claim_next_run($1, $2)", "worker-b", 90)
            assert len(claimed) == 1
            assert claimed[0]["prev_status"] == "awaiting_approval"

            async with workspace_scope(api, wid) as c:
                status = await c.fetchval("SELECT status FROM runs WHERE id=$1::uuid", rid)
            assert status == "awaiting_approval", (
                "a reclaimed gated run must NOT be promoted to running — that would lie "
                "to every client polling it"
            )
        finally:
            await api.close()

    asyncio.run(body())


def test_a_worker_can_only_beat_its_own_leases(clean_db):
    """touch_worker_runs is scoped to claimed_by: a worker must never be able to keep
    ANOTHER worker's dead run out of reach of the reclaimer."""

    async def body():
        from backend.database import create_pool, workspace_scope

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with api.acquire() as c:
                _, wid = await _register(c, "beat@example.com")
            mine = await _seed_run(api, workspace_scope, wid)
            theirs = await _seed_run(api, workspace_scope, wid)
            async with workspace_scope(api, wid) as c:
                await c.execute(
                    "UPDATE runs SET status='running', claimed_by='worker-a', "
                    "heartbeat_at = now() - interval '10 minutes' WHERE id=$1::uuid",
                    mine,
                )
                await c.execute(
                    "UPDATE runs SET status='running', claimed_by='worker-b', "
                    "heartbeat_at = now() - interval '10 minutes' WHERE id=$1::uuid",
                    theirs,
                )
            async with api.acquire() as c:
                beaten = await c.fetchval("SELECT touch_worker_runs($1)", "worker-a")
            assert beaten == 1

            async with api.acquire() as c:
                claimed = await c.fetch("SELECT * FROM claim_next_run($1, $2)", "worker-c", 90)
            assert len(claimed) == 1
            assert str(claimed[0]["id"]) == theirs, (
                "only the run whose worker did NOT beat may be reclaimed"
            )
        finally:
            await api.close()

    asyncio.run(body())


def test_run_events_rows_are_invisible_to_another_workspace(clean_db):
    """A5/T28 — the publish relay writes run_events under the run's workspace, and the
    durable event log is the stream's replay source. If it leaked across tenants, one
    workspace could replay another's run output."""

    async def body():
        from backend.database import create_pool, workspace_scope

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with api.acquire() as c:
                _, wa = await _register(c, "ea@example.com")
                _, wb = await _register(c, "eb@example.com")
            rid = await _seed_run(api, workspace_scope, wa)
            async with workspace_scope(api, wa) as c:
                await c.execute(
                    """INSERT INTO run_events(run_id, workspace_id, event, data)
                       VALUES($1::uuid, $2::uuid, 'status', '{"status":"running"}'::jsonb)""",
                    rid,
                    wa,
                )
                assert await c.fetchval("SELECT count(*) FROM run_events") == 1
            async with workspace_scope(api, wb) as c:
                assert await c.fetchval("SELECT count(*) FROM run_events") == 0
                assert (
                    await c.fetch(
                        "SELECT id FROM run_events WHERE run_id = $1::uuid AND id > 0", rid
                    )
                    == []
                ), "the replay read must be empty for a workspace that does not own the run"
        finally:
            await api.close()

    asyncio.run(body())
