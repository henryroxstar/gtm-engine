"""Live-Postgres proof of MM12: MCP ``dispatch`` reserves wallet credit BEFORE it enqueues.

Before this, ``dispatch`` checked ``admits`` (a read) and wrote a ``queued`` run holding nothing, so
N queued runs could each pass the read and the wallet would be spent N times over. The reservation is
now taken in the SAME transaction as the run row: a wallet that cannot cover it refuses the dispatch
and the row rolls back with it — a refusal leaves nothing behind.

Everything here drives the REAL ``dispatch_task`` as a service principal (a real ``sk-`` key bound to a
real agent, a real profile on disk, a real Postgres as the non-owner ``gtm_api``). Seeding a run row and
reserving by hand would test the helpers, not the tool — and the tool had never been called: it passed
``insert_run_row`` keywords that function does not accept, so every MCP dispatch raised ``TypeError``
until this change. A reach test is the only thing that could have seen that.

What is pinned:
  - an empty or failed-debit wallet refuses the dispatch and NO run row exists afterwards;
  - a funded wallet queues exactly ONE run holding exactly ONE open reservation for that run;
  - the executor's own pre-spend gate (start, and again after an approved gate) ADOPTS that
    reservation instead of inserting a second one (a second open one is a constraint error, which the
    reservation code reads as "deny" — so every gated run would fail ``cost_cap_reached``);
  - a run cancelled or failed before any worker claims it leaves no open reservation behind;
  - replaying a ``client_request_id``, even concurrently, never reserves twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

from gtm_core.db import workspace_scope  # noqa: E402
from tests.backend._live_helpers import (  # noqa: E402
    marker,
    reservation_states,
    seed_workspace,
    wallet,
)

REPO = Path(__file__).resolve().parents[2]
PACK, VARIANT = "marketing", "linkedin-post"


def _imports():
    """The heavy imports, skipped (not errored) where the minimal CI job lacks them."""
    pytest.importorskip("mcp")
    pytest.importorskip("slowapi")
    from backend.services.runs import budget
    from backend.services.runs.decisions import cancel
    from mcp_server.tasks import dispatch_task
    from tests.backend.test_packs_api import PROFILE, _provision

    return SimpleNamespace(
        dispatch_task=dispatch_task,
        budget=budget,
        cancel=cancel,
        PROFILE=PROFILE,
        provision=_provision,
    )


async def _seed(admin, workspaces_root: Path, x, *, credits: float, email="a@example.com"):
    """A workspace with a wallet, an active agent, a key bound to it, and a profile on disk."""
    async with admin.acquire() as c:
        ws = await seed_workspace(c, email, credits=credits)
        await c.execute(
            "UPDATE subscriptions SET entitlement = 'pro_plus' WHERE workspace_id = $1::uuid", ws
        )
        agent = await c.fetchval(
            "INSERT INTO agents(workspace_id, name, profile_name) "
            "VALUES($1::uuid, 'fleet-agent', $2) RETURNING id::text",
            ws,
            x.PROFILE,
        )
        raw = f"sk-{uuid.uuid4().hex}"
        await c.execute(
            "INSERT INTO api_keys(workspace_id, key_hash, prefix, entitlement, agent_id) "
            "VALUES($1::uuid, $2, $3, 'pro_plus', $4::uuid)",
            ws,
            hashlib.sha256(raw.encode()).hexdigest(),
            raw[:8],
            agent,
        )
    x.provision(workspaces_root / ws / "profiles", packs_toml='active = ["marketing"]\n')
    return ws, raw


async def _runs(api, ws: str) -> list[dict]:
    async with workspace_scope(api, ws) as c:
        rows = await c.fetch(
            "SELECT id::text AS id, status FROM runs WHERE workspace_id = $1::uuid", ws
        )
    return [dict(r) for r in rows]


async def _reservations(api, ws: str) -> list[dict]:
    async with workspace_scope(api, ws) as c:
        rows = await c.fetch(
            "SELECT run_id::text AS run_id, state, estimated_usd FROM cost_reservations "
            "WHERE workspace_id = $1::uuid",
            ws,
        )
    return [dict(r) for r in rows]


def _live(clean_db, tmp_path, monkeypatch, body):
    workspaces = tmp_path / "workspaces"
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(workspaces))
    x = _imports()

    async def run():
        from backend.database import create_pool

        admin = await asyncpg.create_pool(clean_db["admin_dsn"], min_size=1, max_size=2)
        api = await create_pool(clean_db["api_dsn"], min_size=1, max_size=6)
        try:
            await body(admin, api, workspaces, x)
        finally:
            await api.close()
            await admin.close()

    asyncio.run(run())


async def _dispatch(x, api, raw, monkeypatch, **kw):
    monkeypatch.setenv(
        "MCP_API_KEY", raw
    )  # the stdio fallback: no HTTP context to read a header from
    return await x.dispatch_task(
        PACK, VARIANT, {}, pool=api, cfg=SimpleNamespace(repo_root=REPO), **kw
    )


# ── (a) a wallet that cannot cover it refuses, and leaves nothing ─────────────


def test_an_empty_wallet_refuses_the_dispatch_and_no_run_row_exists(
    clean_db, tmp_path, monkeypatch
):
    async def body(admin, api, workspaces, x):
        ws, raw = await _seed(admin, workspaces, x, credits=0.0)

        with pytest.raises(ValueError):
            await _dispatch(x, api, raw, monkeypatch)

        assert await _runs(api, ws) == []  # the row rolled back with the refusal
        assert await _reservations(api, ws) == []

    _live(clean_db, tmp_path, monkeypatch, body)


def test_a_wallet_with_a_failed_debit_marker_refuses_the_dispatch(clean_db, tmp_path, monkeypatch):
    async def body(admin, api, workspaces, x):
        ws, raw = await _seed(admin, workspaces, x, credits=50_000.0)
        await admin.execute(
            "UPDATE workspace_wallets SET debit_failed_at = now() WHERE workspace_id = $1::uuid", ws
        )
        # the refusal must READ as a cap refusal, not crash while composing its own sentence
        with pytest.raises(ValueError, match="cost_cap_reached"):
            await _dispatch(x, api, raw, monkeypatch)
        assert await _runs(api, ws) == []
        assert await marker(api, ws) is not None

    _live(clean_db, tmp_path, monkeypatch, body)


# ── (b) a funded wallet: one run, one reservation, before AND during execution ─


@pytest.mark.parametrize("flag", ["true", "false"])
def test_a_funded_dispatch_holds_exactly_one_reservation_and_the_executor_adopts_it(
    clean_db, tmp_path, monkeypatch, flag
):
    monkeypatch.setenv("COST_RESERVATION_ENABLED", flag)

    async def body(admin, api, workspaces, x):
        ws, raw = await _seed(admin, workspaces, x, credits=50_000.0)

        out = await _dispatch(x, api, raw, monkeypatch)

        run_id = out["run_id"]
        assert out["status"] == "queued"
        assert [r["id"] for r in await _runs(api, ws)] == [run_id]
        held = await _reservations(api, ws)
        assert [(r["run_id"], r["state"]) for r in held] == [(run_id, "open")]

        # The executor's start gate and its re-gate after an approved gate both run
        # _reserve_or_deny. Each must ADOPT the dispatch reservation: still exactly one, still open.
        assert await x.budget._reserve_or_deny(api, ws, run_id) is True
        assert await x.budget._reserve_or_deny(api, ws, run_id) is True
        held = await _reservations(api, ws)
        assert [(r["run_id"], r["state"]) for r in held] == [(run_id, "open")]

    _live(clean_db, tmp_path, monkeypatch, body)


def test_a_marker_set_after_dispatch_still_stops_the_adopting_executor(
    clean_db, tmp_path, monkeypatch
):
    """Adopting is a re-check, not a free pass: the run must not spend once the wallet is unreconciled."""
    monkeypatch.setenv("COST_RESERVATION_ENABLED", "true")

    async def body(admin, api, workspaces, x):
        ws, raw = await _seed(admin, workspaces, x, credits=50_000.0)
        run_id = (await _dispatch(x, api, raw, monkeypatch))["run_id"]
        assert await x.budget._reserve_or_deny(api, ws, run_id) is True
        await admin.execute(
            "UPDATE workspace_wallets SET debit_failed_at = now() WHERE workspace_id = $1::uuid", ws
        )
        assert await x.budget._reserve_or_deny(api, ws, run_id) is False

    _live(clean_db, tmp_path, monkeypatch, body)


def test_a_run_with_no_dispatch_reservation_still_reserves_at_start_and_adopts_after_a_gate(
    clean_db, tmp_path, monkeypatch
):
    """The REST/prompt path: nothing reserved at creation, so the first gate reserves, the second adopts.
    With the flag on this is the shape every GATED run has — before the adopt rule the second call
    inserted a second open reservation, hit the one-per-run index, and failed the run cost_cap_reached."""
    monkeypatch.setenv("COST_RESERVATION_ENABLED", "true")

    async def body(admin, api, workspaces, x):
        ws, _raw = await _seed(admin, workspaces, x, credits=50_000.0)
        async with workspace_scope(api, ws) as c:
            run_id = await c.fetchval(
                "INSERT INTO runs(workspace_id, profile_name, prompt) "
                "VALUES($1::uuid, 'p', 'x') RETURNING id::text",
                ws,
            )
        assert await x.budget._reserve_or_deny(api, ws, run_id) is True  # start gate: reserves
        assert await x.budget._reserve_or_deny(api, ws, run_id) is True  # after the gate: adopts
        held = await _reservations(api, ws)
        assert [(r["run_id"], r["state"]) for r in held] == [(run_id, "open")]

    _live(clean_db, tmp_path, monkeypatch, body)


# ── (c) nothing leaks when a queued run never reaches an executor ─────────────


def test_a_run_cancelled_while_queued_leaves_no_open_reservation(clean_db, tmp_path, monkeypatch):
    async def body(admin, api, workspaces, x):
        ws, raw = await _seed(admin, workspaces, x, credits=50_000.0)
        run_id = (await _dispatch(x, api, raw, monkeypatch))["run_id"]
        assert [r["state"] for r in await _reservations(api, ws)] == ["open"]

        await x.cancel(api, ws, run_id)

        assert "open" not in [r["state"] for r in await _reservations(api, ws)]
        assert await wallet(api, ws) == (
            50_000.0,
            50.0,
        )  # a run that spent nothing is debited nothing

    _live(clean_db, tmp_path, monkeypatch, body)


def test_a_run_failed_before_it_was_claimed_leaves_no_open_reservation(
    clean_db, tmp_path, monkeypatch
):
    """The claim loop fails a run (retries spent, fakes off, a restart, no session store) BEFORE any task
    exists to settle it. Driven through the REAL ``dispatch_claimed`` with a row that has used up its
    attempts — not by calling a helper — so the hook in the claim loop itself is what is proven."""

    async def body(admin, api, workspaces, x):
        from backend.services.runs.queue import MAX_ATTEMPTS, dispatch_claimed

        ws, raw = await _seed(admin, workspaces, x, credits=50_000.0)
        run_id = (await _dispatch(x, api, raw, monkeypatch))["run_id"]
        assert [r["state"] for r in await _reservations(api, ws)] == ["open"]

        claimed = {
            "id": run_id,
            "workspace_id": ws,
            "payload": {"mode": "pack", "pack": PACK, "variant": VARIANT, "inputs": {}},
            "prev_status": "queued",
            "attempts": MAX_ATTEMPTS + 1,
            "agent_id": None,
            "profile_name": x.PROFILE,
            "prompt": "",
        }
        assert await dispatch_claimed(api, REPO, None, claimed) is False

        assert "open" not in [r["state"] for r in await _reservations(api, ws)]
        assert [r["status"] for r in await _runs(api, ws)] == ["failed"]

    _live(clean_db, tmp_path, monkeypatch, body)


# ── (d) idempotent replay never reserves twice ────────────────────────────────


def test_replaying_a_client_request_id_returns_the_run_and_reserves_once(
    clean_db, tmp_path, monkeypatch
):
    async def body(admin, api, workspaces, x):
        ws, raw = await _seed(admin, workspaces, x, credits=50_000.0)
        first = await _dispatch(x, api, raw, monkeypatch, client_request_id="req-1")
        again = await _dispatch(x, api, raw, monkeypatch, client_request_id="req-1")
        assert first["run_id"] == again["run_id"]
        assert len(await _reservations(api, ws)) == 1

    _live(clean_db, tmp_path, monkeypatch, body)


def test_two_concurrent_dispatches_of_one_client_request_id_reserve_once(
    clean_db, tmp_path, monkeypatch
):
    """Both pass the fast-path replay check (neither run exists yet); the loser of the insert race must
    take no reservation of its own."""

    async def body(admin, api, workspaces, x):
        ws, raw = await _seed(admin, workspaces, x, credits=50_000.0)
        a, b = await asyncio.gather(
            _dispatch(x, api, raw, monkeypatch, client_request_id="req-2"),
            _dispatch(x, api, raw, monkeypatch, client_request_id="req-2"),
        )
        assert a["run_id"] == b["run_id"]
        assert [r["state"] for r in await _reservations(api, ws)] == ["open"]
        assert len(await _runs(api, ws)) == 1

    _live(clean_db, tmp_path, monkeypatch, body)


def test_the_credit_held_is_the_estimate_not_the_whole_wallet(clean_db, tmp_path, monkeypatch):
    """Two dispatches fit in a wallet that covers two estimates and a third is refused: the
    reservations stack, which is the whole point — N queued runs can no longer all pass a read."""

    async def body(admin, api, workspaces, x):
        estimate = x.budget.RESERVATION_ESTIMATE_CREDITS
        ws, raw = await _seed(admin, workspaces, x, credits=estimate * 2.5)
        await _dispatch(x, api, raw, monkeypatch)
        await _dispatch(x, api, raw, monkeypatch)
        with pytest.raises(ValueError):
            await _dispatch(x, api, raw, monkeypatch)
        assert len(await _runs(api, ws)) == 2
        assert [r["state"] for r in await _reservations(api, ws)] == ["open", "open"]
        assert await reservation_states(api, ws) == ["open", "open"]

    _live(clean_db, tmp_path, monkeypatch, body)


@pytest.mark.parametrize("flag", ["true", "false"])
def test_a_dispatched_run_that_ends_is_settled_and_debited_whatever_the_flag_says(
    clean_db, tmp_path, monkeypatch, flag
):
    """The done-callback used to settle only with COST_RESERVATION_ENABLED on, so a reservation taken at
    dispatch with it off would have stayed open for ever and the run's spend never reached the wallet."""
    monkeypatch.setenv("COST_RESERVATION_ENABLED", flag)

    async def body(admin, api, workspaces, x):
        from tests.backend._live_helpers import seed_cost_record

        ws, raw = await _seed(admin, workspaces, x, credits=50_000.0)
        run_id = (await _dispatch(x, api, raw, monkeypatch))["run_id"]
        await seed_cost_record(admin, ws, 0.5, run_id=run_id)  # what the run spent

        async def work():
            return None

        task = x.budget._track_run(asyncio.create_task(work()), api, ws, run_id)
        await task
        for _ in range(50):  # the settle is fire-and-forget from the done-callback
            if "open" not in [r["state"] for r in await _reservations(api, ws)]:
                break
            await asyncio.sleep(0.05)

        assert [r["state"] for r in await _reservations(api, ws)] == ["settled"]
        assert await wallet(api, ws) == (49_500.0, 49.5)  # 500 credits, the run's actual spend

    _live(clean_db, tmp_path, monkeypatch, body)


def test_a_reservation_that_comes_back_empty_refuses_instead_of_reporting_a_run_that_is_not_there(
    clean_db, tmp_path, monkeypatch
):
    """reserve_credits can answer None (a database error under fail_closed) rather than raise — and the
    transaction it ran in is then aborted, so the run row will not commit. Reporting ``queued`` for it
    would hand the caller a run id that does not exist."""

    async def body(admin, api, workspaces, x):
        import mcp_server.tasks as tasks

        async def empty(*_a, **_k):
            return None

        monkeypatch.setattr(tasks, "reserve_credits", empty)
        ws, raw = await _seed(admin, workspaces, x, credits=50_000.0)
        with pytest.raises(ValueError, match="cost_cap_reached"):
            await _dispatch(x, api, raw, monkeypatch)
        assert await _runs(api, ws) == []

    _live(clean_db, tmp_path, monkeypatch, body)


def test_a_gated_run_that_cannot_be_resumed_after_a_restart_releases_its_reservation(
    clean_db, tmp_path, monkeypatch
):
    """Startup reconciliation fails a stranded gated run it cannot resume. Like the claim loop, there
    is no task to settle it, and it HAS spent (up to its gate) — releasing it unsettled would hand that
    spend over free, so it must be settled, i.e. debited, as it is released."""

    async def body(admin, api, workspaces, x):
        from backend.services.runs.reconcile import reconcile_gates
        from tests.backend._live_helpers import seed_cost_record

        ws, raw = await _seed(admin, workspaces, x, credits=50_000.0)
        run_id = (await _dispatch(x, api, raw, monkeypatch))["run_id"]
        # the run got as far as its gate, having spent $0.40, then the process restarted; its job
        # record is gone, so it cannot be resumed
        await admin.execute(
            "UPDATE runs SET status = 'awaiting_approval', payload = NULL, prompt = 'x' "
            "WHERE id = $1::uuid",
            run_id,
        )
        await seed_cost_record(admin, ws, 0.4, run_id=run_id)

        await reconcile_gates(api, REPO)

        assert [r["status"] for r in await _runs(api, ws)] == ["failed"]
        assert [r["state"] for r in await _reservations(api, ws)] == ["settled"]
        assert await wallet(api, ws) == (49_600.0, 49.6)  # the 400 credits it spent were collected

    _live(clean_db, tmp_path, monkeypatch, body)
