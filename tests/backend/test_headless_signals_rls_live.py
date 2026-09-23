"""Live-Postgres tenant isolation for ``headless_signals`` (V043).

The mocked suite in ``test_headless_queue.py`` can prove the code *enters*
``workspace_scope``; only a real database can prove the scope *filters*. That gap is
how V041 shipped: the table had no RLS at all and every test was green.

Runs under the ``dbtest`` marker — skipped unless ``GTM_TEST_PG_ADMIN_DSN`` is set
(the ``backend-db`` CI job provisions ``postgres:16`` and sets it). A skip here is not
a pass; it means this property went unverified on that run.

Each test carries its own positive control: the owning workspace must SEE the row in
the same configuration where the other workspace must not, so a misconfigured fixture
cannot read as isolation.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.dbtest

asyncpg = pytest.importorskip("asyncpg")

from backend.services.signals.queue import (  # noqa: E402
    claim_next_signal,
    complete_signal,
    enqueue_signal,
    get_signal,
)
from gtm_core.db import workspace_scope  # noqa: E402


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


def test_force_rls_is_armed_on_headless_signals(clean_db):
    """V043 §3 — ENABLE alone leaves the owner exempt; FORCE is what filters."""

    async def body():
        pool = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
        try:
            async with pool.acquire() as c:
                row = await c.fetchrow(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'headless_signals'"
                )
                assert row is not None, "headless_signals does not exist"
                assert row["relrowsecurity"] is True, "ENABLE ROW LEVEL SECURITY missing"
                assert row["relforcerowsecurity"] is True, "FORCE ROW LEVEL SECURITY missing"
        finally:
            await pool.close()

    asyncio.run(body())


def test_a_signal_is_invisible_to_another_workspace(clean_db):
    async def body():
        wid_a, wid_b = await _two_workspaces(clean_db["admin_dsn"])
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            created = await enqueue_signal(
                api,
                workspace_id=wid_a,
                profile_name="acme",
                signal_type="market-harvest",
                payload_ref="content/acme/signals/sig-1.json",
                idempotency_key="sig-1",
            )
            sid = created["id"]

            # positive control: the owner sees it
            assert await get_signal(api, wid_a, sid) is not None

            # the isolation property: absence, not an error
            assert await get_signal(api, wid_b, sid) is None

            # and a raw scoped read agrees
            async with workspace_scope(api, wid_b) as conn:
                assert await conn.fetchval("SELECT count(*) FROM headless_signals") == 0
            async with workspace_scope(api, wid_a) as conn:
                assert await conn.fetchval("SELECT count(*) FROM headless_signals") == 1
        finally:
            await api.close()

    asyncio.run(body())


def test_another_workspace_cannot_complete_someone_elses_signal(clean_db):
    async def body():
        wid_a, wid_b = await _two_workspaces(clean_db["admin_dsn"])
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            created = await enqueue_signal(
                api,
                workspace_id=wid_a,
                profile_name="acme",
                signal_type="market-harvest",
                payload_ref="content/acme/signals/sig-1.json",
                idempotency_key="sig-1",
            )
            claimed = await claim_next_signal(api, worker_id="w1")
            assert claimed is not None
            assert str(claimed["workspace_id"]) == wid_a, "the claim must report the owner"

            # B cannot finish A's work...
            assert await complete_signal(api, wid_b, created["id"]) is False
            # ...and A still can (positive control, same configuration)
            assert await complete_signal(api, wid_a, created["id"]) is True
        finally:
            await api.close()

    asyncio.run(body())


def test_the_same_idempotency_key_is_free_in_another_workspace(clean_db):
    """V043 §2 — a global UNIQUE would fail on a row the tenant cannot even see."""

    async def body():
        wid_a, wid_b = await _two_workspaces(clean_db["admin_dsn"])
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            kwargs = {
                "profile_name": "acme",
                "signal_type": "market-harvest",
                "payload_ref": "content/acme/signals/sig-1.json",
                "idempotency_key": "shared-key",
            }
            a = await enqueue_signal(api, workspace_id=wid_a, **kwargs)
            b = await enqueue_signal(api, workspace_id=wid_b, **kwargs)
            assert a["id"] != b["id"], "two workspaces collapsed onto one signal row"

            # ...while a repeat WITHIN one workspace is still idempotent
            again = await enqueue_signal(api, workspace_id=wid_a, **kwargs)
            assert again["id"] == a["id"]
        finally:
            await api.close()

    asyncio.run(body())


def test_the_claim_spans_workspaces_but_hands_back_the_owner(clean_db):
    """The claim is cross-tenant on purpose; what matters is that it reports whose."""

    async def body():
        wid_a, wid_b = await _two_workspaces(clean_db["admin_dsn"])
        api = await asyncpg.create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            for i, wid in enumerate((wid_a, wid_b)):
                await enqueue_signal(
                    api,
                    workspace_id=wid,
                    profile_name="acme",
                    signal_type="market-harvest",
                    payload_ref=f"content/acme/signals/sig-{i}.json",
                    idempotency_key=f"sig-{i}",
                )

            seen = set()
            for _ in range(2):
                claimed = await claim_next_signal(api, worker_id="w1")
                assert claimed is not None, "a worker could not reach both tenants' signals"
                seen.add(str(claimed["workspace_id"]))

            assert seen == {wid_a, wid_b}
            assert await claim_next_signal(api, worker_id="w1") is None
        finally:
            await api.close()

    asyncio.run(body())
