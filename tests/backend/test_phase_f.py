"""Unit tests for Phase F backend surface.

Tests for:
  - GET /v1/workspace/subscriptions/me
  - POST /v1/push-tokens
  - DELETE /v1/push-tokens/{token}
  - send_gate_push no-op when PUSH_PROVIDER unset

All DB and pool calls are mocked — no live Postgres required.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")
os.environ.setdefault("BACKEND_JWT_EXPIRE_MINUTES", "60")
os.environ.setdefault("BACKEND_REFRESH_EXPIRE_DAYS", "30")

from backend.auth import create_access_token
from backend.routers import push_tokens as push_tokens_router
from backend.routers import workspaces as workspaces_router

WS_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"


def _access_token() -> str:
    return create_access_token(USER_ID, WS_ID)


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token()}"}


# ── shared app factory ─────────────────────────────────────────────────────────


def _make_app(*routers) -> FastAPI:
    app = FastAPI()
    for rtr in routers:
        app.include_router(rtr.router, prefix="/v1")
    app.state.pool = MagicMock()
    return app


# ── GET /v1/workspace/subscriptions/me ────────────────────────────────────────


class TestSubscriptionMe:
    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        c.fetchrow.return_value = {
            "entitlement": "pro",
            "status": "active",
            "monthly_cost_cap_usd": 50.0,
            "revenuecat_subscriber_id": "rc-sub-abc",
        }
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(workspaces_router)

        # Also need require_auth to resolve; patch workspace_scope + subscriptions query
        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.workspaces.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_returns_entitlement(self, client):
        resp = client.get("/v1/workspace/subscriptions/me", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert body["entitlement"] == "pro"
        assert body["status"] == "active"
        assert body["revenuecat_subscriber_id"] == "rc-sub-abc"

    def test_requires_auth(self, client):
        resp = client.get("/v1/workspace/subscriptions/me")
        assert resp.status_code == 401

    def test_not_found_when_no_subscription(self, conn, client):
        conn.fetchrow.return_value = None
        resp = client.get("/v1/workspace/subscriptions/me", headers=_auth_header())
        assert resp.status_code == 404


# ── POST /v1/push-tokens ──────────────────────────────────────────────────────


class TestRegisterToken:
    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        c.execute.return_value = "INSERT 0 1"
        # require_auth fetches entitlement
        c.fetchrow.return_value = {"entitlement": "free"}
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(push_tokens_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.push_tokens.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_register_fcm_token(self, client):
        resp = client.post(
            "/v1/push-tokens",
            json={"token": "fcm-device-token-abc", "platform": "fcm"},
            headers=_auth_header(),
        )
        assert resp.status_code == 201
        assert resp.json()["platform"] == "fcm"

    def test_register_apns_token(self, client):
        resp = client.post(
            "/v1/push-tokens",
            json={"token": "apns-device-token-xyz", "platform": "apns"},
            headers=_auth_header(),
        )
        assert resp.status_code == 201
        assert resp.json()["platform"] == "apns"

    def test_invalid_platform_rejected(self, client):
        resp = client.post(
            "/v1/push-tokens",
            json={"token": "tok", "platform": "web"},
            headers=_auth_header(),
        )
        assert resp.status_code == 422

    def test_requires_auth(self, client):
        resp = client.post("/v1/push-tokens", json={"token": "x", "platform": "fcm"})
        assert resp.status_code == 401


# ── DELETE /v1/push-tokens/{token} ────────────────────────────────────────────


class TestDeregisterToken:
    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        c.execute.return_value = "DELETE 1"
        c.fetchrow.return_value = {"entitlement": "free"}
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(push_tokens_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.push_tokens.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_deregister_existing_token(self, client):
        resp = client.delete("/v1/push-tokens/fcm-device-token-abc", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json()["status"] == "deregistered"

    def test_deregister_missing_token_returns_404(self, conn, client):
        conn.execute.return_value = "DELETE 0"
        resp = client.delete("/v1/push-tokens/nonexistent-token", headers=_auth_header())
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.delete("/v1/push-tokens/some-token")
        assert resp.status_code == 401


# ── send_gate_push no-op ──────────────────────────────────────────────────────


def test_send_gate_push_noop_when_no_provider():
    """send_gate_push returns 0 and makes no HTTP calls when PUSH_PROVIDER is unset."""
    import asyncio

    conn = AsyncMock()
    conn.fetch.return_value = [{"token": "fcm-tok-1", "platform": "fcm"}]
    pool = MagicMock()

    @asynccontextmanager
    async def _scope(p, wid):
        yield conn

    with (
        patch("backend.push.workspace_scope", _scope),
        patch.dict(os.environ, {"PUSH_PROVIDER": ""}, clear=False),
    ):
        from backend import push as push_mod

        with patch.object(push_mod, "_PROVIDER", ""):
            count = asyncio.run(push_mod.send_gate_push(pool, WS_ID, "run-id-123", "⟦GATE:plan⟧"))

    assert count == 0


def test_send_gate_push_noop_when_no_tokens():
    """send_gate_push returns 0 when no tokens are registered."""
    import asyncio

    conn = AsyncMock()
    conn.fetch.return_value = []
    pool = MagicMock()

    @asynccontextmanager
    async def _scope(p, wid):
        yield conn

    with patch("backend.push.workspace_scope", _scope):
        from backend import push as push_mod

        count = asyncio.run(push_mod.send_gate_push(pool, WS_ID, "run-id-456", "⟦GATE:publish⟧"))

    assert count == 0
