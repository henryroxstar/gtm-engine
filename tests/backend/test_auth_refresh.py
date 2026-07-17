"""Regression tests for POST /auth/refresh token invalidation (Strix VULN-0003).

A refresh token issued before the user's last password change must be rejected, so a
stolen token cannot outlive a password reset. Also covers the deleted-user case (the
row lookup returns None → 401). The pool is mocked — no live Postgres required, so these
run in every CI tier alongside the other backend-surface unit tests.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")
os.environ.setdefault("BACKEND_JWT_EXPIRE_MINUTES", "60")
os.environ.setdefault("BACKEND_REFRESH_EXPIRE_DAYS", "30")

from backend.auth import create_refresh_token, decode_token
from backend.routers import auth as auth_router

WS_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"


def _client(row) -> TestClient:
    """Build a TestClient whose pool.acquire() yields a conn returning `row`."""
    conn = AsyncMock()
    conn.fetchrow.return_value = row

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/v1")
    app.state.pool = pool
    return TestClient(app)


def test_refresh_succeeds_when_never_changed():
    # password_changed_at is NULL → token stays valid.
    client = _client({"password_changed_at": None})
    token = create_refresh_token(USER_ID, WS_ID)
    resp = client.post("/v1/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_refresh_rejected_after_password_change():
    # password changed AFTER the token was minted → 401.
    token = create_refresh_token(USER_ID, WS_ID)
    iat = decode_token(token, expected_type="refresh")["iat"]
    changed = datetime.fromtimestamp(iat, UTC) + timedelta(hours=1)
    client = _client({"password_changed_at": changed})
    resp = client.post("/v1/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401


def test_refresh_valid_when_changed_before_issue():
    # password changed BEFORE the token was minted → still valid.
    token = create_refresh_token(USER_ID, WS_ID)
    iat = decode_token(token, expected_type="refresh")["iat"]
    changed = datetime.fromtimestamp(iat, UTC) - timedelta(hours=1)
    client = _client({"password_changed_at": changed})
    resp = client.post("/v1/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 200


def test_refresh_rejected_for_deleted_user():
    # user row gone (deleted account) → 401.
    client = _client(None)
    token = create_refresh_token(USER_ID, WS_ID)
    resp = client.post("/v1/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401
