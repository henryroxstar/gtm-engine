"""Characterisation (green by design, NOT a discriminating red): what flipping
``COST_RESERVATION_ENABLED`` on does to a SUBSCRIPTION-ONLY workspace — one with a plan cap and a $0 wallet.

Why this exists. The plan (MM2, task 4.6) calls for making ``COST_RESERVATION_ENABLED`` default ON, which
arms the settlement that debits a run's actual spend from the wallet. ``settle_credits`` debits the wallet
whatever KIND of reservation held the run, and with the flag on a run that the wallet cannot cover is
still admitted by the legacy USD-cap reservation (``areserve_budget``). So the first run of a workspace
that pays by subscription and never topped up its wallet leaves the wallet NEGATIVE by the run's spend —
and a negative wallet reads as "no credits" to everything that asks the wallet alone (the edge's
``has_credits``), although the MCP guard itself still admits the workspace on its subscription.

This file pins that behaviour as it is, so the decision to flip the default is made against a fact and
cannot be made by accident. If a later change gives subscription-only runs a way to settle against their
plan rather than the wallet, this test going red is the signal to revisit the default.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

from gtm_core.db import workspace_scope  # noqa: E402
from tests.backend._live_helpers import (  # noqa: E402
    marker,
    seed_cost_record,
    seed_run,
    seed_workspace,
    wallet,
)


def test_a_subscription_only_workspace_goes_negative_on_its_first_run_with_the_flag_on(
    clean_db, monkeypatch
):
    pytest.importorskip("backend.services.runs.budget")
    from backend.services.runs.budget import _reserve_or_deny, _settle_run_reservations
    from mcp_server.meter import check_budget

    monkeypatch.setenv("COST_RESERVATION_ENABLED", "true")

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=4)
        try:
            async with admin.acquire() as c:
                ws = await seed_workspace(c, "sub@example.com", credits=0.0, cap_usd=100.0)
            run = await seed_run(api, ws)

            # The run is ADMITTED — through the legacy USD-cap reservation, because the wallet
            # cannot cover a credit reservation. The plan's cap is what lets it start.
            assert await _reserve_or_deny(api, ws, run) is True

            await seed_cost_record(admin, ws, 0.5, run_id=run)  # it spends $0.50
            await _settle_run_reservations(api, ws, run)

            # ... and the settlement then debits a wallet that holds nothing: debt, recorded.
            assert await wallet(api, ws) == (-500.0, -0.5)
            assert await marker(api, ws) is None  # not a failure — the designed unclamped debit

            # What the debt does and does not do. The MCP guard still ADMITS the workspace — its
            # active subscription covers the call — so this is not a lock-out. But the wallet now
            # reads as "no credits" everywhere that asks the wallet alone (the edge's has_credits),
            # for a payment the workspace never owed: it is a plan customer shown as in arrears.
            assert await check_budget(ws, api) is True
            from gtm_core.metering import wallet_has_credits

            assert wallet_has_credits(-500.0, -0.5) is False
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_the_same_workspace_with_the_flag_off_is_never_debited_for_a_rest_run(
    clean_db, monkeypatch
):
    """The default today: no reservation, so nothing to settle, so the wallet is untouched."""
    pytest.importorskip("backend.services.runs.budget")
    from backend.services.runs.budget import _reserve_or_deny, _settle_run_reservations

    monkeypatch.setenv("COST_RESERVATION_ENABLED", "false")

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=4)
        try:
            async with admin.acquire() as c:
                ws = await seed_workspace(c, "sub@example.com", credits=0.0, cap_usd=100.0)
            run = await seed_run(api, ws)
            assert await _reserve_or_deny(api, ws, run) is True
            await seed_cost_record(admin, ws, 0.5, run_id=run)
            await _settle_run_reservations(api, ws, run)
            assert await wallet(api, ws) == (0.0, 0.0)
            async with workspace_scope(api, ws) as c:
                assert await c.fetchval("SELECT count(*) FROM cost_reservations") == 0
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())
