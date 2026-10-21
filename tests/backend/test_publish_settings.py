"""A5 — per-workspace publish destination: resolver fail-closed + service-only write.

Two security-critical properties:
  1. The write route is SERVICE-authed (BILLING_SYNC_SECRET), never a user JWT — a
     tenant able to set its own publish URL would be an arbitrary-egress hole.
  2. The dispatch resolver is fail-closed: no row / disabled / half-configured /
     non-namespaced secret_ref / unset secret all yield None → NO publish, and there
     is NO fallback to a shared/operator destination (that fallback was the A5 leak).

SDK-free / mock-based (no live DB); the live cross-tenant + FORCE-RLS path is covered
by tests/backend/test_rls_live.py (workspace_publish_settings is in _TENANT_TABLES), and
the schedule-merge UPSERT regression — which needs a real Postgres to prove the actual SQL
COALESCE clause, not a mock — lives in tests/backend/test_publish_settings_live.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
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
