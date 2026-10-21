"""Live-Postgres: what ``apply_wallet_top_up`` really does to a wallet (MM16 and the tri-state).

``tests/backend/test_revenuecat_topup_rules.py`` proves the rules and the outcome-to-response
mapping with the write patched out. It cannot prove the three properties that live in the
database, so they get their own file:

* a well-formed workspace id that names **no** workspace hits the ``workspaces`` foreign key. That
  used to surface as a 500; it is now the outcome ``"unknown"`` and nothing is credited. A mocked
  pool never raises ``asyncpg.ForeignKeyViolationError``, so only a real Postgres can show it.
* the idempotency key ``(provider, external_transaction_id)`` is enforced by a unique index under
  FORCE RLS -- including across workspaces, where the first row is invisible to the second
  workspace's ``SELECT`` -- and holds when a second delivery races an uncommitted first.
* a wallet row is created on the first credit and incremented on the next, at 1000 credits per
  dollar, in the same transaction as the audit row.

The primitive is exercised directly **and** through both routes that reach it, so the 404 is
proven against the real foreign key, not against a patched return value.

Imports (what the ``backend-db`` CI job must install): ``backend.routers.webhooks`` pulls
``slowapi`` (and its ``limits`` chain) on top of the job's minimal list; nothing else.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")
httpx = pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402

from backend.database import (
    create_pool,  # noqa: E402
    workspace_scope,  # noqa: E402
)
from backend.errors import register_error_handlers  # noqa: E402
from backend.routers import webhooks  # noqa: E402

_SECRET = "rc_live_secret_top_up"  # nosec B105 -- throwaway test value
_AUTH = {"Authorization": f"Bearer {_SECRET}"}
_PROVIDER = "revenuecat_stripe"


async def _new_workspace(conn, email: str) -> str:
    """Minimal signup (as test_publish_settings_live.py does): the signup trigger provisions
    one workspace per user."""
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name, password_hash) "
        "VALUES($1, $2, 'h') RETURNING id::text",
        email,
        email.split("@")[0],
    )
    return await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


@contextlib.asynccontextmanager
async def _stack(dsns):
    """The real runtime pool (``gtm_api``, FORCE RLS), an owner pool for assertions that must see
    every tenant, and the webhook router served over ASGI on the SAME event loop as the pool."""
    api = await create_pool(dsns["api_dsn"], min_size=1, max_size=6)
    admin = await asyncpg.create_pool(dsns["admin_dsn"], min_size=1, max_size=2)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(webhooks.router, prefix="/v1")
    app.state.pool = api
    # raise_app_exceptions=False: an unhandled error must show up as the 500 it would be in
    # production, so "no 500" is an assertion on a status code and not on a Python traceback.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield api, admin, http
    finally:
        await api.close()
        await admin.close()


async def _wallet(admin, ws: str):
    return await admin.fetchrow(
        "SELECT balance_credits, balance_usd FROM workspace_wallets WHERE workspace_id = $1::uuid",
        ws,
    )


async def _tx_rows(admin, ws: str | None = None):
    if ws is None:
        return await admin.fetch("SELECT * FROM wallet_transactions")
    return await admin.fetch("SELECT * FROM wallet_transactions WHERE workspace_id = $1::uuid", ws)


def _event(ws: str, *, event_id: str, product_id: str = "credits_25_usd") -> dict:
    return {
        "api_version": "1.0",
        "event": {
            "id": event_id,
            "type": "NON_RENEWING_PURCHASE",
            "app_user_id": ws,
            "product_id": product_id,
        },
    }


def _routes(ws: str) -> dict[str, str]:
    return {"revenuecat": "/v1/webhooks/revenuecat", "generic": f"/v1/webhooks/revenuecat/{ws}"}


# -- the primitive ------------------------------------------------------------------------


def test_a_first_credit_creates_the_wallet_and_a_second_increments_it(clean_db):
    async def body():
        async with _stack(clean_db) as (api, admin, _):
            async with api.acquire() as c:
                ws = await _new_workspace(c, "topup-a@example.com")
            assert await _wallet(admin, ws) is None

            first = await webhooks.apply_wallet_top_up(api, ws, 25.0, _PROVIDER, "tx-1", 25000.0)
            second = await webhooks.apply_wallet_top_up(api, ws, 10.0, _PROVIDER, "tx-2", 10000.0)
            # credits omitted: a dollar is a thousand credits
            third = await webhooks.apply_wallet_top_up(api, ws, 2.0, _PROVIDER, "tx-3")

            assert (first, second, third) == ("new", "new", "new")
            wallet = await _wallet(admin, ws)
            assert float(wallet["balance_credits"]) == 37000.0
            assert float(wallet["balance_usd"]) == 37.0
            rows = await _tx_rows(admin, ws)
            assert sorted(float(r["credits_deposited"]) for r in rows) == [2000.0, 10000.0, 25000.0]
            assert {r["provider"] for r in rows} == {_PROVIDER}

    asyncio.run(body())


def test_a_replayed_transaction_id_is_a_duplicate_and_credits_once(clean_db):
    async def body():
        async with _stack(clean_db) as (api, admin, _):
            async with api.acquire() as c:
                ws = await _new_workspace(c, "topup-b@example.com")

            outcomes = [
                await webhooks.apply_wallet_top_up(api, ws, 25.0, _PROVIDER, "tx-replay")
                for _ in range(3)
            ]

            assert outcomes == ["new", "duplicate", "duplicate"]
            assert float((await _wallet(admin, ws))["balance_credits"]) == 25000.0
            assert len(await _tx_rows(admin, ws)) == 1

    asyncio.run(body())


def test_the_transaction_id_is_one_key_across_workspaces_but_not_across_providers(clean_db):
    """Under FORCE RLS the first workspace's audit row is invisible to the second workspace's
    duplicate-check ``SELECT``; the unique index is what stops a replay that names a different
    workspace. Without this a redelivered payment could be re-pointed at another tenant."""

    async def body():
        async with _stack(clean_db) as (api, admin, _):
            async with api.acquire() as c:
                ws_a = await _new_workspace(c, "topup-c1@example.com")
                ws_b = await _new_workspace(c, "topup-c2@example.com")

            a = await webhooks.apply_wallet_top_up(api, ws_a, 25.0, _PROVIDER, "tx-shared")
            b = await webhooks.apply_wallet_top_up(api, ws_b, 25.0, _PROVIDER, "tx-shared")
            other = await webhooks.apply_wallet_top_up(
                api, ws_b, 5.0, "other_provider", "tx-shared"
            )

            assert (a, b, other) == ("new", "duplicate", "new")
            assert float((await _wallet(admin, ws_a))["balance_credits"]) == 25000.0
            assert float((await _wallet(admin, ws_b))["balance_credits"]) == 5000.0

    asyncio.run(body())


def test_a_workspace_that_does_not_exist_is_unknown_and_nothing_is_written(clean_db):
    """The MM16 defect, at the primitive: the write violates ``wallet_transactions``' foreign key
    to ``workspaces``. Anything but a clean ``"unknown"`` here is the 500."""

    async def body():
        async with _stack(clean_db) as (api, admin, _):
            ghost = str(uuid.uuid4())

            outcome = await webhooks.apply_wallet_top_up(api, ghost, 25.0, _PROVIDER, "tx-ghost")

            assert outcome == "unknown"
            assert await _tx_rows(admin) == []
            assert await admin.fetchval("SELECT count(*) FROM workspace_wallets") == 0

    asyncio.run(body())


# -- both routes, against the real database ---------------------------------------------------


@pytest.mark.parametrize("route_name", ["revenuecat", "generic"])
def test_a_purchase_for_a_workspace_that_does_not_exist_is_a_404_not_a_500(
    clean_db, monkeypatch, caplog, route_name
):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", _SECRET)

    async def body():
        async with _stack(clean_db) as (api, admin, http):
            ghost = str(uuid.uuid4())  # well-formed, names nothing
            payload = _event(ghost, event_id="rc_evt_ghost_9")

            with caplog.at_level(logging.WARNING, logger=webhooks.log.name):
                resp = await http.post(_routes(ghost)[route_name], headers=_AUTH, json=payload)

            assert resp.status_code == 404, resp.text
            assert resp.json()["error"]["code"] == "workspace_not_found"
            warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
            assert any("rc_evt_ghost_9" in m and ghost in m for m in warnings), warnings
            assert await _tx_rows(admin) == []
            assert await admin.fetchval("SELECT count(*) FROM workspace_wallets") == 0

    asyncio.run(body())


@pytest.mark.parametrize("route_name", ["revenuecat", "generic"])
def test_a_purchase_credits_the_real_wallet_once_at_a_thousand_credits_per_dollar(
    clean_db, monkeypatch, route_name
):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", _SECRET)

    async def body():
        async with _stack(clean_db) as (api, admin, http):
            async with api.acquire() as c:
                ws = await _new_workspace(c, f"topup-{route_name}@example.com")
            url = _routes(ws)[route_name]
            payload = _event(ws, event_id="rc_evt_real_1")

            first = await http.post(url, headers=_AUTH, json=payload)
            replay = await http.post(url, headers=_AUTH, json=payload)

            assert first.status_code == 200 and first.json()["status"] == "ok", first.text
            assert replay.status_code == 200 and replay.json()["status"] == "duplicate"
            wallet = await _wallet(admin, ws)
            assert float(wallet["balance_credits"]) == 25000.0
            assert float(wallet["balance_usd"]) == 25.0
            rows = await _tx_rows(admin, ws)
            assert [r["external_transaction_id"] for r in rows] == ["rc_evt_real_1"]

    asyncio.run(body())


async def _a_backend_is_parked_on_a_lock(admin, timeout: float = 5.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await admin.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
        ):
            return True
        await asyncio.sleep(0.02)
    return False


def test_a_delivery_racing_an_uncommitted_one_loses_cleanly(clean_db):
    """RevenueCat retries on a slow reply, so two deliveries of one purchase can be in flight
    together: both pass the duplicate-check ``SELECT`` before either commits. The unique index plus
    ``ON CONFLICT DO NOTHING`` is what makes the second one a ``"duplicate"`` rather than a 500.

    Made deterministic instead of hoped-for: transaction A inserts the audit row and stays open;
    delivery B is started and waits (observed in ``pg_stat_activity``) on A's uncommitted unique
    key; only then does A commit."""

    async def body():
        async with _stack(clean_db) as (api, admin, _):
            async with api.acquire() as c:
                ws = await _new_workspace(c, "topup-race@example.com")

            async with workspace_scope(api, ws) as a:
                await a.execute(
                    "INSERT INTO wallet_transactions "
                    "(workspace_id, amount_usd, credits_deposited, provider, external_transaction_id) "
                    "VALUES ($1::uuid, 25, 25000, $2, 'tx-race')",
                    ws,
                    _PROVIDER,
                )
                b = asyncio.create_task(
                    webhooks.apply_wallet_top_up(api, ws, 25.0, _PROVIDER, "tx-race")
                )
                assert await _a_backend_is_parked_on_a_lock(admin), "delivery B never raced A"
                assert not b.done()
            # A has committed; B is released onto a conflicting key.

            assert await b == "duplicate"
            assert len(await _tx_rows(admin, ws)) == 1
            assert await _wallet(admin, ws) is None  # B credited nothing

    asyncio.run(body())
