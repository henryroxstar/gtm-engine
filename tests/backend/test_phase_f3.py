"""Unit tests for Phase F.3 backend surface.

Tests for:
  - GET /v1/push-tokens  — list registered device tokens
  - GET /v1/account/export — GDPR data export

All DB and pool calls are mocked — no live Postgres required.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")
os.environ.setdefault("BACKEND_JWT_EXPIRE_MINUTES", "60")
os.environ.setdefault("BACKEND_REFRESH_EXPIRE_DAYS", "30")

from backend.auth import create_access_token
from backend.routers import account as account_router
from backend.routers import push_tokens as push_tokens_router

WS_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _access_token() -> str:
    return create_access_token(USER_ID, WS_ID)


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token()}"}


def _make_app(*routers) -> FastAPI:
    app = FastAPI()
    for rtr in routers:
        app.include_router(rtr.router, prefix="/v1")
    app.state.pool = MagicMock()
    return app


# ── GET /v1/push-tokens ───────────────────────────────────────────────────────


class TestListTokens:
    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        c.fetchrow.return_value = {"entitlement": "free"}
        c.fetch.return_value = [
            {
                "id": "00000000-0000-0000-0000-000000000030",
                "token": "fcm-device-token-abc",
                "platform": "fcm",
                "created_at": _TS,
            },
            {
                "id": "00000000-0000-0000-0000-000000000031",
                "token": "apns-device-token-xyz",
                "platform": "apns",
                "created_at": _TS,
            },
        ]
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

    def test_lists_registered_tokens(self, client):
        resp = client.get("/v1/push-tokens", headers=_auth_header())
        assert resp.status_code == 200
        tokens = resp.json()
        assert len(tokens) == 2
        assert tokens[0]["token"] == "fcm-device-token-abc"
        assert tokens[0]["platform"] == "fcm"
        assert "created_at" in tokens[0]

    def test_empty_list_when_no_tokens(self, conn, client):
        conn.fetch.return_value = []
        resp = client.get("/v1/push-tokens", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_requires_auth(self, client):
        resp = client.get("/v1/push-tokens")
        assert resp.status_code == 401


# ── GET /v1/account/export ────────────────────────────────────────────────────


class TestExportAccount:
    @pytest.fixture()
    def pool(self):
        p = MagicMock()
        user_conn = AsyncMock()
        user_conn.fetchrow.return_value = {
            "id": USER_ID,
            "email": "test@example.com",
            "display_name": "Test User",
            "created_at": _TS,
            "last_seen_at": None,
        }
        p.acquire.return_value.__aenter__ = AsyncMock(return_value=user_conn)
        p.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return p

    @pytest.fixture()
    def ws_conn(self):
        c = AsyncMock()
        # require_auth entitlement lookup
        c.fetchrow.side_effect = [
            {"entitlement": "pro"},  # require_auth
            {  # workspace row
                "id": WS_ID,
                "slug": "test-workspace",
                "display_name": "Test Workspace",
                "created_at": _TS,
            },
            {  # subscription row
                "entitlement": "pro",
                "status": "active",
                "monthly_cost_cap_usd": 50.0,
                "created_at": _TS,
            },
        ]
        c.fetch.side_effect = [
            # profiles
            [{"profile_name": "default", "is_default": True, "created_at": _TS}],
            # runs
            [
                {
                    "id": "00000000-0000-0000-0000-000000000050",
                    "profile_name": "default",
                    "prompt": "run market-scan",
                    "status": "ok",
                    "created_at": _TS,
                    "completed_at": _TS,
                }
            ],
            # api_keys
            [],
        ]
        return c

    @pytest.fixture()
    def client(self, pool, ws_conn):
        app = _make_app(account_router)
        app.state.pool = pool
        app.state.cfg = MagicMock(repo_root=Path("/nonexistent-gtm-test-root"))

        @asynccontextmanager
        async def _scope(p, workspace_id):
            yield ws_conn

        with (
            patch("backend.routers.account.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_export_returns_all_sections(self, client):
        resp = client.get("/v1/account/export", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert body["user"]["email"] == "test@example.com"
        assert body["workspace"]["slug"] == "test-workspace"
        assert body["subscription"]["entitlement"] == "pro"
        assert len(body["profiles"]) == 1
        assert body["profiles"][0]["profile_name"] == "default"
        assert len(body["runs"]) == 1
        assert body["api_keys"] == []

    def test_no_password_hash_in_export(self, client):
        resp = client.get("/v1/account/export", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert "password_hash" not in body["user"]
        assert "password" not in body["user"]

    def test_requires_auth(self, client):
        resp = client.get("/v1/account/export")
        assert resp.status_code == 401
