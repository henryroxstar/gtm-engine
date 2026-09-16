"""Every 401 a client meets carries a machine code it can branch on (M-01).

The app's 401 interceptor keys on ``error.code``: refresh once on ``token_expired``, sign out on
``token_revoked`` / ``refresh_token_expired`` / ``token_invalid``, and surface
``invalid_credentials`` to the user. Bearer failures also carry an RFC 6750 challenge; a body
credential (login, refresh, a service secret) does not. All apps here are enveloped exactly as
``backend/main.py`` wires them. No DB — failure paths refuse before any query.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")
os.environ.setdefault("BACKEND_JWT_EXPIRE_MINUTES", "60")
os.environ.setdefault("BACKEND_REFRESH_EXPIRE_DAYS", "30")

from backend.auth import create_access_token, create_refresh_token  # noqa: E402
from backend.deps import WorkspaceCtx, require_auth, require_service_auth  # noqa: E402
from backend.errors import register_error_handlers  # noqa: E402
from backend.routers import auth as auth_router  # noqa: E402

WS_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
SERVICE_SECRET = "service-secret-for-unit-tests-only"

CHALLENGE_MISSING = "Bearer"
CHALLENGE_INVALID = 'Bearer error="invalid_token"'
CHALLENGE_EXPIRED = 'Bearer error="invalid_token", error_description="token expired"'


def _signed(claims: dict, *, secret: str | None = None) -> str:
    now = int(time.time())
    body = {"sub": USER_ID, "workspace_id": WS_ID, "type": "access", "iat": now, "exp": now + 600}
    body.update(claims)
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, secret or os.environ["BACKEND_JWT_SECRET"], algorithm="HS256")


def _expired(kind: str, *, secret: str | None = None) -> str:
    now = int(time.time())
    return _signed({"type": kind, "iat": now - 7200, "exp": now - 3600}, secret=secret)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("BILLING_SYNC_SECRET", SERVICE_SECRET)
    conn = AsyncMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    app = FastAPI()

    @app.get("/probe")
    async def probe(ws: Annotated[WorkspaceCtx, Depends(require_auth)]) -> dict:
        return {"ok": True}

    @app.put("/service")
    async def service(_: Annotated[None, Depends(require_service_auth)]) -> dict:
        return {"ok": True}

    app.include_router(auth_router.router, prefix="/v1")
    app.state.pool = MagicMock(acquire=_acquire)
    register_error_handlers(app)
    with TestClient(app) as c:
        yield c, conn


def _code(resp) -> str:
    return resp.json()["error"]["code"]


# ── require_auth: bearer failures ─────────────────────────────────────────────


def test_missing_bearer_is_token_missing_with_a_bare_challenge(client):
    c, _ = client
    for headers in ({}, {"Authorization": "Basic abc"}):
        resp = c.get("/probe", headers=headers)
        assert (resp.status_code, _code(resp)) == (401, "token_missing"), headers
        assert resp.headers["WWW-Authenticate"] == CHALLENGE_MISSING


def test_expired_access_token_is_token_expired(client):
    c, _ = client
    resp = c.get("/probe", headers={"Authorization": f"Bearer {_expired('access')}"})
    assert (resp.status_code, _code(resp)) == (401, "token_expired")
    assert resp.headers["WWW-Authenticate"] == CHALLENGE_EXPIRED
    assert resp.json()["error"]["message"] == "Access token expired"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("garbage", id="malformed"),
        pytest.param(_signed({}, secret="attacker-secret-attacker-secret-00"), id="forged"),
        pytest.param(
            _expired("access", secret="attacker-secret-attacker-secret-00"), id="forged-expired"
        ),
        pytest.param(create_refresh_token(USER_ID, WS_ID), id="refresh-on-user-route"),
        pytest.param(_signed({"workspace_id": None}), id="no-workspace-claim"),
        pytest.param(_signed({"sub": None}), id="no-subject-claim"),
    ],
)
def test_every_other_bearer_failure_is_token_invalid(client, token):
    c, _ = client
    resp = c.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert (resp.status_code, _code(resp)) == (401, "token_invalid")
    assert resp.headers["WWW-Authenticate"] == CHALLENGE_INVALID


def test_bearer_failures_never_echo_the_library_message(client):
    c, _ = client
    for token in ("garbage", _expired("access"), create_refresh_token(USER_ID, WS_ID)):
        resp = c.get("/probe", headers={"Authorization": f"Bearer {token}"})
        text = resp.text.lower()
        for leak in ("segments", "signature has expired", "expected token type", "refresh"):
            assert leak not in text, (leak, resp.text)


# ── the service-secret route ──────────────────────────────────────────────────


def test_service_route_refuses_a_user_jwt_with_its_own_code_and_no_challenge(client):
    c, _ = client
    user_jwt = f"Bearer {create_access_token(USER_ID, WS_ID)}"
    for header in ({"Authorization": user_jwt}, {"Authorization": "wrong"}, {}):
        resp = c.put("/service", headers=header)
        assert (resp.status_code, _code(resp)) == (401, "service_auth_invalid"), header
        assert "WWW-Authenticate" not in resp.headers
    assert c.put("/service", headers={"Authorization": SERVICE_SECRET}).status_code == 200


# ── login + refresh: body credentials, no challenge ───────────────────────────


def test_login_wrong_credentials_keeps_invalid_credentials(client):
    c, conn = client
    conn.fetchrow.return_value = None
    resp = c.post("/v1/auth/login", json={"email": "dana@example.com", "password": "hunter2-x"})
    assert (resp.status_code, _code(resp)) == (401, "invalid_credentials")
    assert "WWW-Authenticate" not in resp.headers


def _refresh(c, token: str):
    return c.post("/v1/auth/refresh", json={"refresh_token": token})


def test_expired_refresh_token_is_refresh_token_expired(client):
    c, _ = client
    resp = _refresh(c, _expired("refresh"))
    assert (resp.status_code, _code(resp)) == (401, "refresh_token_expired")
    assert "WWW-Authenticate" not in resp.headers


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("garbage", id="malformed"),
        pytest.param(create_access_token(USER_ID, WS_ID), id="access-token-as-refresh"),
        pytest.param(_expired("refresh", secret="attacker-secret-attacker-secret-00"), id="forged"),
    ],
)
def test_other_refresh_token_failures_are_token_invalid(client, token):
    c, _ = client
    resp = _refresh(c, token)
    assert (resp.status_code, _code(resp)) == (401, "token_invalid")
    assert "WWW-Authenticate" not in resp.headers
    assert "segments" not in resp.text.lower()


def test_refresh_after_password_change_is_token_revoked(client):
    from datetime import UTC, datetime, timedelta

    c, conn = client
    conn.fetchrow.return_value = {"password_changed_at": datetime.now(UTC) + timedelta(hours=1)}
    resp = _refresh(c, create_refresh_token(USER_ID, WS_ID))
    assert (resp.status_code, _code(resp)) == (401, "token_revoked")
    assert "WWW-Authenticate" not in resp.headers


def test_refresh_for_a_deleted_user_is_token_revoked(client):
    c, conn = client
    conn.fetchrow.return_value = None
    resp = _refresh(c, create_refresh_token(USER_ID, WS_ID))
    assert (resp.status_code, _code(resp)) == (401, "token_revoked")


# ── the challenge has to be readable by a browser client ──────────────────────


def test_cors_exposes_the_bearer_challenge(monkeypatch):
    """A cross-origin XHR can only read WWW-Authenticate if CORS exposes it — without that a
    browser client is back to guessing which 401 it got."""
    from backend.main import create_app

    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    app = create_app()
    resp = TestClient(app).get(  # no `with`: the app's lifespan (DB, workers) never starts
        "/v1/workspace", headers={"Origin": "https://app.example.com"}
    )
    assert (resp.status_code, _code(resp)) == (401, "token_missing")
    assert resp.headers["WWW-Authenticate"] == CHALLENGE_MISSING
    exposed = {h.strip() for h in resp.headers["access-control-expose-headers"].split(",")}
    assert "WWW-Authenticate" in exposed
    assert resp.headers["access-control-allow-origin"] == "https://app.example.com"
