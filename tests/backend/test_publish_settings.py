"""A5 — per-workspace publish destination: resolver fail-closed + service-only write.

Two security-critical properties:
  1. The write route is SERVICE-authed (BILLING_SYNC_SECRET), never a user JWT — a
     tenant able to set its own publish URL would be an arbitrary-egress hole.
  2. The dispatch resolver is fail-closed: no row / disabled / half-configured /
     non-namespaced secret_ref / unset secret all yield None → NO publish, and there
     is NO fallback to a shared/operator destination (that fallback was the A5 leak).

SDK-free / mock-based (no live DB); the live cross-tenant + FORCE-RLS path is covered
by tests/backend/test_rls_live.py (workspace_publish_settings is in _TENANT_TABLES).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import publish_dispatch as pd  # noqa: E402
from backend.routers import publish_settings as ps  # noqa: E402
from backend.schemas import PublishSettingsSyncRequest  # noqa: E402

_WS = "11111111-1111-4111-8111-111111111111"
_SECRET = "svc-secret-abc123"  # nosec B105 — throwaway test value


# ── resolver: fail-closed, no shared-account fallback ─────────────────────────


def _resolve_with(monkeypatch, row) -> object:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=row)

    @contextlib.asynccontextmanager
    async def _scope(pool, ws):
        yield conn

    monkeypatch.setattr("backend.database.workspace_scope", _scope)
    return asyncio.run(pd._resolve_workspace_publish_settings(MagicMock(), _WS))


def test_resolver_none_when_no_row(monkeypatch):
    assert _resolve_with(monkeypatch, None) is None


def test_resolver_none_when_disabled(monkeypatch):
    monkeypatch.setenv("PUBLISH_ACME", "s")
    row = {"enabled": False, "url": "https://relay.example/p", "secret_ref": "PUBLISH_ACME"}
    assert _resolve_with(monkeypatch, row) is None


def test_resolver_none_when_secret_env_unset(monkeypatch):
    monkeypatch.delenv("PUBLISH_ACME", raising=False)
    row = {"enabled": True, "url": "https://relay.example/p", "secret_ref": "PUBLISH_ACME"}
    assert _resolve_with(monkeypatch, row) is None


def test_resolver_none_when_secret_ref_not_namespaced(monkeypatch):
    # A non-PUBLISH_* ref (e.g. pointing at BACKEND_JWT_SECRET) is refused outright.
    monkeypatch.setenv("BACKEND_JWT_SECRET", "s")
    row = {"enabled": True, "url": "https://relay.example/p", "secret_ref": "BACKEND_JWT_SECRET"}
    assert _resolve_with(monkeypatch, row) is None


def test_resolver_happy_path(monkeypatch):
    monkeypatch.setenv("PUBLISH_ACME", "supersecret")
    row = {"enabled": True, "url": "https://relay.example/post", "secret_ref": "PUBLISH_ACME"}
    settings = _resolve_with(monkeypatch, row)
    assert settings is not None
    assert settings.url == "https://relay.example/post"
    assert settings.secret == "supersecret"
    assert settings.enabled is True


# ── write route: validation ───────────────────────────────────────────────────


def _put(body: PublishSettingsSyncRequest, ws: str = _WS):
    return asyncio.run(ps.sync_publish_settings(ws, body, MagicMock(), None))


def test_bad_workspace_id_422():
    with pytest.raises(HTTPException) as exc:
        _put(PublishSettingsSyncRequest(enabled=False), ws="not-a-uuid")
    assert exc.value.status_code == 422


def test_enabled_requires_url_and_secret_ref():
    with pytest.raises(HTTPException) as exc:
        _put(PublishSettingsSyncRequest(enabled=True, url=None, secret_ref="PUBLISH_ACME"))
    assert exc.value.status_code == 422


def test_enabled_rejects_non_https():
    with pytest.raises(HTTPException) as exc:
        _put(
            PublishSettingsSyncRequest(
                enabled=True, url="http://relay.example/p", secret_ref="PUBLISH_ACME"
            )
        )
    assert exc.value.status_code == 422


def test_enabled_rejects_non_namespaced_secret_ref():
    with pytest.raises(HTTPException) as exc:
        _put(
            PublishSettingsSyncRequest(
                enabled=True, url="https://relay.example/p", secret_ref="BACKEND_JWT_SECRET"
            )
        )
    assert exc.value.status_code == 422


def _put_with_fake_pool(monkeypatch, body: PublishSettingsSyncRequest):
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    @contextlib.asynccontextmanager
    async def _scope(pool, ws):
        yield conn

    monkeypatch.setattr(ps, "workspace_scope", _scope)
    req = MagicMock()
    req.app.state.pool = MagicMock()
    return asyncio.run(ps.sync_publish_settings(_WS, body, req, None))


def test_valid_enable_upserts(monkeypatch):
    resp = _put_with_fake_pool(
        monkeypatch,
        PublishSettingsSyncRequest(
            enabled=True, url="https://relay.example/post", secret_ref="PUBLISH_ACME"
        ),
    )
    assert resp.applied is True and resp.outcome == "set"


def test_disable_clears(monkeypatch):
    resp = _put_with_fake_pool(monkeypatch, PublishSettingsSyncRequest(enabled=False))
    assert resp.applied is True and resp.outcome == "cleared"


# ── service-auth wiring: a user JWT (or no auth) cannot set a destination ──────


def test_route_rejects_unauthenticated(monkeypatch):
    """End-to-end wiring: the route depends on require_service_auth, so a call with no
    service secret is 401 before the pool is ever touched (no self-set egress target)."""
    monkeypatch.setenv("BILLING_SYNC_SECRET", _SECRET)
    app = FastAPI()
    app.include_router(ps.router, prefix="/v1")
    app.state.pool = MagicMock()
    with TestClient(app) as client:
        # no Authorization header → 401
        assert client.put(f"/v1/publish-settings/{_WS}", json={"enabled": False}).status_code == 401
        # a user-JWT-shaped bearer token is not the service secret → 401
        jwt_like = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.sig"
        r = client.put(
            f"/v1/publish-settings/{_WS}",
            json={"enabled": False},
            headers={"Authorization": jwt_like},
        )
        assert r.status_code == 401


# ── live-DB: schedule fields merge, they don't blind-overwrite ────────────────
# The gap this closes: the route's UPSERT used to ``SET schedule_enabled =
# EXCLUDED.schedule_enabled`` unconditionally. A caller that PUTs without the
# V020 fields — Pydantic then defaults them — silently reset a previously-enabled
# workspace back to schedule_enabled=false with no error and no signal. Confirmed
# live against a real Postgres before this fix: enable schedule → PUT again with
# only the pre-V020 field shape → schedule_enabled reverted to false in the row.
#
# A mocked ``conn.execute`` (the rest of this file's style) cannot prove the SQL
# text itself is correct — only a real Postgres executing the actual COALESCE
# clause can, so this one test earns the live-DB tier.


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


@pytest.mark.dbtest
def test_a_partial_put_preserves_the_workspaces_existing_schedule_settings(clean_db):
    asyncpg = pytest.importorskip("asyncpg")

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
