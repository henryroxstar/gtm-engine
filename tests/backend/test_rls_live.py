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
import time

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
    "unified_metering_log",
    "push_tokens",
    "entitlement_sync_events",
    "run_nodes",
    "run_blocks",
    "run_artifacts",
    "agents",
    "run_gates",
    "run_events",
    "workspace_publish_settings",
    "tenant_accounts",
    "tenant_contacts",
    "tenant_suppression",
    "tenant_people",
    "tenant_engagements",
    "tenant_outcomes",
    "tenant_content_items",
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


def test_a_gate_wait_reclaim_never_burns_attempts(clean_db):
    """RL-17 (V024) on the real engine. A run parked at `awaiting_approval` can have its
    lease reclaimed repeatedly — purely from waiting out `p_lease_s`, with zero actual
    execution failures — and `attempts` must never move. Reclaim it MORE than
    MAX_ATTEMPTS (3, backend/services/runs/queue.py) times to prove dispatch_claimed's
    "failed too many times" guard can never fire on a gate wait alone."""

    async def body():
        from backend.database import create_pool, workspace_scope

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with api.acquire() as c:
                _, wid = await _register(c, "gatewait@example.com")
            rid = await _seed_run(api, workspace_scope, wid)
            async with workspace_scope(api, wid) as c:
                await c.execute("UPDATE runs SET status='queued' WHERE id=$1::uuid", rid)

            # First dispatch: promotes queued -> running, attempts 0 -> 1.
            async with api.acquire() as c, c.transaction():
                first = await c.fetch("SELECT * FROM claim_next_run($1, $2)", "worker-a", 90)
            assert len(first) == 1
            assert first[0]["attempts"] == 1

            # The node hits its gate: parked at awaiting_approval, pre-reclaim attempts=1.
            async with workspace_scope(api, wid) as c:
                await c.execute("UPDATE runs SET status='awaiting_approval' WHERE id=$1::uuid", rid)
                pre_reclaim_attempts = await c.fetchval(
                    "SELECT attempts FROM runs WHERE id=$1::uuid", rid
                )
            assert pre_reclaim_attempts == 1

            # Reclaim 4 times (> MAX_ATTEMPTS=3), purely by expiring the lease each time —
            # exactly what a long gate wait does to a worker's heartbeat.
            for n in range(4):
                async with workspace_scope(api, wid) as c:
                    await c.execute(
                        "UPDATE runs SET heartbeat_at = now() - interval '1000 seconds' "
                        "WHERE id=$1::uuid",
                        rid,
                    )
                async with api.acquire() as c, c.transaction():
                    claimed = await c.fetch(
                        "SELECT * FROM claim_next_run($1, $2)", f"worker-gate-{n}", 90
                    )
                assert len(claimed) == 1, f"reclaim {n} should have found the gated row"
                assert claimed[0]["prev_status"] == "awaiting_approval"
                assert claimed[0]["attempts"] == pre_reclaim_attempts, (
                    f"reclaim {n} of an awaiting_approval run must NOT bump attempts"
                )

            async with workspace_scope(api, wid) as c:
                row = await c.fetchrow("SELECT status, attempts FROM runs WHERE id=$1::uuid", rid)
            assert row["status"] == "awaiting_approval"
            assert row["attempts"] == pre_reclaim_attempts
        finally:
            await api.close()

    asyncio.run(body())


def test_a_stranded_running_lease_reclaim_still_burns_attempts(clean_db):
    """RL-17 (V024) regression guard: the fix must not touch the crash-loop protection
    MAX_ATTEMPTS exists for. A run that is genuinely 'running' (never gated) whose lease
    expires — a crashed or killed worker, not a human waiting — must still increment
    `attempts` on every reclaim exactly as V021 always did."""

    async def body():
        from backend.database import create_pool, workspace_scope

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with api.acquire() as c:
                _, wid = await _register(c, "stuckrun@example.com")
            rid = await _seed_run(api, workspace_scope, wid)
            async with workspace_scope(api, wid) as c:
                await c.execute(
                    "UPDATE runs SET status='running', claimed_by='worker-dead', "
                    "heartbeat_at = now() - interval '1000 seconds', attempts = 1 "
                    "WHERE id=$1::uuid",
                    rid,
                )

            async with api.acquire() as c, c.transaction():
                claimed = await c.fetch("SELECT * FROM claim_next_run($1, $2)", "worker-b", 90)
            assert len(claimed) == 1
            assert claimed[0]["prev_status"] == "running"
            assert claimed[0]["attempts"] == 2, (
                "a stranded running lease's reclaim must still count against MAX_ATTEMPTS"
            )

            async with workspace_scope(api, wid) as c:
                row = await c.fetchrow("SELECT status, attempts FROM runs WHERE id=$1::uuid", rid)
            assert row["status"] == "running"
            assert row["attempts"] == 2
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


def _account_linked_identity_sql() -> str:
    """The EXACT query DELETE /v1/account's federated step-up runs, read from the router's
    source. Parsed, not imported: this tier installs only pytest + asyncpg, and importing the
    router pulls in FastAPI. A mocked suite matches this text by substring, so only here are
    its column names and the ``$3::uuid`` cast checked against a real schema."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "backend" / "routers" / "account.py"
    for node in ast.parse(src.read_text(encoding="utf-8")).body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if any(isinstance(t, ast.Name) and t.id == "LINKED_IDENTITY_SQL" for t in targets):
            # Adjacent string literals parse to ONE Constant, so this is the full query text.
            assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            return node.value.value
    raise AssertionError("backend/routers/account.py no longer defines LINKED_IDENTITY_SQL")


def test_account_step_up_identity_link_matches_only_this_users_issuer_subject(clean_db):
    """The federated DELETE step-up refuses unless (issuer, subject) maps to the authenticated
    user. Run as gtm_api on the plain pool, with a text user id, exactly as the router does."""
    sql = _account_linked_identity_sql()
    issuer_a, issuer_b = "https://idp-a.example", "https://idp-b.example"

    async def body():
        from backend.database import create_pool

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            async with api.acquire() as c:
                alice, _ = await _register(c, "fa@example.com")
                bob, _ = await _register(c, "fb@example.com")
                await c.execute(
                    "INSERT INTO external_identities(issuer, subject, user_id) "
                    "VALUES($1, $2, $3::uuid)",
                    issuer_a,
                    "subject-a",
                    alice,
                )
                assert await c.fetchval(sql, issuer_a, "subject-a", alice) == 1
                assert await c.fetchval(sql, issuer_a, "subject-a", bob) is None
                assert await c.fetchval(sql, issuer_b, "subject-a", alice) is None
                assert await c.fetchval(sql, issuer_a, "subject-b", alice) is None
        finally:
            await api.close()

    asyncio.run(body())


def test_a_failed_runs_error_code_is_written_and_read_under_rls(clean_db):
    """M-07 (V023) on the real engine. The fakes emulate the UPDATE by regex; only Postgres
    proves the column exists, is nullable with no default, and that gtm_api — FORCE RLS, no
    BYPASSRLS — writes it through ``_fail_run`` and reads it back on both read paths (the poll
    query and the stream's opening read, each an asyncpg Record), while another tenant sees
    nothing. The queue claim, which names its columns, still hands the run out after V023."""

    async def body():
        from backend.database import create_pool, workspace_scope
        from backend.services.runs.persistence import _fail_run
        from backend.services.runs.queries import fetch_run_detail
        from backend.services.runs.stream import _read_opening, _terminal_frame

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                column = await c.fetchrow(
                    "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_name = 'runs' AND column_name = 'error_code'"
                )
            assert dict(column) == {
                "data_type": "text",
                "is_nullable": "YES",
                "column_default": None,
            }

            async with api.acquire() as c:
                _, wa = await _register(c, "failed-a@example.com")
                _, wb = await _register(c, "failed-b@example.com")
            rid = await _seed_run(api, workspace_scope, wa)
            async with workspace_scope(api, wa) as c:
                await c.execute("UPDATE runs SET status='queued' WHERE id=$1::uuid", rid)
            async with api.acquire() as c, c.transaction():
                claimed = await c.fetch("SELECT * FROM claim_next_run($1, $2)", "worker-m07", 90)
            assert [str(r["id"]) for r in claimed] == [rid]
            async with workspace_scope(api, wa) as c:
                assert (
                    await c.fetchval("SELECT error_code FROM runs WHERE id=$1::uuid", rid) is None
                )

            await _fail_run(api, wa, rid, "gate timeout", error_code="gate_timeout")

            async with workspace_scope(api, wa) as c:
                row = await c.fetchrow(
                    "SELECT status, error, error_code FROM runs WHERE id=$1::uuid", rid
                )
                opening = await _read_opening(c, wa, rid, None)
            assert dict(row) == {
                "status": "failed",
                "error": "gate timeout",
                "error_code": "gate_timeout",
            }
            detail = await fetch_run_detail(api, wa, rid)
            assert detail["error_code"] == "gate_timeout"
            assert detail["stages"][0]["error_code"] == "gate_timeout"
            assert '"error_code":"gate_timeout"' in _terminal_frame(opening.row)
            async with workspace_scope(api, wb) as c:
                assert (
                    await c.fetchrow("SELECT error_code FROM runs WHERE id=$1::uuid", rid) is None
                )
            assert await fetch_run_detail(api, wb, rid) is None
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_client_request_id_concurrent_admission_creates_exactly_one_row(clean_db):
    """RT-04 (V025) on the real engine. Two concurrent POST /v1/runs-shaped calls for the
    SAME workspace + client_request_id must yield exactly one row and the SAME run_id — a
    fake conn cannot prove this, since the whole point is Postgres's own unique index plus
    _reserve_cap's advisory lock serialising the two transactions. Fires both through
    insert_run_row directly (asyncio.gather), the exact function POST /v1/runs calls."""

    async def body():
        import uuid

        from backend.database import create_pool, workspace_scope
        from backend.schemas import RunRequest
        from backend.services.runs.admission import insert_run_row

        api = await create_pool(clean_db["api_dsn"], min_size=2, max_size=4)
        try:
            async with api.acquire() as c:
                _, wid = await _register(c, "rt04-race@example.com")

            req = RunRequest(
                profile_name="p", prompt="do the thing", client_request_id="race-key-1"
            )
            run_id_a, run_id_b = str(uuid.uuid4()), str(uuid.uuid4())
            result_a, result_b = await asyncio.gather(
                insert_run_row(api, wid, run_id_a, profile_name="p", agent_row=None, body=req),
                insert_run_row(api, wid, run_id_b, profile_name="p", agent_row=None, body=req),
            )

            effective_a, existed_a, *_ = result_a
            effective_b, existed_b, *_ = result_b
            # Exactly one of the two calls won the race (existed=False); the other lost
            # (existed=True) and both report the SAME winning run_id.
            assert {existed_a, existed_b} == {True, False}
            assert effective_a == effective_b

            async with workspace_scope(api, wid) as c:
                rows = await c.fetch(
                    "SELECT id::text FROM runs WHERE workspace_id = $1::uuid "
                    "AND client_request_id = 'race-key-1'",
                    wid,
                )
            assert [r["id"] for r in rows] == [effective_a]
        finally:
            await api.close()

    asyncio.run(body())


def test_open_gate_row_never_clobbers_a_decision_committed_while_it_waits(clean_db):
    """RL-12 (Gap 1) on the real engine. ``_open_gate_row``'s reset is a single
    ``INSERT ... ON CONFLICT DO UPDATE ... WHERE`` statement precisely so that its WHERE
    clause is evaluated against the row's CURRENT COMMITTED state — never a value read by
    an earlier, separate statement. A fake conn cannot prove this: a single-threaded mock
    always serialises the two calls in SOME order and never makes one of them block on a
    real row lock, so it cannot show the WHERE clause seeing a decision that committed
    WHILE the reset statement was waiting to acquire the lock, only one that committed
    strictly before or strictly after.

    Session A holds a transaction open that recorded a decision (mirroring
    ``_record_gate_decision``) for 1.5s before committing; session B's ``_open_gate_row``
    call starts 0.3s in — while A's row lock is still held — and is timed. Two things
    together prove genuine cross-transaction atomicity rather than lucky sequencing:
    B's call must have BLOCKED for most of A's remaining hold (not returned early with a
    stale answer), and once unblocked it must report the row unchanged, not reset."""

    async def body():

        from backend.database import create_pool, workspace_scope
        from backend.services.runs.gates import (
            _content_sha,
            _open_gate_row,
            _record_gate_decision,
        )

        api = await create_pool(clean_db["api_dsn"], min_size=2, max_size=3)
        try:
            async with api.acquire() as c:
                _, wid = await _register(c, "rl12-race@example.com")
            run_id = await _seed_run(api, workspace_scope, wid)
            sha = _content_sha("the pending draft")

            # The gate's original open — the row session B's reset attempt will race
            # against. Its own `opened_at` is asserted unchanged at the end (Gap 2's
            # property: a left-alone row keeps its REAL open time, not a fresh one).
            first_open = await _open_gate_row(api, wid, run_id, "plan", "plan", sha)
            assert first_open[0] is True  # a fresh insert always "changes" something
            original_opened_at = first_open[1]

            hold_s, start_delay = 1.5, 0.3

            async def _hold_a_committed_decision() -> None:
                async with workspace_scope(api, wid) as conn:
                    recorded = await _record_gate_decision(conn, wid, run_id, "approve", None)
                    assert recorded is True
                    # The transaction — and the row lock its UPDATE took — stays open here.
                    await asyncio.sleep(hold_s)
                # Commits on exiting this block.

            async def _attempt_reset_while_a_holds_the_lock() -> tuple[tuple, float]:
                await asyncio.sleep(start_delay)
                started = time.monotonic()
                result = await _open_gate_row(api, wid, run_id, "plan", "plan", sha)
                return result, time.monotonic() - started

            _, (reset_result, elapsed) = await asyncio.gather(
                _hold_a_committed_decision(), _attempt_reset_while_a_holds_the_lock()
            )

            changed, opened_at, _db_now = reset_result
            assert changed is False, "a decision committed mid-wait must be left alone"
            assert opened_at == original_opened_at, (
                "a left-alone row must report its ORIGINAL open time, not a fresh one"
            )
            # The headline proof: B did not read stale pre-commit data and return early —
            # it genuinely waited on A's row lock for most of A's remaining hold.
            assert elapsed >= hold_s - start_delay - 0.3, (
                f"_open_gate_row returned too fast ({elapsed:.3f}s) to have waited on "
                "session A's row lock — it may have read a stale, pre-commit row instead"
            )

            async with workspace_scope(api, wid) as c:
                row = await c.fetchrow(
                    "SELECT state, decided_at, applied_at FROM run_gates "
                    "WHERE run_id = $1::uuid AND gate = 'plan' AND node_id = 'plan'",
                    run_id,
                )
            assert row["state"] == "approved" and row["decided_at"] is not None
            assert row["applied_at"] is None, "the decision must still be UNCLAIMED"
        finally:
            await api.close()

    asyncio.run(body())
