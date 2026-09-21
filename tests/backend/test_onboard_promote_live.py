"""Live-Postgres: onboarding promote registers the workspace's ``profiles`` row.

Promote used to rename the staged tree and stop, leaving the workspace with no
``profiles`` row — so ``GET /v1/profiles`` and ``GET /v1/packs`` came back empty and no
default agent could be provisioned. The registration SQL is the one piece a mocked
connection cannot prove: the default is decided against the one-default-per-workspace
partial unique index, and only a real Postgres can show that two concurrent promotes in a
fresh workspace end with exactly one default and no error, and that RLS keeps the row in
its own workspace.

Imports only ``backend.profile_rows`` + ``backend.database`` (no router, no Agent SDK), so
it runs in the dbtest CI job's minimal install.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

from backend.profile_rows import register_promoted_profile  # noqa: E402


async def _new_workspace(conn, email: str) -> str:
    """Minimal signup, mirroring test_rls_live.py's ``_register`` — the signup trigger
    provisions one workspace per user."""
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name, password_hash) "
        "VALUES($1, $2, 'h') RETURNING id::text",
        email,
        email.split("@")[0],
    )
    return await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


async def _register(pool, wid: str, name: str) -> bool:
    from backend.database import workspace_scope

    async with workspace_scope(pool, wid) as conn:
        return await register_promoted_profile(conn, wid, name)


async def _rows(admin_dsn: str, wid: str) -> list[tuple[str, bool]]:
    admin = await asyncpg.create_pool(admin_dsn, min_size=1, max_size=1)
    try:
        async with admin.acquire() as c:
            rows = await c.fetch(
                "SELECT profile_name, is_default FROM profiles "
                "WHERE workspace_id = $1::uuid ORDER BY profile_name",
                wid,
            )
    finally:
        await admin.close()
    return [(r["profile_name"], r["is_default"]) for r in rows]


def _run(clean_db, scenario):
    async def body():
        from backend.database import create_pool

        api = await create_pool(clean_db["api_dsn"], min_size=2, max_size=4)
        try:
            await scenario(api)
        finally:
            await api.close()

    asyncio.run(body())


def test_first_promote_in_a_fresh_workspace_is_the_default(clean_db):
    async def scenario(api):
        async with api.acquire() as c:
            wid = await _new_workspace(c, "first@example.com")
        assert await _register(api, wid, "riverbend-logistics") is True
        assert await _rows(clean_db["admin_dsn"], wid) == [("riverbend-logistics", True)]

    _run(clean_db, scenario)


def test_a_second_profile_is_registered_but_not_the_default(clean_db):
    async def scenario(api):
        async with api.acquire() as c:
            wid = await _new_workspace(c, "second@example.com")
        assert await _register(api, wid, "riverbend-logistics") is True
        assert await _register(api, wid, "lantern-interactive") is False
        assert await _rows(clean_db["admin_dsn"], wid) == [
            ("lantern-interactive", False),
            ("riverbend-logistics", True),
        ]

    _run(clean_db, scenario)


def test_the_same_name_twice_keeps_one_row(clean_db):
    async def scenario(api):
        async with api.acquire() as c:
            wid = await _new_workspace(c, "twice@example.com")
        assert await _register(api, wid, "riverbend-logistics") is True
        assert await _register(api, wid, "riverbend-logistics") is True
        assert await _rows(clean_db["admin_dsn"], wid) == [("riverbend-logistics", True)]

    _run(clean_db, scenario)


def test_concurrent_promotes_in_a_fresh_workspace_leave_exactly_one_default(clean_db):
    async def scenario(api):
        async with api.acquire() as c:
            wid = await _new_workspace(c, "race@example.com")
        results = await asyncio.gather(
            _register(api, wid, "riverbend-logistics"),
            _register(api, wid, "lantern-interactive"),
        )
        rows = await _rows(clean_db["admin_dsn"], wid)
        assert len(rows) == 2
        assert sum(is_default for _, is_default in rows) == 1
        # Each caller's answer matches what the database committed for its row.
        committed = dict(rows)
        assert results == [committed["riverbend-logistics"], committed["lantern-interactive"]]

    _run(clean_db, scenario)


def test_a_registration_is_invisible_from_another_workspace(clean_db):
    async def scenario(api):
        from backend.database import workspace_scope

        async with api.acquire() as c:
            wid_a = await _new_workspace(c, "tenant-a@example.com")
            wid_b = await _new_workspace(c, "tenant-b@example.com")
        assert await _register(api, wid_a, "riverbend-logistics") is True

        async with workspace_scope(api, wid_b) as conn:
            seen = await conn.fetch("SELECT profile_name FROM profiles")
        assert seen == []
        # B's own first promote is still its default — A's default does not count.
        assert await _register(api, wid_b, "lantern-interactive") is True

    _run(clean_db, scenario)
