"""Live-Postgres test: a pack node's state reaches ``run_nodes`` (client issue #337).

The runner stamps ``entry["started"]`` as an ISO-8601 string. asyncpg refuses a str for a
``timestamptz`` parameter, and the runner contains observer errors, so every backend pack
run silently persisted no ``run_nodes`` rows and no content blocks. The mocked tier cannot
see this — only a real driver encoding the parameter does. Skipped unless
GTM_TEST_PG_ADMIN_DSN is set (root conftest).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")


async def _register(conn, email: str) -> str:
    """Insert a user (fires the bootstrap trigger); return the workspace id."""
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name, password_hash) "
        "VALUES($1, $2, 'h') RETURNING id::text",
        email,
        email.split("@")[0],
    )
    return await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


def test_a_runner_entry_with_an_iso_string_start_is_persisted(clean_db):
    async def body():
        from backend.database import create_pool
        from backend.services.runs.persistence import _persist_node

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            async with admin.acquire() as c:
                wid = await _register(c, "a@example.com")
                run_id = await c.fetchval(
                    "INSERT INTO runs(workspace_id, profile_name, prompt) "
                    "VALUES($1::uuid,'p','x') RETURNING id::text",
                    wid,
                )

            # Exactly the shape agent/pipeline.py hands the observer.
            started = "2026-10-06T10:11:59.406493Z"
            state = await _persist_node(
                api, wid, run_id, "prospect", {"status": "running", "started": started}
            )
            assert state == "running"
            await _persist_node(api, wid, run_id, "prospect", {"status": "ok", "started": started})

            async with admin.acquire() as c:
                row = await c.fetchrow(
                    "SELECT state, started_at, finished_at FROM run_nodes "
                    "WHERE run_id = $1::uuid AND node_id = 'prospect'",
                    run_id,
                )
            assert row is not None, "the node row was never written"
            assert row["state"] == "completed"
            assert row["started_at"] == datetime(2026, 10, 6, 10, 11, 59, 406493, tzinfo=UTC)
            assert row["finished_at"] is not None
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())
