"""Live-Postgres proof of MM2 (decision D1-A): a run's worker spend reaches its wallet, counted once.

The MCP workers meter into the workspace's ``costs.jsonl``; the backend's brain meters into Postgres
``cost_records``. The two stores are disjoint, so a wallet debit that read only Postgres would charge
a tenant nothing for the providers it used. These checks run against a REAL Postgres as ``gtm_api``
with REAL files in a workspace tree — a mocked pool cannot see RLS, and a mocked ledger cannot see
what a torn append does to a total.

What is pinned:
  - settlement debits the run's Postgres spend PLUS the JSONL rows stamped with that run id, from every
    worker, and nobody else's (another run's rows, and rows naming no run, are not this run's debt);
  - it is counted ONCE: the month cap and the reservation each add the JSONL once, and
    ``acheck_budget`` and ``areserve_budget`` draw the admit/deny line at the same cent;
  - a ledger the backend cannot trust REFUSES for a tenant (fail-closed callers) and for settlement
    (which marks the wallet, MM3), but a torn FINAL line is tolerated, and the lenient default keeps
    its legacy reading.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

from gtm_core.db import workspace_scope  # noqa: E402
from gtm_core.metering import acheck_budget, areserve_budget, reserve_credits  # noqa: E402
from tests.backend._live_helpers import (  # noqa: E402
    marker,
    reservation_states,
    seed_cost_record,
    seed_run,
    seed_workspace,
    wallet,
    write_worker_ledger,
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _row(usd: float, run_id: str | None = None) -> dict:
    rec = {"ts": _now(), "tool": "worker", "cost_usd": usd}
    if run_id:
        rec["run_id"] = run_id
    return rec


@pytest.fixture
def workspaces_root(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(root))
    return root


def _with_pools(clean_db, body):
    async def run():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=4)
        try:
            await body(admin, api)
        finally:
            await api.close()
            await admin.close()

    asyncio.run(run())


# ── 4.2 settlement sees the workers' spend ────────────────────────────────────


def test_settlement_debits_the_runs_brain_spend_plus_every_workers_rows(clean_db, workspaces_root):
    pytest.importorskip("backend.services.runs.budget")
    from backend.services.runs.budget import _settle_run_reservations

    async def body(admin, api):
        async with admin.acquire() as c:
            ws = await seed_workspace(c, "a@example.com", credits=10_000.0)
        run, other = await seed_run(api, ws), await seed_run(api, ws)
        assert await reserve_credits(ws, 2000.0, pool=api, run_id=run, raise_on_error=True)
        await seed_cost_record(admin, ws, 0.5, run_id=run)  # the brain: $0.50
        # two DIFFERENT workers, two profile ledgers, one run: $0.20 + $0.10
        write_worker_ledger(workspaces_root, ws, "acme", [_row(0.2, run), _row(9.0, other)])
        write_worker_ledger(workspaces_root, ws, "beta", [_row(0.1, run), _row(7.0)])

        await _settle_run_reservations(api, ws, run)

        assert await marker(api, ws) is None
        # 0.50 + 0.20 + 0.10 = $0.80 = 800 credits. Not 500 (workers invisible), not 1100 (twice).
        assert await wallet(api, ws) == (9_200.0, 9.2)
        assert await reservation_states(api, ws) == ["settled"]

    _with_pools(clean_db, body)


def test_a_run_with_no_worker_rows_settles_exactly_as_before(clean_db, workspaces_root):
    """Positive control: no ledger tree at all (the VPS-shaped case) changes nothing."""
    pytest.importorskip("backend.services.runs.budget")
    from backend.services.runs.budget import _settle_run_reservations

    async def body(admin, api):
        async with admin.acquire() as c:
            ws = await seed_workspace(c, "a@example.com", credits=10_000.0)
        run = await seed_run(api, ws)
        assert await reserve_credits(ws, 2000.0, pool=api, run_id=run, raise_on_error=True)
        await seed_cost_record(admin, ws, 0.5, run_id=run)
        await _settle_run_reservations(api, ws, run)
        assert await wallet(api, ws) == (9_500.0, 9.5)
        assert await marker(api, ws) is None

    _with_pools(clean_db, body)


def test_settlement_cannot_read_the_workers_ledger_so_it_marks_the_wallet(
    clean_db, workspaces_root
):
    """The run's debt is unknown, so nothing is debited and the NEXT run is refused (MM3 marker)."""
    pytest.importorskip("backend.services.runs.budget")
    from backend.services.runs.budget import _reserve_or_deny, _settle_run_reservations

    async def body(admin, api):
        async with admin.acquire() as c:
            ws = await seed_workspace(c, "a@example.com", credits=10_000.0)
        run, nxt = await seed_run(api, ws), await seed_run(api, ws)
        assert await reserve_credits(ws, 2000.0, pool=api, run_id=run, raise_on_error=True)
        path = write_worker_ledger(workspaces_root, ws, "acme", [_row(0.2, run)])
        path.write_text(path.read_text() + "{ not json\n" + path.read_text(), encoding="utf-8")

        await _settle_run_reservations(api, ws, run)

        assert await marker(api, ws) is not None
        assert await wallet(api, ws) == (10_000.0, 10.0)  # nothing half-applied
        assert await reservation_states(api, ws) == ["open"]
        assert await _reserve_or_deny(api, ws, nxt) is False

    _with_pools(clean_db, body)


# ── 4.3 counted once, and the two guards agree ────────────────────────────────


@pytest.mark.parametrize(
    ("jsonl_usd", "cap_admits", "admit_estimate", "deny_estimate"),
    [
        (6.5, True, 0.5, 0.75),  # 3.0 (PG) + 6.5 = 9.5: room for exactly 0.5
        (0.0, True, 7.0, 7.5),  # no worker spend: the Postgres side alone, 3.0 of 10
        (7.0, False, None, 0.25),  # 3.0 + 7.0 = 10.0: at the cap, nothing more is admitted
    ],
)
def test_the_month_cap_and_the_reservation_count_both_stores_once_and_agree(
    clean_db, workspaces_root, jsonl_usd, cap_admits, admit_estimate, deny_estimate
):
    async def body(admin, api):
        async with admin.acquire() as c:
            ws = await seed_workspace(c, "a@example.com", credits=10_000.0, cap_usd=10.0)
        await seed_cost_record(admin, ws, 3.0)  # Postgres: the brain
        if jsonl_usd:
            write_worker_ledger(workspaces_root, ws, "acme", [_row(jsonl_usd)])

        async with workspace_scope(api, ws) as c:
            assert (
                await acheck_budget(api, ws, table="cost_records", conn=c, fail_closed=True)
                is cap_admits
            )
        if admit_estimate is not None:
            async with workspace_scope(api, ws) as c:
                assert await areserve_budget(c, ws, run_id=None, estimate=admit_estimate), (
                    "the reservation counts less spend than the cap read does"
                )
            await admin.execute("DELETE FROM cost_reservations WHERE workspace_id = $1::uuid", ws)
        async with workspace_scope(api, ws) as c:
            assert await areserve_budget(c, ws, run_id=None, estimate=deny_estimate) is None, (
                "the reservation counts more or less spend than the cap read does"
            )

    _with_pools(clean_db, body)


# ── 4.4 an untrustworthy ledger refuses; a torn tail does not ─────────────────


def test_a_tenant_budget_read_refuses_a_corrupt_interior_line_but_the_default_does_not(
    clean_db, workspaces_root
):
    async def body(admin, api):
        async with admin.acquire() as c:
            ws = await seed_workspace(c, "a@example.com", credits=10_000.0, cap_usd=10.0)
        path = write_worker_ledger(workspaces_root, ws, "acme", [_row(1.0)])
        path.write_text("{ not json\n" + path.read_text() + path.read_text(), encoding="utf-8")

        async with workspace_scope(api, ws) as c:
            # Positive control: the SAME ledger, read the legacy way, is still readable and under cap.
            assert await acheck_budget(api, ws, table="cost_records", conn=c) is True
            # The tenant posture refuses: a skipped line is spend missing from the total.
            assert (
                await acheck_budget(api, ws, table="cost_records", conn=c, fail_closed=True)
                is False
            )
            assert await areserve_budget(c, ws, run_id=None, estimate=0.1) is None

    _with_pools(clean_db, body)


def test_a_torn_final_line_does_not_brick_a_tenant(clean_db, workspaces_root):
    """A worker killed mid-append leaves a partial last line. The tenant must still be billable."""

    async def body(admin, api):
        async with admin.acquire() as c:
            ws = await seed_workspace(c, "a@example.com", credits=10_000.0, cap_usd=10.0)
        write_worker_ledger(workspaces_root, ws, "acme", [_row(1.0)], raw_tail='{"ts": "2026-1')

        async with workspace_scope(api, ws) as c:
            assert (
                await acheck_budget(api, ws, table="cost_records", conn=c, fail_closed=True) is True
            )
            assert await areserve_budget(c, ws, run_id=None, estimate=0.1)

    _with_pools(clean_db, body)
