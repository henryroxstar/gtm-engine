"""Unit and integration tests for Developer MCP Prepaid Wallets & Top-Up Integration.

Verifies:
1. Metered tool calls deduct cost_usd from workspace_wallets.
2. check_budget refuses calls when wallet balance is zero or negative with 402 Insufficient credits.
3. check_budget allows calls when wallet balance is positive.
4. FastMCP draft_post and draft_outreach surface 402 Payment Required on insufficient credits.
5. RevenueCat webhook handler idempotently credits wallet balance on NON_RENEWING_PURCHASE events.
6. POST /v1/api-keys/verify reflects workspace wallet credit state (has_credits, balance_usd).
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.errors import register_error_handlers
from backend.routers import api_keys, webhooks
from mcp_server.meter import InsufficientCreditsError, check_budget, meter_call
from mcp_server.server import draft_outreach, draft_post

_WS_ID = "33333333-3333-4333-8333-333333333333"
_KEY_ID = "44444444-4444-4444-8444-444444444444"
_RAW_KEY = "sk-test-developer-mcp-key-12345"
_KEY_HASH = hashlib.sha256(_RAW_KEY.encode()).hexdigest()
_WEBHOOK_SECRET = "rc_webhook_secret_for_tests"


def _run(coro):
    return asyncio.run(coro)


# ── Pool Mock Helper ──────────────────────────────────────────────────────────


def _make_mock_pool(
    *,
    wallet_row: dict[str, Any] | None = None,
    sub_row: dict[str, Any] | None = None,
    resolve_row: dict[str, Any] | None = None,
    tx_exists: bool = False,
):
    conn = MagicMock()
    executed_queries: list[tuple[str, tuple]] = []

    async def _execute(query, *args):
        executed_queries.append((query, args))
        return "UPDATE 1"

    async def _fetchrow(query, *args):
        executed_queries.append((query, args))
        if "workspace_wallets" in query:
            return wallet_row
        if "subscriptions" in query:
            return sub_row
        if "resolve_api_key" in query:
            return resolve_row
        if "wallet_transactions" in query:
            if "SELECT" in query:
                return {"id": "existing-tx-id"} if tx_exists else None
            if "INSERT" in query:
                return None if tx_exists else {"id": "new-tx-id"}
        return None

    async def _fetchval(query, *args):
        executed_queries.append((query, args))
        if "set_config" in query:
            return _WS_ID
        return None

    conn.execute = AsyncMock(side_effect=_execute)
    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.fetchval = AsyncMock(side_effect=_fetchval)
    conn.fetch = AsyncMock(return_value=[])

    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.conn = conn
    pool.executed_queries = executed_queries
    return pool


# ── 1. Wallet Balance Deduction on Metered Calls ──────────────────────────────


def test_meter_call_deducts_wallet_balance():
    """Verify meter_call issues an atomic deduction on workspace_wallets."""
    pool = _make_mock_pool()

    _run(
        meter_call(
            workspace_id=_WS_ID,
            api_key_id=_KEY_ID,
            tool_name="draft_post",
            profile_name="acme-test",
            model="deepseek-v4-flash",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.015000,
            pool=pool,
        )
    )

    update_queries = [
        (q, args) for q, args in pool.executed_queries if "UPDATE workspace_wallets" in q
    ]
    assert len(update_queries) == 1
    query, args = update_queries[0]
    assert "balance_usd = GREATEST(0.0, balance_usd - $1)" in query
    assert args[0] == 0.015000
    assert args[1] == _WS_ID


# ── 2. Refusal When Wallet Balance is Zero or Negative ────────────────────────


def test_check_budget_refuses_zero_wallet_balance():
    """check_budget raises InsufficientCreditsError when balance is 0.00."""
    wallet_row = {"balance_usd": 0.000000}
    pool = _make_mock_pool(wallet_row=wallet_row)

    with pytest.raises(
        InsufficientCreditsError, match=r"402 Payment Required: Insufficient MCP credits"
    ):
        _run(check_budget(_WS_ID, pool))


def test_check_budget_refuses_negative_wallet_balance():
    """check_budget raises InsufficientCreditsError when balance is negative."""
    wallet_row = {"balance_usd": -1.500000}
    pool = _make_mock_pool(wallet_row=wallet_row)

    with pytest.raises(
        InsufficientCreditsError, match=r"402 Payment Required: Insufficient MCP credits"
    ):
        _run(check_budget(_WS_ID, pool))


def test_check_budget_refuses_when_no_wallet_and_no_subscription():
    """check_budget raises InsufficientCreditsError when neither wallet nor active subscription exists."""
    pool = _make_mock_pool(wallet_row=None, sub_row=None)

    with pytest.raises(
        InsufficientCreditsError, match=r"402 Payment Required: Insufficient MCP credits"
    ):
        _run(check_budget(_WS_ID, pool))


# ── 3. Positive Wallet Balance Allows Execution ───────────────────────────────


def test_check_budget_allows_positive_wallet():
    """check_budget returns True when wallet balance > 0."""
    wallet_row = {"balance_usd": 10.000000}
    pool = _make_mock_pool(wallet_row=wallet_row)

    allowed = _run(check_budget(_WS_ID, pool))
    assert allowed is True


# ── 4. Server Draft Tools Surface 402 Error Message ───────────────────────────


def test_server_draft_tools_handle_insufficient_credits(monkeypatch):
    """draft_post and draft_outreach raise clear 402 error message on empty wallet."""
    ctx = MagicMock()
    ctx.request_context.request.headers.get.return_value = f"Bearer {_RAW_KEY}"

    wallet_row = {"balance_usd": 0.000000}
    resolve_row = {
        "key_id": _KEY_ID,
        "workspace_id": _WS_ID,
        "entitlement": "pro",
    }
    pool = _make_mock_pool(wallet_row=wallet_row, resolve_row=resolve_row)

    from mcp_server import server

    server._state.pool = pool

    # Mock _require_profile to avoid profile folder filesystem requirement
    from gtm_core.capabilities import Entitlement
    from mcp_server.auth import ApiKeyCtx

    mock_ctx = ApiKeyCtx(
        key_id=_KEY_ID,
        workspace_id=_WS_ID,
        entitlement=Entitlement.PRO,
    )
    monkeypatch.setattr(server, "_require_profile", AsyncMock(return_value=mock_ctx))

    with pytest.raises(ValueError, match=r"402 Payment Required: Insufficient MCP credits"):
        _run(draft_post(brief="Sample brief", profile="test", ctx=ctx))

    with pytest.raises(ValueError, match=r"402 Payment Required: Insufficient MCP credits"):
        _run(draft_outreach(brief="Sample brief", profile="test", ctx=ctx))


# ── 5. RevenueCat Webhook Top-Up Flow & Idempotency ───────────────────────────


@pytest.fixture
def webhook_client(monkeypatch):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(webhooks.router, prefix="/v1")
    return app


def test_revenuecat_webhook_top_up_success(webhook_client):
    """NON_RENEWING_PURCHASE top-up event credits wallet and records transaction."""
    pool = _make_mock_pool(tx_exists=False)
    webhook_client.state.pool = pool
    client = TestClient(webhook_client)

    payload = {
        "api_version": "1.0",
        "event": {
            "id": "rc_evt_1001",
            "type": "NON_RENEWING_PURCHASE",
            "app_user_id": _WS_ID,
            "product_id": "credits_25_usd",
            "price_in_purchased_currency": 25.00,
        },
    }

    resp = client.post(
        "/v1/webhooks/revenuecat",
        headers={"Authorization": f"Bearer {_WEBHOOK_SECRET}"},
        json=payload,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["applied"] is True
    assert data["workspace_id"] == _WS_ID
    assert data["amount_usd"] == 25.00

    # Verify SQL inserts
    inserts = [q for q, _ in pool.executed_queries if "INSERT INTO" in q]
    assert any("wallet_transactions" in q for q in inserts)
    assert any("workspace_wallets" in q for q in inserts)


def test_revenuecat_webhook_top_up_idempotency(webhook_client):
    """Replaying the same transaction returns duplicate without double-crediting."""
    pool = _make_mock_pool(tx_exists=True)
    webhook_client.state.pool = pool
    client = TestClient(webhook_client)

    payload = {
        "api_version": "1.0",
        "event": {
            "id": "rc_evt_duplicate",
            "type": "NON_RENEWING_PURCHASE",
            "app_user_id": _WS_ID,
            "product_id": "credits_10_usd",
        },
    }

    resp = client.post(
        "/v1/webhooks/revenuecat",
        headers={"Authorization": f"Bearer {_WEBHOOK_SECRET}"},
        json=payload,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "duplicate"
    assert data["applied"] is False


def test_revenuecat_webhook_invalid_signature_refused(webhook_client):
    """Webhook with missing or invalid secret returns 401 Unauthorized."""
    pool = _make_mock_pool()
    webhook_client.state.pool = pool
    client = TestClient(webhook_client)

    payload = {
        "event": {
            "id": "rc_evt_1002",
            "type": "NON_RENEWING_PURCHASE",
            "app_user_id": _WS_ID,
            "product_id": "credits_10_usd",
        }
    }

    resp = client.post(
        "/v1/webhooks/revenuecat",
        headers={"Authorization": "Bearer wrong_secret"},
        json=payload,
    )

    assert resp.status_code == 401


# ── 6. Backend API Key Verify Reflects Wallet Credits ─────────────────────────


@pytest.fixture
def api_keys_client():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(api_keys.router, prefix="/v1")
    return app


def test_verify_api_key_reflects_positive_credits(api_keys_client):
    """verify endpoint returns has_credits=True and balance_usd when positive."""
    resolve_row = {
        "key_id": _KEY_ID,
        "workspace_id": _WS_ID,
        "entitlement": "pro",
    }
    wallet_row = {"balance_usd": 15.500000}
    pool = _make_mock_pool(resolve_row=resolve_row, wallet_row=wallet_row)
    api_keys_client.state.pool = pool
    client = TestClient(api_keys_client)

    resp = client.post(
        "/v1/api-keys/verify",
        headers={"Authorization": f"Bearer {_RAW_KEY}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["workspace_id"] == _WS_ID
    assert data["has_credits"] is True
    assert data["balance_usd"] == 15.50


def test_verify_api_key_reflects_empty_credits(api_keys_client):
    """verify endpoint returns has_credits=False and balance_usd=0.0 when zero."""
    resolve_row = {
        "key_id": _KEY_ID,
        "workspace_id": _WS_ID,
        "entitlement": "pro",
    }
    wallet_row = {"balance_usd": 0.000000}
    pool = _make_mock_pool(resolve_row=resolve_row, wallet_row=wallet_row)
    api_keys_client.state.pool = pool
    client = TestClient(api_keys_client)

    resp = client.post(
        "/v1/api-keys/verify",
        headers={"Authorization": f"Bearer {_RAW_KEY}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["has_credits"] is False
    assert data["balance_usd"] == 0.0


def test_verify_api_key_reflects_missing_wallet(api_keys_client):
    """verify endpoint returns has_credits=False and balance_usd=None when no wallet row."""
    resolve_row = {
        "key_id": _KEY_ID,
        "workspace_id": _WS_ID,
        "entitlement": "pro",
    }
    pool = _make_mock_pool(resolve_row=resolve_row, wallet_row=None)
    api_keys_client.state.pool = pool
    client = TestClient(api_keys_client)

    resp = client.post(
        "/v1/api-keys/verify",
        headers={"Authorization": f"Bearer {_RAW_KEY}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["has_credits"] is False
    assert data["balance_usd"] is None


# ── 7. Advanced Hybrid & Edge Cases ───────────────────────────────────────────


def test_check_budget_allows_active_subscription_when_no_wallet():
    """An active subscription under monthly cost cap allows calls without a wallet."""
    sub_row = {
        "status": "active",
        "monthly_cost_cap_usd": 100.0,
    }
    pool = _make_mock_pool(wallet_row=None, sub_row=sub_row)

    with patch("mcp_server.meter.acheck_budget", AsyncMock(return_value=True)):
        allowed = _run(check_budget(_WS_ID, pool))
        assert allowed is True


def test_check_budget_allows_positive_wallet_when_subscription_cap_exhausted():
    """Positive wallet balance permits execution even when subscription cap is exhausted."""
    wallet_row = {"balance_usd": 25.000000}
    sub_row = {
        "status": "active",
        "monthly_cost_cap_usd": 50.0,
    }
    pool = _make_mock_pool(wallet_row=wallet_row, sub_row=sub_row)

    # acheck_budget would return False (cap exhausted), but positive wallet balance permits execution
    with patch("mcp_server.meter.acheck_budget", AsyncMock(return_value=False)):
        allowed = _run(check_budget(_WS_ID, pool))
        assert allowed is True


def test_revenuecat_webhook_handles_test_event(webhook_client):
    """RevenueCat dashboard TEST event returns 200 OK without requiring valid workspace UUID."""
    pool = _make_mock_pool()
    webhook_client.state.pool = pool
    client = TestClient(webhook_client)

    payload = {
        "api_version": "1.0",
        "event": {
            "id": "rc_test_123",
            "type": "TEST",
            "app_user_id": "$RCAnonymousID:test_user_dashboard",
        },
    }

    resp = client.post(
        "/v1/webhooks/revenuecat",
        headers={"Authorization": f"Bearer {_WEBHOOK_SECRET}"},
        json=payload,
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_check_budget_refuses_free_tier_without_wallet():
    """Free tier with 0 monthly cost cap and no wallet is refused with InsufficientCreditsError."""
    sub_row = {
        "status": "active",
        "monthly_cost_cap_usd": 0.0,
    }
    pool = _make_mock_pool(wallet_row=None, sub_row=sub_row)

    with pytest.raises(InsufficientCreditsError) as exc_info:
        _run(check_budget(_WS_ID, pool))

    assert "402 Payment Required" in str(exc_info.value)
    assert "Insufficient MCP credits" in str(exc_info.value)


def test_check_budget_allows_free_tier_with_positive_wallet():
    """Free tier with $0 subscription cap but positive wallet balance is permitted."""
    sub_row = {
        "status": "active",
        "monthly_cost_cap_usd": 0.0,
    }
    wallet_row = {"balance_usd": 10.0}
    pool = _make_mock_pool(wallet_row=wallet_row, sub_row=sub_row)

    allowed = _run(check_budget(_WS_ID, pool))
    assert allowed is True


def test_verify_api_key_free_tier_with_and_without_credits(api_keys_client):
    """Free tier workspace reports active=False and correctly reflects has_credits."""
    # Without credits
    resolve_row = {
        "key_id": _KEY_ID,
        "workspace_id": _WS_ID,
        "entitlement": "free",
    }
    sub_row = {
        "status": "active",
        "entitlement": "free",
        "current_period_end": None,
    }
    pool = _make_mock_pool(resolve_row=resolve_row, sub_row=sub_row, wallet_row=None)
    api_keys_client.state.pool = pool
    client = TestClient(api_keys_client)

    resp = client.post(
        "/v1/api-keys/verify",
        headers={"Authorization": f"Bearer {_RAW_KEY}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["tier"] == "free"
    assert data["active"] is False
    assert data["has_credits"] is False

    # With credits
    pool_with_credits = _make_mock_pool(
        resolve_row=resolve_row,
        sub_row=sub_row,
        wallet_row={"balance_usd": 25.0},
    )
    api_keys_client.state.pool = pool_with_credits
    resp2 = client.post(
        "/v1/api-keys/verify",
        headers={"Authorization": f"Bearer {_RAW_KEY}"},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["valid"] is True
    assert data2["tier"] == "free"
    assert data2["active"] is False
    assert data2["has_credits"] is True
    assert data2["balance_usd"] == 25.0
