"""Tests for POST /v1/api-keys/verify endpoint (used by Edge MCP Gateway)."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.api_keys import router

app = FastAPI()
app.include_router(router, prefix="/v1")


def _mock_pool(resolve_row=None, sub_row=None, wallet_row=None):
    pool = MagicMock()
    conn = MagicMock()

    async def _fetchrow(query, *args):
        if "resolve_api_key" in query:
            return resolve_row
        if "FROM subscriptions" in query:
            return sub_row
        if "FROM workspace_wallets" in query:
            return wallet_row
        return None

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.execute = AsyncMock()

    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


_WS_ID = "00000000-0000-0000-0000-000000000001"
_RESOLVE_ROW = {
    "key_id": "00000000-0000-0000-0000-000000000002",
    "workspace_id": _WS_ID,
    "entitlement": "pro",
}


def test_verify_missing_key_returns_401():
    client = TestClient(app)
    resp = client.post("/v1/api-keys/verify")
    assert resp.status_code == 401
    detail = resp.json().get("detail", resp.json())
    assert detail["code"] == "invalid_key"


def test_verify_malformed_key_returns_401():
    client = TestClient(app)
    resp = client.post(
        "/v1/api-keys/verify",
        headers={"Authorization": "Bearer not-a-valid-key"},
    )
    assert resp.status_code == 401
    detail = resp.json().get("detail", resp.json())
    assert detail["code"] == "invalid_key"


def test_verify_unknown_or_revoked_key_returns_401():
    pool = _mock_pool(resolve_row=None)
    app.state.pool = pool
    client = TestClient(app)

    resp = client.post(
        "/v1/api-keys/verify",
        headers={"Authorization": "Bearer sk-unknown-key"},
    )
    assert resp.status_code == 401
    detail = resp.json().get("detail", resp.json())
    assert detail["code"] == "invalid_key"


def test_verify_valid_key_header_returns_200():
    period_end = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)
    sub_row = {
        "status": "active",
        "current_period_end": period_end,
    }
    pool = _mock_pool(resolve_row=_RESOLVE_ROW, sub_row=sub_row)
    app.state.pool = pool
    client = TestClient(app)

    resp = client.post(
        "/v1/api-keys/verify",
        headers={"Authorization": "Bearer sk-valid-live-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["workspace_id"] == _WS_ID
    assert data["tier"] == "pro"
    assert data["active"] is True
    assert data["expires_at"] == period_end.isoformat()


def test_verify_valid_key_json_body_returns_200():
    pool = _mock_pool(resolve_row=_RESOLVE_ROW, sub_row=None)
    app.state.pool = pool
    client = TestClient(app)

    resp = client.post(
        "/v1/api-keys/verify",
        json={"api_key": "sk-valid-live-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["workspace_id"] == _WS_ID
    assert data["tier"] == "pro"
    assert data["active"] is True


def test_verify_inactive_subscription_returns_active_false():
    sub_row = {
        "status": "canceled",
        "current_period_end": None,
    }
    pool = _mock_pool(resolve_row=_RESOLVE_ROW, sub_row=sub_row)
    app.state.pool = pool
    client = TestClient(app)

    resp = client.post(
        "/v1/api-keys/verify",
        headers={"Authorization": "Bearer sk-valid-key-canceled-sub"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["active"] is False


def test_verify_free_tier_without_credits_returns_active_false_and_has_credits_false():
    resolve_row = {
        "key_id": "00000000-0000-0000-0000-000000000002",
        "workspace_id": _WS_ID,
        "entitlement": "free",
    }
    sub_row = {
        "status": "active",
        "entitlement": "free",
        "current_period_end": None,
    }
    wallet_row = {"balance_usd": 0.0}
    pool = _mock_pool(resolve_row=resolve_row, sub_row=sub_row, wallet_row=wallet_row)
    app.state.pool = pool
    client = TestClient(app)

    resp = client.post(
        "/v1/api-keys/verify",
        headers={"Authorization": "Bearer sk-valid-free-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["tier"] == "free"
    assert data["active"] is False
    assert data["has_credits"] is False
    assert data["balance_usd"] == 0.0


def test_verify_free_tier_with_credits_returns_active_false_and_has_credits_true():
    resolve_row = {
        "key_id": "00000000-0000-0000-0000-000000000002",
        "workspace_id": _WS_ID,
        "entitlement": "free",
    }
    sub_row = {
        "status": "active",
        "entitlement": "free",
        "current_period_end": None,
    }
    wallet_row = {"balance_usd": 15.0}
    pool = _mock_pool(resolve_row=resolve_row, sub_row=sub_row, wallet_row=wallet_row)
    app.state.pool = pool
    client = TestClient(app)

    resp = client.post(
        "/v1/api-keys/verify",
        headers={"Authorization": "Bearer sk-valid-free-key-with-credits"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["tier"] == "free"
    assert data["active"] is False
    assert data["has_credits"] is True
    assert data["balance_usd"] == 15.0
