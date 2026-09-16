"""Live-Postgres regression: a partial publish-settings PUT preserves schedule fields.

The route's UPSERT used to ``SET schedule_enabled = EXCLUDED.schedule_enabled``
unconditionally. A caller that PUTs without the V020 fields — Pydantic then defaults them
to ``None`` — silently reset a previously-enabled workspace back to
``schedule_enabled=false`` with no error and no signal. Confirmed live against a real
Postgres before the fix: enable schedule -> PUT again with only the pre-V020 field shape ->
``schedule_enabled`` reverted to false in the row.

A mocked ``conn.execute`` (tests/backend/test_publish_settings.py's SDK-free style) cannot
prove the SQL text itself is correct — only a real Postgres executing the actual COALESCE
clause can, so this one test earns the live-DB tier and lives apart from that file's
mock-based suite (the backend-db CI job runs dbtest files directly rather than collecting
tests/, so a mixed mocked+dbtest file would either skip its dbtest silently or make the job
collect tests it was never meant to run).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

from backend.routers import publish_settings as ps  # noqa: E402
from backend.schemas import PublishSettingsSyncRequest  # noqa: E402


async def _register_for_publish_settings(conn, email: str) -> str:
    """Minimal signup, mirroring test_rls_live.py's ``_register`` — returns the new
    user's workspace id (the signup trigger provisions one workspace per user)."""
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name, password_hash) "
        "VALUES($1, $2, 'h') RETURNING id::text",
        email,
        email.split("@")[0],
    )
    return await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


def test_a_partial_put_preserves_the_workspaces_existing_schedule_settings(clean_db):
    async def body():
        from backend.database import create_pool

        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=2)
        try:
            async with api.acquire() as c:
                wid = await _register_for_publish_settings(c, "sched@example.com")

            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=api)))

            # 1) Enable + opt into scheduling with a 30-day horizon.
            resp1 = await ps.sync_publish_settings(
                wid,
                PublishSettingsSyncRequest(
                    enabled=True,
                    url="https://relay.example/publish",
                    secret_ref="PUBLISH_ACME",
                    schedule_enabled=True,
                    schedule_max_horizon_days=30,
                ),
                request,
                None,
            )
            assert resp1.applied is True

            # 2) A second call in the PRE-V020 shape — schedule fields omitted, so
            #    Pydantic defaults them to None (not False/90). Only `url` changes.
            resp2 = await ps.sync_publish_settings(
                wid,
                PublishSettingsSyncRequest(
                    enabled=True,
                    url="https://relay.example/publish-v2",
                    secret_ref="PUBLISH_ACME",
                ),
                request,
                None,
            )
            assert resp2.applied is True

            admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=1)
            try:
                async with admin.acquire() as c:
                    row = await c.fetchrow(
                        "SELECT url, schedule_enabled, schedule_max_horizon_days "
                        "FROM workspace_publish_settings WHERE workspace_id = $1::uuid",
                        wid,
                    )
            finally:
                await admin.close()

            # The field that WAS sent (url) changed; the fields that were OMITTED
            # (schedule_enabled/schedule_max_horizon_days) must still carry what
            # call 1 set — not silently reverted to false/90.
            assert row["url"] == "https://relay.example/publish-v2"
            assert row["schedule_enabled"] is True
            assert row["schedule_max_horizon_days"] == 30
        finally:
            await api.close()

    asyncio.run(body())
