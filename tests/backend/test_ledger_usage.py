"""Tests for GET /v1/ledger/usage endpoint (spend-vs-cap and wallet balance)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.deps import WorkspaceCtx, require_auth
from backend.routers.ledger import router
from gtm_core.capabilities import Entitlement

app = FastAPI()
app.include_router(router, prefix="/v1")

_WS_ID = "00000000-0000-0000-0000-000000000001"


def _mock_workspace_ctx() -> WorkspaceCtx:
    return WorkspaceCtx(
        user_id="00000000-0000-0000-0000-000000000002",
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
    )


app.dependency_overrides[require_auth] = _mock_workspace_ctx


def _mock_pool(sub_row=None, wallet_row=None):
    pool = MagicMock()
    conn = MagicMock()

    async def _fetchrow(query, *args):
        if "FROM subscriptions" in query:
            return sub_row
        if "FROM workspace_wallets" in query:
            return wallet_row
        return None

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock()

    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def test_get_usage_without_wallet():
    sub_row = {"spent": 12.50, "cap": 100.0}
    pool = _mock_pool(sub_row=sub_row, wallet_row=None)
    app.state.pool = pool

    client = TestClient(app)
    resp = client.get("/v1/ledger/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period"] == "current_month"
    assert data["spent_usd"] == 12.50
    assert data["cap_usd"] == 100.0
    assert data["over_cap"] is False
    assert data["balance_usd"] is None
    assert data["balance_credits"] is None
    assert data["has_credits"] is False


def test_get_usage_with_funded_wallet():
    sub_row = {"spent": 5.0, "cap": 50.0}
    wallet_row = {"balance_usd": 25.0, "balance_credits": 25000.0}
    pool = _mock_pool(sub_row=sub_row, wallet_row=wallet_row)
    app.state.pool = pool

    client = TestClient(app)
    resp = client.get("/v1/ledger/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["spent_usd"] == 5.0
    assert data["cap_usd"] == 50.0
    assert data["over_cap"] is False
    assert data["balance_usd"] == 25.0
    assert data["balance_credits"] == 25000.0
    assert data["has_credits"] is True


def test_get_usage_with_zero_balance_wallet():
    sub_row = {"spent": 0.0, "cap": 50.0}
    wallet_row = {"balance_usd": 0.0, "balance_credits": 0.0}
    pool = _mock_pool(sub_row=sub_row, wallet_row=wallet_row)
    app.state.pool = pool

    client = TestClient(app)
    resp = client.get("/v1/ledger/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["balance_usd"] == 0.0
    assert data["balance_credits"] == 0.0
    assert data["has_credits"] is False


def test_get_usage_with_over_cap():
    sub_row = {"spent": 55.0, "cap": 50.0}
    wallet_row = {"balance_usd": 10.0, "balance_credits": 10000.0}
    pool = _mock_pool(sub_row=sub_row, wallet_row=wallet_row)
    app.state.pool = pool

    client = TestClient(app)
    resp = client.get("/v1/ledger/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["spent_usd"] == 55.0
    assert data["cap_usd"] == 50.0
    assert data["over_cap"] is True
    assert data["balance_usd"] == 10.0
    assert data["balance_credits"] == 10000.0
    assert data["has_credits"] is True
