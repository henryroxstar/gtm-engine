"""Live-Postgres tests for ``dispatch_intents`` (V046) — always-on hardening test plan F4-7/8/9/10.

A mocked pool cannot see RLS, advisory-lock ordering, the unique index or a missing GRANT; the plan's §6 says
so, and those checks do not count unless they run here. Skipped unless ``GTM_TEST_PG_ADMIN_DSN`` is set — a
skip is NOT a pass, it means this property went unverified on that run.

The SAME scenarios the file store passes (tests/unit/dispatch_intent_scenarios.py) run against
``PgIntentStore``: "one decision function, two stores" is only true if both are held to one list.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.dbtest

asyncpg = pytest.importorskip("asyncpg")

from backend.services.dispatch_intents import PgIntentStore  # noqa: E402
from gtm_core.db import workspace_scope  # noqa: E402
from tests.unit.dispatch_intent_scenarios import SCENARIOS, SEQ, keys  # noqa: E402


async def _workspace(conn, email: str) -> str:
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name) VALUES($1, $2) RETURNING id",
        email,
        email.split("@")[0],
    )
    return await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


async def _two_workspaces(admin_dsn: str) -> tuple[str, str]:
    pool = await asyncpg.create_pool(admin_dsn, min_size=1, max_size=1)
    try:
        async with pool.acquire() as c:
            return (
                await _workspace(c, f"a-{uuid.uuid4().hex[:8]}@example.test"),
                await _workspace(c, f"b-{uuid.uuid4().hex[:8]}@example.test"),
            )
    finally:
        await pool.close()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__)
def test_postgres_store_passes_the_same_scenarios_as_the_file_store(clean_db, scenario):
    async def body():
        wid, _ = await _two_workspaces(clean_db["admin_dsn"])
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=10)
        try:
            await scenario(PgIntentStore(api, wid))
        finally:
            await api.close()

    asyncio.run(body())


def test_force_rls_is_armed_on_dispatch_intents(clean_db):
    async def body():
        pool = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        try:
            async with pool.acquire() as c:
                row = await c.fetchrow(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'dispatch_intents'"
                )
            assert row and row["relrowsecurity"] and row["relforcerowsecurity"]
        finally:
            await pool.close()

    asyncio.run(body())


def test_another_workspace_sees_nothing_and_the_same_recipient_is_free_there(clean_db):
    async def body():
        wid_a, wid_b = await _two_workspaces(clean_db["admin_dsn"])
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=4)
        try:
            a, b = PgIntentStore(api, wid_a), PgIntentStore(api, wid_b)
            claimed = await a.claim(SEQ, keys("shared@acme.example"), tool="import")
            await a.record_outcome(claimed.batch_id, "unknown")
            assert [x.batch_id for x in await a.unresolved()] == [
                claimed.batch_id
            ]  # positive control
            assert await b.unresolved() == []  # isolation: absence, not an error
            assert (await b.claim(SEQ, keys("shared@acme.example"), tool="import")).ok
            async with workspace_scope(api, wid_b) as conn:
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM dispatch_intents WHERE batch_id = $1",
                        claimed.batch_id,
                    )
                    == 0
                )
            with pytest.raises(KeyError):  # B cannot resolve A's batch
                await b.resolve(claimed.batch_id, "sent")
        finally:
            await api.close()

    asyncio.run(body())


def test_the_runtime_role_can_append_but_never_update_or_delete(clean_db):
    """F4-7: a row is never edited or removed — enforced by the GRANT, not by good manners."""

    async def body():
        wid, _ = await _two_workspaces(clean_db["admin_dsn"])
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            await PgIntentStore(api, wid).claim(SEQ, keys("x@acme.example"), tool="import")
            async with workspace_scope(api, wid) as conn:
                assert await conn.fetchval("SELECT count(*) FROM dispatch_intents") == 1
            for stmt in (
                "UPDATE dispatch_intents SET kind = 'failed'",
                "DELETE FROM dispatch_intents",
                "TRUNCATE dispatch_intents",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with workspace_scope(api, wid) as conn:
                        await conn.execute(stmt)
        finally:
            await api.close()

    asyncio.run(body())


def test_f4_9_the_unique_index_is_a_real_backstop_even_without_the_advisory_lock(clean_db):
    """Two claim rows for the same (recipient, generation) cannot both exist — checked at the DB."""

    async def body():
        wid, _ = await _two_workspaces(clean_db["admin_dsn"])
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            ins = (
                "INSERT INTO dispatch_intents (workspace_id, batch_id, sequence_id, recipient_key, kind, generation) "
                "VALUES ($1::uuid, $2, 'seq', 'r@acme.example', 'claim', 1)"
            )
            async with workspace_scope(api, wid) as conn:
                await conn.execute(ins, wid, "b-one")
            with pytest.raises(asyncpg.UniqueViolationError):
                async with workspace_scope(api, wid) as conn:
                    await conn.execute(ins, wid, "b-two")
        finally:
            await api.close()

    asyncio.run(body())


def test_f4_9_many_dispatchers_racing_yield_exactly_one_provider_call(clean_db):
    async def body():
        wid, _ = await _two_workspaces(clean_db["admin_dsn"])
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=16)
        try:
            store = PgIntentStore(api, wid)
            results = await asyncio.gather(
                *[
                    store.claim(SEQ, keys("race@acme.example", f"r{i}@acme.example"), tool="import")
                    for i in range(16)
                ]
            )
            assert sum(1 for r in results if r.ok) == 1
        finally:
            await api.close()

    asyncio.run(body())


def test_the_shared_dispatchers_work_end_to_end_against_the_postgres_store(clean_db):
    """The backend's wiring: dispatch_approved_publish / _enrollment with intents=PgIntentStore. A repeat is
    refused with ZERO provider calls, and a timeout blocks until a human resolves it through the store."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from agent import email_dispatch
    from agent.email_dispatch import dispatch_approved_enrollment
    from agent.publish import PublishResult
    from agent.publish_dispatch import dispatch_approved_publish
    from gtm_core.publish_hash import content_hash

    class Publisher:
        def __init__(self, *results):
            self.results, self.calls = list(results), 0

        async def publish(self, post, media, is_published=None, scheduled_at=None):
            self.calls += 1
            return self.results.pop(0)

    class Ledgers:  # history only: the intent ledger is Postgres in this path
        profile = "example"

        def published_content_hashes(self):
            return set()

        def append_history(self, entry):
            pass

    post = "A fictional post for the Postgres-backed dispatch path."

    async def body():
        wid, _ = await _two_workspaces(clean_db["admin_dsn"])
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=4)
        try:
            store = PgIntentStore(api, wid)
            pub = Publisher(PublishResult(ok=True, status="published"))
            staged = content_hash(post, ())
            first = await dispatch_approved_publish(
                pub, Ledgers(), post=post, staged_hash=staged, intents=store
            )
            again = await dispatch_approved_publish(
                pub, Ledgers(), post=post, staged_hash=staged, intents=store
            )
            assert first.ok and again.status == "already_dispatched" and pub.calls == 1

            timeout = Publisher(
                PublishResult(ok=False, status="error", error="TimeoutError", sent_state="unknown")
            )
            p2 = "A second fictional post that times out."
            out = await dispatch_approved_publish(
                timeout, Ledgers(), post=p2, staged_hash=content_hash(p2, ()), intents=store
            )
            assert out.sent_state == "unknown" and out.batch_id
            blocked = await dispatch_approved_publish(
                timeout, Ledgers(), post=p2, staged_hash=content_hash(p2, ()), intents=store
            )
            assert blocked.status == "outcome_unknown" and blocked.open_batches == (out.batch_id,)
            await store.resolve(out.batch_id, "not_sent")
            timeout.results.append(PublishResult(ok=True, status="published"))
            released = await dispatch_approved_publish(
                timeout, Ledgers(), post=p2, staged_hash=content_hash(p2, ()), intents=store
            )
            assert released.ok

            # enrollment: same story through the same store
            draft = {
                "tool": "import_prospects_to_sequence",
                "sequence_id": "seq-9",
                "step_id": "s1",
                "steps": [],
                "prospect_list": [{"Email": "a@acme.example"}],
            }

            async def no_refusal(*_a, **_k):
                return None

            async def rows(_k, prospects):
                return [{"fields": []} for _ in prospects], None

            cfg = SimpleNamespace(saleshandy_api_key="k", content_root="/nonexistent")
            with (
                patch.object(email_dispatch, "_lane_refusal", lambda *a, **k: None),
                patch.object(email_dispatch, "_load_refusal", lambda *a, **k: None),
                patch.object(email_dispatch, "_live_copy_refusal", no_refusal),
                patch.object(email_dispatch, "_import_rows", rows),
                patch(
                    "agent.mcp.saleshandy.server._import_prospects_to_sequence_request",
                    return_value="{}",
                ) as imp,
            ):
                e1 = await dispatch_approved_enrollment(cfg, Ledgers(), draft=draft, intents=store)
                e2 = await dispatch_approved_enrollment(cfg, Ledgers(), draft=draft, intents=store)
            assert e1.ok and e2.code == "already_dispatched" and imp.call_count == 1
        finally:
            await api.close()

    asyncio.run(body())
