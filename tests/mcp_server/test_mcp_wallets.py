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
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.errors import register_error_handlers
from backend.routers import api_keys, webhooks
from gtm_core.metering import BillingUnavailableError, DebitFailedError
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


class _TxCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _LedgerConn:
    """An in-memory wallet + ledger: applies each statement's ARGUMENTS, so a test can assert
    what changed rather than how the SQL is spelled."""

    def __init__(self, credits: float) -> None:
        self.credits = credits
        self.usd = credits / 1000.0
        self.audit: list[tuple] = []

    def transaction(self):
        return _TxCtx()

    async def fetchval(self, query, *args):
        return _WS_ID if "set_config" in query else None

    async def execute(self, query, *args):
        sql = " ".join(query.split())
        if sql.startswith("UPDATE workspace_wallets"):
            self.credits -= args[0]
            self.usd -= args[0] / 1000.0
        elif sql.startswith("INSERT INTO unified_metering_log"):
            self.audit.append(args)
        return "OK"


def _ledger_pool(conn):
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def test_meter_call_moves_the_balance_and_writes_exactly_one_audit_row():
    """Behaviour: the wallet moves by the call's cost and the ledger gains ONE row.

    This used to pin the literal ``GREATEST(0.0, ...)`` UPDATE — the clamp that erased debt.
    That the debit is UNCLAMPED is proven on a real database (test_wallet_debt_live.py); a
    mocked connection cannot evaluate SQL, so the in-memory ledger applies each statement's
    arguments instead.
    """
    conn = _LedgerConn(credits=100.0)

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
            pool=_ledger_pool(conn),
        )
    )

    assert conn.credits == pytest.approx(85.0)
    assert conn.usd == pytest.approx(0.085)
    assert len(conn.audit) == 1
    workspace_id, runtime, tool_name, cost_credits = conn.audit[0][:4]
    assert (workspace_id, runtime, tool_name) == (_WS_ID, "mcp", "draft_post")
    assert cost_credits == pytest.approx(15.0)


def test_a_failed_debit_is_still_raised_when_the_marker_write_fails_too(caplog):
    """The database is wholly unreachable: the debit fails AND the marker cannot be written.

    The original DebitFailedError must still surface (the marker write is best-effort and never
    masks it), and the double failure must be loud in the log — it is the only trace left.
    """
    conn = MagicMock()
    conn.transaction = lambda: _TxCtx()
    conn.execute = AsyncMock(side_effect=RuntimeError("db unreachable"))
    pool = _ledger_pool(conn)

    with caplog.at_level("ERROR"), pytest.raises(DebitFailedError):
        _run(
            meter_call(
                workspace_id=_WS_ID,
                api_key_id=_KEY_ID,
                tool_name="draft_post",
                profile_name="acme-test",
                model="m",
                prompt_tokens=1,
                completion_tokens=1,
                cost_usd=0.01,
                pool=pool,
            )
        )

    assert any(r.levelname == "ERROR" and _WS_ID in r.getMessage() for r in caplog.records), (
        "the double failure must name the workspace in an ERROR log"
    )


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


# Only a one-off credit pack funds the wallet (#340 Q2/Q3). A subscription's allowance
# arrives as the monthly cap via PUT /v1/entitlement, so crediting its purchase or renewal
# would pay a subscriber twice; and an unmapped product's price is in the buyer's
# currency, so reading it as USD credits ¥1,000 as $1,000.
_NOT_A_TOP_UP = [
    pytest.param("RENEWAL", "pro_monthly_50_usd", 50.00, id="subscription-renewal"),
    pytest.param("INITIAL_PURCHASE", "pro_monthly_50_usd", 50.00, id="subscription-start"),
    pytest.param(
        "NON_RENEWING_PURCHASE", "pro_monthly_50_usd", 50.00, id="subscription-product-id"
    ),
    pytest.param("NON_RENEWING_PURCHASE", "coin_bundle_large", 1000.0, id="unmapped-foreign-price"),
]


def _rc_payload(ev_type: str, product_id: str, price: float) -> dict[str, Any]:
    return {
        "api_version": "1.0",
        "event": {
            "id": f"rc_evt_{ev_type}_{product_id}",
            "type": ev_type,
            "app_user_id": _WS_ID,
            "product_id": product_id,
            "price_in_purchased_currency": price,
        },
    }


def _post_both_routes(webhook_client, payload: dict[str, Any]) -> tuple[list[Any], AsyncMock]:
    """POST the same event to both RevenueCat routes; return the responses and the top-up mock."""
    webhook_client.state.pool = _make_mock_pool()
    client = TestClient(webhook_client)
    headers = {"Authorization": f"Bearer {_WEBHOOK_SECRET}"}
    top_up = AsyncMock(return_value=True)
    with (
        patch("backend.routers.webhooks.apply_wallet_top_up", top_up),
        patch(
            "backend.routers.webhooks._resolve_provider_secrets",
            AsyncMock(return_value=[_WEBHOOK_SECRET]),
        ),
    ):
        responses = [
            client.post("/v1/webhooks/revenuecat", headers=headers, json=payload),
            client.post(f"/v1/webhooks/revenuecat/{_WS_ID}", headers=headers, json=payload),
        ]
    return responses, top_up


@pytest.mark.parametrize(("ev_type", "product_id", "price"), _NOT_A_TOP_UP)
def test_revenuecat_non_top_up_never_funds_wallet(webhook_client, ev_type, product_id, price):
    responses, top_up = _post_both_routes(webhook_client, _rc_payload(ev_type, product_id, price))

    for resp in responses:
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
    top_up.assert_not_awaited()


def test_revenuecat_credit_pack_funds_wallet_on_both_routes(webhook_client):
    """Positive control for the test above: a mapped credit pack still credits, on both routes."""
    responses, top_up = _post_both_routes(
        webhook_client, _rc_payload("NON_RENEWING_PURCHASE", "credits_100_usd", 100.00)
    )

    for resp in responses:
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
    assert top_up.await_count == 2
    assert {c.kwargs["amount_usd"] for c in top_up.await_args_list} == {100.0}


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


# ── 8. MM3: one balance predicate, and a failed debit refuses the next call ───

# (balance_credits, balance_usd, has_credits) — absent columns are simply omitted from the row.
_WALLET_CASES = {
    "positive": (2000.0, 2.0, True),
    "zero": (0.0, 0.0, False),
    "in-debt": (-3000.0, -3.0, False),
    "usd-only-positive": (None, 10.0, True),
    "usd-only-zero": (None, 0.0, False),
    "usd-only-in-debt": (None, -1.5, False),
    "credits-only-positive": (5000.0, None, True),
    "credits-only-zero": (0.0, None, False),
    "credits-only-in-debt": (-1.0, None, False),
}


def _wallet_row(credits: float | None, usd: float | None) -> dict[str, float]:
    row: dict[str, float] = {}
    if credits is not None:
        row["balance_credits"] = credits
    if usd is not None:
        row["balance_usd"] = usd
    return row


def _verify(api_keys_client, wallet_row) -> dict:
    resolve_row = {"key_id": _KEY_ID, "workspace_id": _WS_ID, "entitlement": "pro"}
    api_keys_client.state.pool = _make_mock_pool(resolve_row=resolve_row, wallet_row=wallet_row)
    resp = TestClient(api_keys_client).post(
        "/v1/api-keys/verify", headers={"Authorization": f"Bearer {_RAW_KEY}"}
    )
    assert resp.status_code == 200
    return resp.json()


def _mcp_guard_allows(wallet_row) -> bool:
    pool = _make_mock_pool(wallet_row=wallet_row, sub_row=None)
    try:
        return _run(check_budget(_WS_ID, pool))
    except InsufficientCreditsError:
        return False


@pytest.mark.parametrize("case", sorted(_WALLET_CASES))
def test_the_verify_route_and_the_mcp_guard_agree_on_every_wallet(api_keys_client, case):
    """Surface agreement (test plan §3.E): the edge ``has_credits`` and the MCP guard answer the
    same question, including for a wallet in DEBT. Characterisation of the truth table; the
    agreement itself is held by the shared predicate (the delegation test below)."""
    credits, usd, expected = _WALLET_CASES[case]
    wallet_row = _wallet_row(credits, usd)

    assert _verify(api_keys_client, wallet_row)["has_credits"] is expected
    assert _mcp_guard_allows(wallet_row) is expected


@pytest.mark.parametrize("verdict", [True, False])
def test_both_surfaces_take_their_verdict_from_the_one_predicate(
    monkeypatch, api_keys_client, verdict
):
    """Two hand-written copies of "is this wallet positive" cannot be kept in agreement by review;
    one function can. Stub it, give each surface a wallet whose balances say the OPPOSITE, and
    the answer can only have come from the shared predicate."""
    asked: list[tuple] = []

    def _stub(*args, **kwargs):
        asked.append(args)
        return verdict

    monkeypatch.setattr("mcp_server.meter.wallet_has_credits", _stub)
    monkeypatch.setattr("backend.routers.api_keys.wallet_has_credits", _stub)
    opposite = _wallet_row(-5000.0, -5.0) if verdict else _wallet_row(5000.0, 5.0)

    assert _mcp_guard_allows(opposite) is verdict
    assert _verify(api_keys_client, opposite)["has_credits"] is verdict
    assert len(asked) == 2


def test_check_budget_refuses_a_failed_debit_even_with_a_positive_balance():
    """The marker beats the balance, and the error is NOT the "top up" one (a top-up won't clear it)."""
    wallet_row = {
        "balance_usd": 10.0,
        "balance_credits": 10_000.0,
        "debit_failed_at": datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
    }
    pool = _make_mock_pool(wallet_row=wallet_row)

    with pytest.raises(BillingUnavailableError) as refused:
        _run(check_budget(_WS_ID, pool))
    assert not isinstance(refused.value, InsufficientCreditsError)
    assert "top up" not in str(refused.value).lower()


def test_draft_tools_refuse_a_failed_debit_before_spending(monkeypatch):
    """draft_post / draft_outreach stop at the guard — the paid provider is never reached."""
    from gtm_core.capabilities import Entitlement
    from mcp_server import server
    from mcp_server.auth import ApiKeyCtx

    wallet_row = {
        "balance_usd": 10.0,
        "balance_credits": 10_000.0,
        "debit_failed_at": datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
    }
    monkeypatch.setattr(server._state, "pool", _make_mock_pool(wallet_row=wallet_row))
    ctx = ApiKeyCtx(key_id=_KEY_ID, workspace_id=_WS_ID, entitlement=Entitlement.PRO)
    monkeypatch.setattr(server, "_require_profile", AsyncMock(return_value=ctx))
    provider = AsyncMock(return_value=("never", 1, 1, 0.01))
    monkeypatch.setattr(server, "_deepseek", provider)

    with pytest.raises(BillingUnavailableError):
        _run(draft_post(brief="b", profile="p"))
    with pytest.raises(BillingUnavailableError):
        _run(draft_outreach(brief="b", profile="p"))
    provider.assert_not_awaited()


@pytest.mark.parametrize(("fail_closed", "expected"), [(True, False), (False, True)])
def test_an_unreadable_marker_follows_the_budget_read_policy(monkeypatch, fail_closed, expected):
    """A marker that cannot be READ is not a known failed debit: it takes the same policy as an
    unreadable budget — the paid backend route (fail_closed) denies, the MCP/VPS default carries
    on to the cap check. (A marker that IS read always denies; test_wallet_debt_live.py.)"""
    from gtm_core import metering

    async def _unreadable(*_a, **_k):
        raise RuntimeError("db down")

    async def _under_cap(self, workspace_id, *, conn=None):
        return (10.0, 1.0)

    monkeypatch.setattr(metering, "_wallet_debit_failed", _unreadable)
    monkeypatch.setattr(metering.PgSink, "cap_and_spent", _under_cap)

    allowed = _run(
        metering.acheck_budget(None, _WS_ID, table="cost_records", fail_closed=fail_closed)
    )
    assert allowed is expected
