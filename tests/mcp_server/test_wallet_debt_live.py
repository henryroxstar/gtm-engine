"""Live-Postgres proof of MM3: an overrun is recorded as debt, and a failed debit refuses the NEXT call.

Mocked pools cannot see RLS or a real aborted transaction, so every check here runs against a
REAL Postgres as the non-owner ``gtm_api`` role. Skipped unless ``GTM_TEST_PG_ADMIN_DSN`` is set
(root tests/conftest.py). Each test wraps an async body in ``asyncio.run`` (no pytest-asyncio).

Fault injection sits at the CONNECTION, not at a mocked function: ``_FaultyPool`` wraps a real
pool, and when the SQL it sees is the wallet debit (or the audit insert) it first runs a real
failing statement (``SELECT 1/0``) on the real connection — so the transaction is genuinely
aborted exactly as a production failure aborts it — and everything else hits the real database.

What is pinned (the decided behaviour, PRD 2026-09-22 §4.B, resolved 2026-10-14):
  - the debit is unclamped: $5 against $2 leaves -$3 and ONE audit row for 5000 credits;
  - a debit that fails SURFACES (DebitFailedError) and leaves a durable marker
    (``workspace_wallets.debit_failed_at``, V044) written in its OWN transaction;
  - every gate that decides "may this workspace spend" reads that marker, so the next call is
    refused with BillingUnavailableError (not the "top up" error) — and only for the marked
    workspace; an unmarked one is unaffected (the positive control on every refusal);
  - the call that hit the failure still returns its draft (the work is done and paid for).

Import weight: the draft_post test and the run-settlement test import ``mcp_server.server`` and
``backend.services.runs`` (``mcp``, ``httpx``, ``slowapi`` ...) lazily and skip when absent, so
this file still collects in the minimal pytest+asyncpg CI job.
"""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

from gtm_core.db import workspace_scope  # noqa: E402
from gtm_core.metering import (  # noqa: E402
    BillingUnavailableError,
    DebitFailedError,
    InsufficientCreditsError,
    acheck_budget,
    areserve_budget,
    reserve_credits,
)
from mcp_server.meter import check_budget, meter_call  # noqa: E402

# ── seeding + readers ─────────────────────────────────────────────────────────


async def _register(conn, email: str) -> str:
    """Insert a user (fires the SECURITY DEFINER bootstrap trigger); return the workspace id."""
    uid = await conn.fetchval(
        "INSERT INTO users(email, display_name, password_hash) "
        "VALUES($1, $2, 'h') RETURNING id::text",
        email,
        email.split("@")[0],
    )
    return await conn.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


async def _seed_workspace(admin, email: str, *, credits: float, cap_usd: float = 100.0) -> str:
    """A workspace with a prepaid wallet of ``credits`` and a subscription cap that admits spend."""
    wid = await _register(admin, email)
    await admin.execute(
        "INSERT INTO workspace_wallets(workspace_id, balance_credits, balance_usd) "
        "VALUES($1::uuid, $2, $3) ON CONFLICT (workspace_id) DO UPDATE "
        "SET balance_credits = EXCLUDED.balance_credits, balance_usd = EXCLUDED.balance_usd",
        wid,
        credits,
        credits / 1000.0,
    )
    await admin.execute(
        "UPDATE subscriptions SET monthly_cost_cap_usd = $2 WHERE workspace_id = $1::uuid",
        wid,
        cap_usd,
    )
    return wid


async def _seed_key(admin, wid: str, raw: str) -> str:
    return await admin.fetchval(
        "INSERT INTO api_keys(workspace_id, key_hash, prefix, entitlement) "
        "VALUES($1::uuid, $2, $3, 'pro') RETURNING id::text",
        wid,
        hashlib.sha256(raw.encode()).hexdigest(),
        raw[:8],
    )


async def _seed_run(api, wid: str) -> str:
    async with workspace_scope(api, wid) as c:
        return await c.fetchval(
            "INSERT INTO runs(workspace_id, profile_name, prompt) "
            "VALUES($1::uuid, 'p', 'x') RETURNING id::text",
            wid,
        )


async def _marker(api, wid: str):
    async with workspace_scope(api, wid) as c:
        return await c.fetchval(
            "SELECT debit_failed_at FROM workspace_wallets WHERE workspace_id = $1::uuid", wid
        )


async def _wallet(api, wid: str) -> tuple[float, float]:
    async with workspace_scope(api, wid) as c:
        row = await c.fetchrow(
            "SELECT balance_credits, balance_usd FROM workspace_wallets "
            "WHERE workspace_id = $1::uuid",
            wid,
        )
    return float(row["balance_credits"]), float(row["balance_usd"])


async def _reservation_states(api, wid: str) -> list[str]:
    async with workspace_scope(api, wid) as c:
        rows = await c.fetch(
            "SELECT state FROM cost_reservations WHERE workspace_id = $1::uuid", wid
        )
    return [r["state"] for r in rows]


async def _seed_run_spend(admin, wid: str, run_id: str, usd: float) -> None:
    await admin.execute(
        "INSERT INTO cost_records(workspace_id, profile_name, run_id, stage, model, cost_usd) "
        "VALUES($1::uuid, 'p', $2, 'brain', 'm', $3)",
        wid,
        run_id,
        usd,
    )


async def _audit_credits(api, wid: str) -> list[float]:
    async with workspace_scope(api, wid) as c:
        rows = await c.fetch(
            "SELECT cost_credits FROM unified_metering_log WHERE workspace_id = $1::uuid", wid
        )
    return [float(r["cost_credits"]) for r in rows]


# ── the fault injector ────────────────────────────────────────────────────────

_FAULT_SQL = {
    # the settle's wallet debit — the statement MM3 is about
    "debit": "UPDATE WORKSPACE_WALLETS SET BALANCE_CREDITS",
    # the immutable audit row written in the same transaction
    "audit": "INSERT INTO UNIFIED_METERING_LOG",
    # the same debit, but failing CLIENT-side (asyncpg rejects a parameter before sending it): the
    # transaction is NOT aborted, so a caller that ignores the failure carries on and commits
    "debit-client": "UPDATE WORKSPACE_WALLETS SET BALANCE_CREDITS",
}


class _FaultyConn:
    """A real connection whose matching statement fails with a REAL Postgres error."""

    def __init__(self, real, pool: _FaultyPool) -> None:
        self._real = real
        self._pool = pool

    async def execute(self, sql, *args, **kwargs):
        fault = self._pool.fault
        if fault in _FAULT_SQL and " ".join(sql.split()).upper().startswith(_FAULT_SQL[fault]):
            if fault.endswith("-client"):
                raise RuntimeError("injected client-side failure")
            # A genuine server-side error: the surrounding transaction is aborted, as in prod.
            await self._real.execute("SELECT 1/0")
        return await self._real.execute(sql, *args, **kwargs)

    async def fetch(self, sql, *args, **kwargs):
        # "read": the settle's very first statement — the read of the run's open reservations — fails,
        # while every write works. Nothing is then known to be owed.
        if self._pool.fault == "read" and "cost_reservations" in sql:
            raise RuntimeError("injected read failure")
        return await self._real.fetch(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _AcquireCtx:
    def __init__(self, pool: _FaultyPool) -> None:
        self._pool = pool
        self._cm = None

    async def __aenter__(self):
        self._cm = self._pool.real.acquire()
        return _FaultyConn(await self._cm.__aenter__(), self._pool)

    async def __aexit__(self, *exc):
        return await self._cm.__aexit__(*exc)


class _FaultyPool:
    """Wraps a real pool; ``fault`` is None (healthy), ``"debit"``, ``"audit"`` or ``"read"``."""

    def __init__(self, real, fault: str | None = None) -> None:
        self.real = real
        self.fault = fault

    def acquire(self):
        return _AcquireCtx(self)


# ── 1.1 debt is recorded ──────────────────────────────────────────────────────


def test_overrun_is_recorded_as_negative_balance(clean_db):
    """$5 charged against a $2 wallet stores -$3 / -3000 credits and ONE 5000-credit audit row.

    The mutation this guards is the one that used to ship: ``GREATEST(0.0, balance - cost)``
    erased the overage, so a tenant whose call cost more than its balance got the rest free.
    """

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wa = await _seed_workspace(c, "a@example.com", credits=2000.0)
                key = await _seed_key(c, wa, "sk-key-a")

            await meter_call(
                workspace_id=wa,
                api_key_id=key,
                tool_name="draft_post",
                profile_name="acme",
                model="deepseek-v4-flash",
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=5.0,
                pool=api,
            )

            credits, usd = await _wallet(api, wa)
            assert usd == -3.0
            assert credits == -3000.0
            assert await _audit_credits(api, wa) == [5000.0]
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


# ── 1.2 characterisation (GREEN BY DESIGN — not a discriminating RED) ─────────


def test_a_wallet_in_debt_refuses_the_next_call(clean_db):
    """Characterisation, green by design: debt already refuses; this keeps it that way.

    It was true before MM3 (the guard has always treated a non-positive balance as a refusal);
    what MM3 changed is that debt can now EXIST. The test pins the pair so recording debt can
    never quietly become granting credit.
    """

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                # cap 0: no subscription headroom, so only the wallet can admit the call
                wa = await _seed_workspace(c, "a@example.com", credits=-3000.0, cap_usd=0.0)

            with pytest.raises(InsufficientCreditsError):
                await check_budget(wa, api)
            with pytest.raises(InsufficientCreditsError):
                await reserve_credits(wa, 10.0, pool=api, raise_on_error=True)
            assert await _marker(api, wa) is None  # debt is a balance, not a failed debit
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


# ── 1.3 a failed debit surfaces; the next call refuses ────────────────────────


@pytest.mark.parametrize("fault", ["debit", "audit", "debit-client"])
def test_failed_debit_surfaces_marks_the_wallet_and_the_next_call_refuses(clean_db, fault):
    """meter_call raises DebitFailedError, the marker is set, and EVERY gate then refuses.

    Both statements of the settle are faulted in turn: the ``audit`` case (the audit insert fails
    after the wallet UPDATE succeeded) used to leave "credits deducted but audit row missing" — it
    must now roll the whole settle back so the wallet and the ledger can never disagree. The
    ``debit-client`` case fails without aborting the transaction (see ``_FAULT_SQL``).
    """

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=4)
        try:
            async with admin.acquire() as c:
                wa = await _seed_workspace(c, "a@example.com", credits=10_000.0)
                wb = await _seed_workspace(c, "b@example.com", credits=10_000.0)
                key_a = await _seed_key(c, wa, "sk-key-a")

            with pytest.raises(DebitFailedError) as failed:
                await meter_call(
                    workspace_id=wa,
                    api_key_id=key_a,
                    tool_name="draft_post",
                    profile_name="acme",
                    model="m",
                    prompt_tokens=1,
                    completion_tokens=1,
                    cost_usd=1.0,
                    pool=_FaultyPool(api, fault),
                )
            # The error names its cause: an operator reconciling an uncollected charge needs the
            # driver error, not just "it failed". Only the raise at the failing statement chains it.
            assert failed.value.__cause__ is not None

            # The marker is durable, and was written outside the transaction that failed.
            assert await _marker(api, wa) is not None
            # The aborted statement took the whole settle with it: nothing half-applied.
            assert await _wallet(api, wa) == (10_000.0, 10.0)
            assert await _audit_credits(api, wa) == []

            # ── every gate refuses the NEXT call, with the not-a-payment-problem error ──
            with pytest.raises(BillingUnavailableError) as refused:
                await check_budget(wa, api)
            assert not isinstance(refused.value, InsufficientCreditsError)
            assert "top up" not in str(refused.value).lower()

            with pytest.raises(BillingUnavailableError):
                await reserve_credits(wa, 10.0, pool=api, raise_on_error=True)
            assert await reserve_credits(wa, 10.0, pool=api) is None

            # No scoped connection handed in: the marker read must open its own workspace scope,
            # because a raw pool connection under gtm_api sees no wallet row at all (FORCE RLS).
            assert await acheck_budget(api, wa, table="cost_records") is False

            async with workspace_scope(api, wa) as c:
                # fail_closed=True is the backend paid route; the DEFAULT (False) is the fail-open
                # read posture — a KNOWN failed debit must not be excused by it.
                assert (
                    await acheck_budget(api, wa, table="cost_records", conn=c, fail_closed=True)
                    is False
                )
                assert await acheck_budget(api, wa, table="cost_records", conn=c) is False
                assert await areserve_budget(c, wa, run_id=None, estimate=1.0) is None

            # ── positive control: the same gates admit a workspace with no failed debit ──
            assert await _marker(api, wb) is None
            assert await check_budget(wb, api) is True
            assert await reserve_credits(wb, 10.0, pool=api, raise_on_error=True)
            async with workspace_scope(api, wb) as c:
                assert (
                    await acheck_budget(api, wb, table="cost_records", conn=c, fail_closed=True)
                    is True
                )
                assert await areserve_budget(c, wb, run_id=None, estimate=1.0) is not None
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_the_marker_is_written_even_when_the_workspace_has_no_wallet_row(clean_db):
    """Upsert: a failed debit for a workspace with NO wallet row must still be recorded.

    Without it the marker UPDATE would match zero rows and the failure would vanish again — the
    exact shape of bug this task exists to close. Driven through the public path (meter_call).
    """

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=3)
        try:
            async with admin.acquire() as c:
                wa = await _register(c, "a@example.com")
                key = await _seed_key(c, wa, "sk-key-a")
                await c.execute("DELETE FROM workspace_wallets WHERE workspace_id = $1::uuid", wa)

            with pytest.raises(DebitFailedError):
                await meter_call(
                    workspace_id=wa,
                    api_key_id=key,
                    tool_name="draft_post",
                    profile_name="acme",
                    model="m",
                    prompt_tokens=1,
                    completion_tokens=1,
                    cost_usd=1.0,
                    pool=_FaultyPool(api, "debit"),
                )

            assert await _marker(api, wa) is not None
            with pytest.raises(BillingUnavailableError):
                await check_budget(wa, api)
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


@pytest.mark.parametrize("tool", ["draft_post", "draft_outreach"])
def test_draft_tools_return_their_draft_when_the_debit_fails_then_refuse_before_any_spend(
    clean_db, monkeypatch, tool
):
    """The live bug: draft_post/draft_outreach ignored settle_credits' False.

    The CURRENT call must still return its draft (the work is done and paid for — raising inside
    ``finally`` would mask the original error and drop a paid result). The marker is set, and the
    NEXT call of either tool refuses BEFORE it reaches the paid provider.
    """
    pytest.importorskip("mcp.server.fastmcp")
    from gtm_core.capabilities import Entitlement
    from mcp_server import server
    from mcp_server.auth import ApiKeyCtx

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=4)
        try:
            async with admin.acquire() as c:
                wa = await _seed_workspace(c, "a@example.com", credits=10_000.0)
                key_a = await _seed_key(c, wa, "sk-key-a")

            provider = AsyncMock(return_value=("Draft copy.", 100, 50, 0.002))
            monkeypatch.setattr(server, "_deepseek", provider)
            monkeypatch.setattr(
                server,
                "_require_profile",
                AsyncMock(
                    return_value=ApiKeyCtx(
                        key_id=key_a, workspace_id=wa, entitlement=Entitlement.PRO
                    )
                ),
            )

            # 1. The debit fails: the call still returns its draft, and the wallet is marked.
            monkeypatch.setattr(server._state, "pool", _FaultyPool(api, "debit"))
            assert await getattr(server, tool)(brief="b", profile="acme") == "Draft copy."
            assert provider.await_count == 1
            assert await _marker(api, wa) is not None

            # 2. A HEALTHY pool from here on — so a refusal can only come from the marker, not
            #    from the injector. Neither tool may reach the provider again.
            monkeypatch.setattr(server._state, "pool", api)
            provider.reset_mock()
            with pytest.raises(BillingUnavailableError):
                await server.draft_post(brief="b", profile="acme")
            with pytest.raises(BillingUnavailableError):
                await server.draft_outreach(brief="b", profile="acme")
            provider.assert_not_awaited()
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


# ── 1.4 the backend path ──────────────────────────────────────────────────────


@pytest.mark.parametrize("fault", ["debit", "debit-client"])
@pytest.mark.parametrize("reservation_flag", ["true", "false"])
def test_failed_run_settlement_marks_the_wallet_and_every_run_gate_refuses(
    clean_db, monkeypatch, reservation_flag, fault
):
    """``_settle_run_reservations`` used to end in ``except Exception: pass``.

    A failed settle left the run's real spend undebited AND its reservation open, with nothing to
    say so. It must now mark the wallet, and then ``_reserve_or_deny`` (flag on AND off — the
    flag-on path falls back to ``areserve_budget``, which must not admit a marked wallet),
    ``admits`` and ``_budget_guard`` all refuse. An unmarked workspace passes every one.
    """
    pytest.importorskip("backend.services.runs.pack_executor")
    from backend.services.runs.budget import _reserve_or_deny, _settle_run_reservations, admits
    from backend.services.runs.pack_executor import _budget_guard

    monkeypatch.setenv("COST_RESERVATION_ENABLED", reservation_flag)

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=4)
        try:
            async with admin.acquire() as c:
                wa = await _seed_workspace(c, "a@example.com", credits=10_000.0)
                wb = await _seed_workspace(c, "b@example.com", credits=10_000.0)
            a_run, a_next = await _seed_run(api, wa), await _seed_run(api, wa)
            b_run, b_next = await _seed_run(api, wb), await _seed_run(api, wb)

            # Positive control FIRST. With no fault, settling B's run really settles it: its $0.50
            # (500 credits) is debited, the reservation closes, nothing is marked. Without this the
            # refusals below could be passing because settlement is broken for EVERYONE — which
            # it was: the spend query compared cost_records.run_id (TEXT) with a ::uuid.
            assert await reserve_credits(wb, 2000.0, pool=api, run_id=b_run, raise_on_error=True)
            await _seed_run_spend(admin, wb, b_run, 0.5)
            await _settle_run_reservations(api, wb, b_run)
            assert await _marker(api, wb) is None
            assert await _wallet(api, wb) == (9_500.0, 9.5)
            assert await _reservation_states(api, wb) == ["settled"]

            # A holds an open reservation and has $0.50 of real spend; settling it must debit
            # 500 credits — and here that debit fails.
            assert await reserve_credits(wa, 2000.0, pool=api, run_id=a_run, raise_on_error=True)
            await _seed_run_spend(admin, wa, a_run, 0.5)

            await _settle_run_reservations(_FaultyPool(api, fault), wa, a_run)  # never raises

            assert await _marker(api, wa) is not None
            assert await _wallet(api, wa) == (10_000.0, 10.0)  # nothing half-applied
            assert await _reservation_states(api, wa) == ["open"]  # rolled back with the debit

            # A healthy pool from here: the refusal below can only come from the marker.
            assert await _reserve_or_deny(api, wa, a_next) is False
            assert await admits(api, wa, None, None) is False
            budget_ok, _verdicts, _cancelled = _budget_guard(api, wa, a_run, None, None)
            assert await budget_ok() is False

            # Positive control: the same three gates admit a workspace with no failed debit.
            assert await _reserve_or_deny(api, wb, b_next) is True
            assert await admits(api, wb, None, None) is True
            budget_ok, _verdicts, _cancelled = _budget_guard(api, wb, b_run, None, None)
            assert await budget_ok() is True
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())


def test_an_unreadable_reservation_read_marks_nothing(clean_db):
    """The settle runs for EVERY run that ends, and most hold no reservation at all. A transient error
    on its first read must not put a failed-debit marker — an operator-only lock — on a workspace
    that was never known to owe anything. (A failed DEBIT, with a reservation in hand, still marks:
    see test_failed_run_settlement_marks_the_wallet_and_every_run_gate_refuses.)"""
    pytest.importorskip("backend.services.runs.budget")
    from backend.services.runs.budget import _settle_run_reservations

    async def body():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=4)
        try:
            async with admin.acquire() as c:
                ws = await _seed_workspace(c, "a@example.com", credits=10_000.0)
            run = await _seed_run(api, ws)
            assert await reserve_credits(ws, 2000.0, pool=api, run_id=run, raise_on_error=True)

            await _settle_run_reservations(_FaultyPool(api, "read"), ws, run)  # never raises

            assert await _marker(api, ws) is None
            assert await _reservation_states(api, ws) == ["open"]  # still held; the sweep backstops
        finally:
            await api.close()
            await admin.close()

    asyncio.run(body())
