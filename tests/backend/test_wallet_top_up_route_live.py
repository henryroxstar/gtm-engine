"""Live-Postgres proof of MM7: the billing service's top-up route, and the one-channel rule, on a REAL wallet.

``test_payment_channel_switch.py`` proves the switch and the route's rules with the wallet write patched
out. These checks need the database: that a top-up lands in the real wallet at 1000 credits a dollar, that
its audit row carries the route's own provider (so its idempotency key can never collide with the
webhook's), that a replay credits nothing, that a well-formed id naming no workspace credits nothing, and
that — with the SAME payment sent down both channels — the wallet moves exactly once whichever channel the
setting names.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")
httpx = pytest.importorskip("httpx")
pytest.importorskip("slowapi")

from fastapi import FastAPI  # noqa: E402

from backend import payment_channel  # noqa: E402
from backend.database import create_pool  # noqa: E402
from backend.errors import register_error_handlers  # noqa: E402
from backend.routers import wallet, webhooks  # noqa: E402

_RC = "rc_live_secret_channel"  # nosec B105 -- throwaway test value
_SVC = "svc_live_secret_channel"  # nosec B105 -- throwaway test value
SETTING = payment_channel.SETTING


@contextlib.asynccontextmanager
async def _stack(dsns):
    api = await create_pool(dsns["api_dsn"], min_size=1, max_size=6)
    admin = await asyncpg.create_pool(dsns["admin_dsn"], min_size=1, max_size=2)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(webhooks.router, prefix="/v1")
    app.include_router(wallet.router, prefix="/v1")
    app.state.pool = api
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield api, admin, http
    finally:
        await api.close()
        await admin.close()


async def _workspace(conn, email: str) -> str:
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name, password_hash) VALUES($1, $2, 'h') RETURNING id::text",
        email,
        email.split("@")[0],
    )
    return await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


async def _wallet(admin, ws):
    row = await admin.fetchrow(
        "SELECT balance_credits, balance_usd FROM workspace_wallets WHERE workspace_id = $1::uuid",
        ws,
    )
    return None if row is None else (float(row["balance_credits"]), float(row["balance_usd"]))


def _svc(ws, order, amount=25.0):
    return {
        "url": f"/v1/wallet/{ws}/top-ups",
        "headers": {"Authorization": _SVC},
        "json": {"amount_usd": amount, "order_id": order},
    }


def _rc(ws, event_id):
    return {
        "url": "/v1/webhooks/revenuecat",
        "headers": {"Authorization": f"Bearer {_RC}"},
        "json": {
            "api_version": "1.0",
            "event": {
                "id": event_id,
                "type": "NON_RENEWING_PURCHASE",
                "app_user_id": ws,
                "product_id": "credits_25_usd",
            },
        },
    }


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", _RC)
    monkeypatch.setenv("BILLING_SYNC_SECRET", _SVC)


def test_a_top_up_credits_the_real_wallet_once_at_a_thousand_credits_per_dollar(
    clean_db, monkeypatch
):
    monkeypatch.setenv(SETTING, "true")

    async def body():
        async with _stack(clean_db) as (api, admin, http):
            async with api.acquire() as c:
                ws = await _workspace(c, "topup-route@example.com")

            first = await http.post(**_svc(ws, "ord_1"))
            replay = await http.post(**_svc(ws, "ord_1"))
            second = await http.post(**_svc(ws, "ord_2", 10.0))

            assert first.status_code == 200 and first.json() == {"applied": True, "outcome": "new"}
            assert replay.json() == {"applied": False, "outcome": "duplicate"}
            assert second.json() == {"applied": True, "outcome": "new"}
            assert await _wallet(admin, ws) == (35_000.0, 35.0)  # 25 + 10, the replay added nothing
            rows = await admin.fetch(
                "SELECT provider, external_transaction_id FROM wallet_transactions "
                "WHERE workspace_id = $1::uuid ORDER BY external_transaction_id",
                ws,
            )
            assert [(r["provider"], r["external_transaction_id"]) for r in rows] == [
                ("billing_service", "ord_1"),
                ("billing_service", "ord_2"),
            ]

    asyncio.run(body())


def test_the_same_order_id_from_the_webhook_and_the_route_are_two_different_payments(
    clean_db, monkeypatch
):
    """Idempotency is per (provider, id): the route's `ord_x` must not be swallowed by a webhook event
    that happens to share the string — and while only ONE channel is open, only one ever credits."""

    async def body():
        async with _stack(clean_db) as (api, admin, http):
            async with api.acquire() as c:
                ws = await _workspace(c, "two-channels@example.com")

            monkeypatch.delenv(SETTING, raising=False)  # webhook live, route shut
            assert (await http.post(**_rc(ws, "shared_id"))).status_code == 200
            assert (await http.post(**_svc(ws, "shared_id"))).status_code == 409
            assert await _wallet(admin, ws) == (25_000.0, 25.0)

            monkeypatch.setenv(SETTING, "true")  # the other way round
            assert (await http.post(**_rc(ws, "shared_id_2"))).status_code == 409
            assert (await http.post(**_svc(ws, "shared_id"))).json() == {
                "applied": True,
                "outcome": "new",
            }
            assert await _wallet(admin, ws) == (
                50_000.0,
                50.0,
            )  # 25 + 25: each payment counted ONCE

    asyncio.run(body())


def test_a_top_up_for_a_workspace_that_does_not_exist_credits_nothing_and_says_so(
    clean_db, monkeypatch, caplog
):
    monkeypatch.setenv(SETTING, "true")

    async def body():
        async with _stack(clean_db) as (api, admin, http):
            ghost = str(uuid.uuid4())
            resp = await http.post(**_svc(ghost, "ord_ghost"))
            assert resp.status_code == 200 and resp.json() == {
                "applied": False,
                "outcome": "unknown",
            }
            assert await admin.fetchval("SELECT count(*) FROM wallet_transactions") == 0
            assert await admin.fetchval("SELECT count(*) FROM workspace_wallets") == 0

    asyncio.run(body())
    assert any("ord_ghost" in r.getMessage() for r in caplog.records)


def test_a_refused_top_up_leaves_an_audit_row_in_the_workspaces_own_tree(
    clean_db, monkeypatch, tmp_path
):
    monkeypatch.setenv(SETTING, "true")
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))

    async def body():
        async with _stack(clean_db) as (api, admin, http):
            async with api.acquire() as c:
                ws = await _workspace(c, "audit-route@example.com")
            (tmp_path / "workspaces" / ws / "content").mkdir(parents=True)

            assert (await http.post(**_svc(ws, "ord_big", 9999.0))).status_code == 422
            await http.post(**_svc(ws, "ord_a"))
            await http.post(**_svc(ws, "ord_a"))  # a duplicate
            return ws

    ws = asyncio.run(body())
    ledger = tmp_path / "workspaces" / ws / "content" / "_billing" / "denials.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    kinds = [r["detail"].split(":")[0] for r in rows]
    assert kinds == ["amount_out_of_bounds", "duplicate"]
    assert all(r["tool"] == "wallet_top_up" and r["source"] == "billing-service" for r in rows)
    assert "ord_big" in rows[0]["detail"] and "ord_a" in rows[1]["detail"]


def test_a_user_token_cannot_credit_a_wallet(clean_db, monkeypatch):
    monkeypatch.setenv(SETTING, "true")

    async def body():
        async with _stack(clean_db) as (api, admin, http):
            async with api.acquire() as c:
                ws = await _workspace(c, "no-self-credit@example.com")
            forged = _svc(ws, "ord_forged")
            forged["headers"] = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.x"}
            resp = await http.post(**forged)
            assert resp.status_code == 401
            assert await _wallet(admin, ws) is None

    asyncio.run(body())
