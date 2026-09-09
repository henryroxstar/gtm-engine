"""A5 step 1 — durable gates: the run_gates row persists BOTH the wait and the
decision, so a restart mid-gate no longer kills the run and a decision posted
while the runner is down is applied on resume rather than lost.

The DB is a stateful in-memory fake (GateDb) implementing the exact upsert/claim
SQL, so the single-consumption and restart shapes are testable without Postgres
(the live tier covers the real engine). Convention: no pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from backend.routers import runs as runs_router  # noqa: E402
from backend.services.runs import decisions as runs_decisions  # noqa: E402
from backend.services.runs import events as runs_events  # noqa: E402
from backend.services.runs import executor as runs_executor  # noqa: E402
from backend.services.runs import lifecycle as runs_lifecycle  # noqa: E402
from backend.services.runs import pack_executor as runs_pack_executor  # noqa: E402
from backend.services.runs import queue as runs_queue  # noqa: E402
from backend.services.runs import reconcile as runs_reconcile  # noqa: E402
from tests.backend._protocol1 import (  # noqa: E402  # noqa: E402
    REPO,
    SCOPE_MODULES,
    fake_broker,
    fake_executor,
    pack_run_harness,
    patch_everywhere,
)
from tests.backend.test_packs_api import PROFILE, _provision  # noqa: E402

WS_ID = str(uuid.uuid4())


class GateDb:
    """run_gates + runs, with the routers' ON CONFLICT / claim semantics."""

    def __init__(self) -> None:
        self.gates: dict[tuple[str, str, str], dict] = {}
        self.run_status: list[str] = []
        self.runs: list[dict] = []

    async def execute(self, sql: str, *args):
        if "INSERT INTO run_gates" in sql:
            run_id, gate, node_id, ws, sha = args
            self.gates[(run_id, gate, node_id)] = {
                "run_id": run_id,
                "gate": gate,
                "node_id": node_id,
                "workspace_id": ws,
                "content_sha": sha,
                "state": "open",
                "edited_content": None,
                "decided_at": None,
                "applied_at": None,
            }
            return
        match = re.search(r"UPDATE runs\s+SET status = '(\w+)'", sql)
        if match:
            self.run_status.append(match.group(1))
        return

    async def fetchrow(self, sql: str, *args):
        if "UPDATE run_gates SET state" in sql:  # _record_gate_decision
            run_id, _ws, state, edited = args
            for key, row in self.gates.items():
                if key[0] == run_id and row["state"] == "open":
                    row.update(state=state, edited_content=edited, decided_at="now")
                    return {"gate": key[1], "node_id": key[2]}
            return None
        if "UPDATE run_gates SET applied_at" in sql:  # _claim_gate_decision
            run_id, _ws, gate, node_id = args
            row = self.gates.get((run_id, gate, node_id))
            if row is None or row["state"] == "open" or row["applied_at"] is not None:
                return None
            row["applied_at"] = "now"
            return {"state": row["state"], "edited_content": row["edited_content"]}
        return None

    async def fetch(self, sql: str, *args):
        # A4/V018: reconcile now reads via the SECURITY DEFINER awaiting_approval_runs()
        # (a bare SELECT would see 0 rows under FORCE RLS on the runtime role).
        if "awaiting_approval_runs()" in sql:
            return self.runs
        return []

    async def fetchval(self, sql: str, *args):
        return None


@asynccontextmanager
async def _scope(pool, workspace_id):
    yield _scope.db


@pytest.fixture(autouse=True)
def _clean_state():
    yield
    runs_router._gate_events.clear()
    runs_router._gate_decisions.clear()
    runs_router._cancelled_runs.clear()


async def _run_pack(ws_env, db, run_id, executor):
    """Await INSIDE the patch context — returning the coroutine would let the
    patches unwind before the run ever executes."""
    _scope.db = db
    with (
        pack_run_harness(db, executor),
        patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
    ):
        await runs_router._execute_pack_run(
            MagicMock(),
            REPO,
            ws_env.ws_id,
            run_id,
            PROFILE,
            "marketing",
            "linkedin-post",
            {},
            entitlement="pro_plus",
        )


def _provision_gated(ws_env):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    pending = ws_env.content_root / PROFILE / "plans" / ".pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "2026-32.draft.json").write_text("[]")


async def _await_gate_row(db, run_id: str, ticks: int = 800) -> bool:
    for _ in range(ticks):
        if any(k[0] == run_id for k in db.gates):
            return True
        await asyncio.sleep(0.005)
    return False


def test_open_gate_row_written_when_run_pauses(ws_env):
    """The WAIT is durable: pausing at Gate 1 writes an `open` row carrying the
    sha of the exact bytes the operator will be shown."""
    _provision_gated(ws_env)
    run_id = str(uuid.uuid4())
    db = GateDb()

    async def _go():
        task = asyncio.create_task(
            _run_pack(ws_env, db, run_id, fake_executor([], awaiting_on="plan"))
        )
        assert await _await_gate_row(db, run_id), "no gate row was persisted"
        row = next(v for k, v in db.gates.items() if k[0] == run_id)
        assert (row["gate"], row["node_id"], row["state"]) == ("plan", "plan", "open")
        assert row["content_sha"] == runs_router._content_sha("[]")
        async with runs_router._state_lock:  # resolve so the task finishes
            runs_router._gate_decisions[run_id] = {"decision": "reject", "edited_content": None}
            runs_router._gate_events[run_id].set()
        await asyncio.wait_for(task, timeout=10)

    asyncio.run(_go())


def test_decision_recorded_durably_then_claimed_once(ws_env):
    """The DECISION is durable and single-consumption: a second claim returns None."""
    db = GateDb()
    _scope.db = db
    run_id = str(uuid.uuid4())
    db.gates[(run_id, "plan", "plan")] = {
        "run_id": run_id,
        "gate": "plan",
        "node_id": "plan",
        "workspace_id": WS_ID,
        "content_sha": "abc",
        "state": "open",
        "edited_content": None,
        "decided_at": None,
        "applied_at": None,
    }

    async def _go():
        recorded = await runs_router._record_gate_decision(db, WS_ID, run_id, "edit", "new bytes")
        # A second write finds no `open` row — the durable single-consumption guard.
        second = await runs_router._record_gate_decision(db, WS_ID, run_id, "approve", None)
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope):
            first_claim = await runs_router._claim_gate_decision(
                MagicMock(), WS_ID, run_id, "plan", "plan"
            )
            second_claim = await runs_router._claim_gate_decision(
                MagicMock(), WS_ID, run_id, "plan", "plan"
            )
        return recorded, second, first_claim, second_claim

    recorded, second, first_claim, second_claim = asyncio.run(_go())
    assert recorded is True and second is False
    assert first_claim == {"decision": "edit", "edited_content": "new bytes"}
    assert second_claim is None, "a claimed decision must never be applied twice"


def test_decision_posted_while_runner_is_down_is_applied_on_resume(ws_env):
    """The A5 acceptance case. The decision lands with NO in-process waiter (the
    runner is 'down'); when the run resumes it claims the row and completes —
    without ever re-opening the gate or waiting."""
    _provision_gated(ws_env)
    run_id = str(uuid.uuid4())
    db = GateDb()
    _scope.db = db
    # A decided row left behind by the operator while the process was restarting.
    db.gates[(run_id, "plan", "plan")] = {
        "run_id": run_id,
        "gate": "plan",
        "node_id": "plan",
        "workspace_id": ws_env.ws_id,
        "content_sha": "abc",
        "state": "approved",
        "edited_content": None,
        "decided_at": "then",
        "applied_at": None,
    }

    asyncio.run(_run_pack(ws_env, db, run_id, fake_executor([], awaiting_on="plan")))

    assert db.gates[(run_id, "plan", "plan")]["applied_at"] is not None
    assert db.run_status[-1] == "ok", "the resumed run must complete, not hang"
    assert run_id not in runs_router._gate_events, "no new wait should have been opened"


def test_reopening_a_gate_clears_the_prior_decision(ws_env):
    """A re-opened gate resets state/applied_at, so a stale decision can never be
    re-applied to a LATER gate of the same node."""
    db = GateDb()
    _scope.db = db
    run_id = str(uuid.uuid4())
    key = (run_id, "plan", "plan")
    db.gates[key] = {
        "run_id": run_id,
        "gate": "plan",
        "node_id": "plan",
        "workspace_id": WS_ID,
        "content_sha": "old",
        "state": "approved",
        "edited_content": "x",
        "decided_at": "then",
        "applied_at": "then",
    }

    async def _go():
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope):
            await runs_router._open_gate_row(MagicMock(), WS_ID, run_id, "plan", "plan", "new")
            return await runs_router._claim_gate_decision(
                MagicMock(), WS_ID, run_id, "plan", "plan"
            )

    claimed = asyncio.run(_go())
    assert claimed is None, "a re-opened gate must not surface the prior decision"
    assert db.gates[key]["state"] == "open" and db.gates[key]["content_sha"] == "new"


def test_malformed_gate_row_reads_as_no_decision(ws_env):
    """Fail-safe: an unrecognised state must NOT resolve the gate (never an
    accidental auto-approve) — it reads as 'no decision yet'."""
    db = GateDb()
    _scope.db = db
    run_id = str(uuid.uuid4())
    db.gates[(run_id, "plan", "plan")] = {
        "run_id": run_id,
        "gate": "plan",
        "node_id": "plan",
        "workspace_id": WS_ID,
        "content_sha": "abc",
        "state": "weird-state",
        "edited_content": None,
        "decided_at": "then",
        "applied_at": None,
    }

    async def _go():
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope):
            return await runs_router._claim_gate_decision(
                MagicMock(), WS_ID, run_id, "plan", "plan"
            )

    assert asyncio.run(_go()) is None


def test_reconcile_resumes_pack_runs_and_fails_prompt_runs(ws_env):
    """Startup reconciliation: a stranded PACK run is re-dispatched (its durable
    manifest resumes it); a prompt-mode run — whose SDK session died with the
    process — is failed explicitly instead of hanging forever."""
    db = GateDb()
    _scope.db = db
    pack_run, prompt_run = str(uuid.uuid4()), str(uuid.uuid4())
    db.runs = [
        {
            "id": pack_run,
            "workspace_id": ws_env.ws_id,
            "profile_name": PROFILE,
            "prompt": '[pack] marketing/linkedin-post inputs={"brand_name": "ExampleCo"}',
            "agent_id": None,
        },
        {
            "id": prompt_run,
            "workspace_id": ws_env.ws_id,
            "profile_name": PROFILE,
            "prompt": "run market-scan",
            "agent_id": None,
        },
    ]

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield db

    pool.acquire = _acquire
    dispatched: list[tuple] = []

    async def _record_dispatch(*args, **kwargs):
        dispatched.append((args, kwargs))

    failed: list[tuple] = []

    async def _record_fail(pool_, ws, rid, err):
        failed.append((rid, err))

    async def _go():
        with (
            patch.object(runs_reconcile, "_execute_pack_run", _record_dispatch),
            patch.object(runs_reconcile, "_fail_run", _record_fail),
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
        ):
            resumed = await runs_router.reconcile_gates(pool, REPO)
            await asyncio.sleep(0)  # let the created task run
            return resumed

    resumed = asyncio.run(_go())

    assert resumed == 1
    assert len(dispatched) == 1
    args, _kwargs = dispatched[0]
    assert args[3] == pack_run and args[5] == "marketing" and args[6] == "linkedin-post"
    assert args[7] == {"brand_name": "ExampleCo"}, "inputs must survive the round trip"
    assert [rid for rid, _ in failed] == [prompt_run]
    assert "restart" in failed[0][1]


def test_reconcile_tolerates_a_read_failure(ws_env):
    """Reconciliation must never block boot."""

    @asynccontextmanager
    async def _boom():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    pool = MagicMock()
    pool.acquire = _boom
    assert asyncio.run(runs_router.reconcile_gates(pool, REPO)) == 0


def test_pack_prompt_round_trip_regex():
    """The reconciliation parser is bound to the exact audit line create_run writes."""
    inputs = {"brand_name": "ExampleCo", "topic": "a/b test"}
    line = f"[pack] marketing/linkedin-post inputs={json.dumps(inputs, sort_keys=True)}"
    m = runs_router._PACK_PROMPT_RE.match(line)
    assert m is not None
    assert (m.group("pack"), m.group("variant")) == ("marketing", "linkedin-post")
    assert json.loads(m.group("inputs")) == inputs
    assert runs_router._PACK_PROMPT_RE.match("run market-scan") is None


# ── A5 step 2: the durable run queue ──────────────────────────────────────────


class QueueDb(GateDb):
    """GateDb + `runs` rows and the V021 claim.

    ``claim`` models what Postgres guarantees — one caller gets the row, and the
    predicate is LIVENESS-based — so these tests pin the CALLER's behaviour around it.
    That `FOR UPDATE SKIP LOCKED` really is atomic on a real engine is a property only
    the live tier can prove (test_rls_live.py::test_claim_next_run_hands_a_row_to_one_worker).
    """

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict] = []
        self.failed: list[tuple[str, str]] = []

    def add_run(self, **over) -> dict:
        row = {
            "id": str(uuid.uuid4()),
            "workspace_id": WS_ID,
            "profile_name": "acme",
            "prompt": "do the thing",
            "payload": {"mode": "pack", "pack": "marketing", "variant": "linkedin-post"},
            "agent_id": None,
            "status": "queued",
            "heartbeat_at": None,
            "attempts": 0,
            "claimed_by": None,
        }
        row.update(over)
        self.rows.append(row)
        return row

    def claim(self, worker_id: str, lease_s: int) -> dict | None:
        for row in self.rows:
            stale = (
                row["status"] in ("running", "awaiting_approval")
                and row["heartbeat_at"] is not None
                and row["heartbeat_at"] < -lease_s
            )
            if row["status"] != "queued" and not stale:
                continue
            prev = row["status"]
            row["claimed_by"] = worker_id
            row["heartbeat_at"] = 0
            row["attempts"] += 1
            if prev == "queued":
                row["status"] = "running"
            return {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "profile_name": row["profile_name"],
                "prompt": row["prompt"],
                "payload": row["payload"],
                "agent_id": row["agent_id"],
                "prev_status": prev,
                "attempts": row["attempts"],
            }
        return None

    async def fetchrow(self, sql: str, *args):
        if "claim_next_run" in sql:
            return self.claim(args[0], args[1])
        return await super().fetchrow(sql, *args)


@asynccontextmanager
async def _acquire(db):
    yield db


def _pool_for(db):
    pool = MagicMock()
    pool.acquire = lambda: _acquire(db)
    return pool


@contextmanager
def _queue_harness(db, dispatched: list, failed: list):
    """Patch the two executors at their DEFINING modules (dispatch_claimed imports them
    lazily, so a patch on the queue module would never be seen) plus _fail_run."""

    async def _record_pack(*args, **kwargs):
        dispatched.append(("pack", args, kwargs))

    async def _record_prompt(*args, **kwargs):
        dispatched.append(("prompt", args, kwargs))

    async def _record_fail(_pool, _ws, run_id, error):
        failed.append((run_id, error))

    _scope.db = db
    with (
        patch.object(runs_pack_executor, "_execute_pack_run", _record_pack),
        patch.object(runs_executor, "_execute_run", _record_prompt),
        patch.object(runs_queue, "_fail_run", _record_fail),
        patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
    ):
        yield


def test_a_queued_run_is_claimed_and_dispatched_exactly_once():
    """T1 — the headline acceptance criterion. Two competing claim loops, one queued
    row: it runs once and the loser gets nothing (never a second dispatch)."""
    db = QueueDb()
    run = db.add_run()
    pool = _pool_for(db)
    dispatched: list = []
    failed: list = []

    async def _go():
        with _queue_harness(db, dispatched, failed):
            first = await runs_queue.claim_next(pool, worker_id="worker-a")
            second = await runs_queue.claim_next(pool, worker_id="worker-b")
            assert first is not None, "the first worker must get the queued row"
            assert second is None, "a claimed row must never be handed to a second worker"
            await runs_queue.dispatch_claimed(pool, REPO, MagicMock(), first)
            await asyncio.sleep(0)

    asyncio.run(_go())
    assert [kind for kind, _, _ in dispatched] == ["pack"]
    assert db.rows[0]["claimed_by"] == "worker-a"
    assert db.rows[0]["status"] == "running", "a claimed queued run is promoted to running"
    assert run["attempts"] == 1


def test_a_gated_run_with_a_live_heartbeat_is_never_claimed():
    """T4 — the positive control for the lease reclaim, and the property that protects
    the 24h gate: a run parked at awaiting_approval is invisible to the claim for as long
    as its worker keeps beating, however long it has been parked."""
    db = QueueDb()
    db.add_run(status="awaiting_approval", heartbeat_at=0, claimed_by="worker-a", attempts=1)
    assert db.claim("worker-b", runs_queue.LEASE_S) is None


def test_a_running_row_with_no_heartbeat_is_not_an_expired_lease():
    """T5 — `heartbeat_at IS NOT NULL` guards the predicate, so a row written by a
    pre-V021 code path is never mistaken for a lease that ran out."""
    db = QueueDb()
    db.add_run(status="running", heartbeat_at=None)
    assert db.claim("worker-b", runs_queue.LEASE_S) is None


def test_a_stale_pack_run_is_reclaimed_and_keeps_its_gate_status():
    """T3 + T8 — a dead worker's PACK run is re-dispatched (the graph runner resumes from
    its durable manifest), and a reclaimed gated run KEEPS awaiting_approval: promoting it
    to running would lie to every client polling it."""
    db = QueueDb()
    db.add_run(status="awaiting_approval", heartbeat_at=-9999, claimed_by="dead", attempts=1)
    pool = _pool_for(db)
    dispatched: list = []
    failed: list = []

    async def _go():
        with _queue_harness(db, dispatched, failed):
            row = await runs_queue.claim_next(pool, worker_id="worker-b")
            assert row is not None and row["prev_status"] == "awaiting_approval"
            await runs_queue.dispatch_claimed(pool, REPO, MagicMock(), row)
            await asyncio.sleep(0)

    asyncio.run(_go())
    assert [kind for kind, _, _ in dispatched] == ["pack"]
    assert db.rows[0]["status"] == "awaiting_approval"
    assert failed == []


def test_a_stale_prompt_run_is_failed_not_reclaimed():
    """T6 — a prompt run's SDK session died with its worker and cannot be resumed, so it
    is failed explicitly: a client sees a terminal state instead of a run that never moves."""
    db = QueueDb()
    db.add_run(
        status="running",
        heartbeat_at=-9999,
        claimed_by="dead",
        attempts=1,
        payload={"mode": "prompt"},
    )
    pool = _pool_for(db)
    dispatched: list = []
    failed: list = []

    async def _go():
        with _queue_harness(db, dispatched, failed):
            row = await runs_queue.claim_next(pool)
            await runs_queue.dispatch_claimed(pool, REPO, MagicMock(), row)

    asyncio.run(_go())
    assert dispatched == [], "an unresumable run must never be re-dispatched"
    assert len(failed) == 1 and "restart" in failed[0][1]


def test_a_first_dispatch_of_a_prompt_run_still_runs_it():
    """The positive control for the rule above: 'prompt runs are not RECLAIMED' must not
    become 'prompt runs never start'."""
    db = QueueDb()
    db.add_run(payload={"mode": "prompt"})
    pool = _pool_for(db)
    dispatched: list = []
    failed: list = []

    async def _go():
        with _queue_harness(db, dispatched, failed):
            row = await runs_queue.claim_next(pool)
            await runs_queue.dispatch_claimed(pool, REPO, MagicMock(), row)
            await asyncio.sleep(0)

    asyncio.run(_go())
    assert [kind for kind, _, _ in dispatched] == ["prompt"]
    assert failed == []


def test_a_run_that_keeps_crashing_its_worker_is_failed_not_retried_forever():
    """T7 — attempts is incremented by the claim itself, so a job that kills whatever
    picks it up is bounded instead of walking the whole queue as a crash loop."""
    db = QueueDb()
    db.add_run(
        status="running",
        heartbeat_at=-9999,
        claimed_by="dead",
        attempts=runs_queue.MAX_ATTEMPTS,
    )
    pool = _pool_for(db)
    dispatched: list = []
    failed: list = []

    async def _go():
        with _queue_harness(db, dispatched, failed):
            row = await runs_queue.claim_next(pool)
            await runs_queue.dispatch_claimed(pool, REPO, MagicMock(), row)

    asyncio.run(_go())
    assert dispatched == []
    assert len(failed) == 1 and "too many times" in failed[0][1]


def test_the_payload_carries_what_the_prompt_regex_dropped():
    """T2 — dry_run, language and agent_budget_usd survive the round trip into the
    executor call. The `[pack] …` audit line never carried them, so a run rebuilt from it
    silently became a non-dry-run in the profile's default language with no agent cap."""
    db = QueueDb()
    db.add_run(
        payload={
            "mode": "pack",
            "pack": "marketing",
            "variant": "linkedin-post",
            "inputs": {"brand_name": "ExampleCo"},
            "dry_run": True,
            "language": "de",
            "agent_budget_usd": 7.5,
        }
    )
    pool = _pool_for(db)
    dispatched: list = []
    failed: list = []

    async def _go():
        with _queue_harness(db, dispatched, failed):
            row = await runs_queue.claim_next(pool)
            await runs_queue.dispatch_claimed(pool, REPO, MagicMock(), row)
            await asyncio.sleep(0)

    asyncio.run(_go())
    _kind, args, kwargs = dispatched[0]
    assert args[7] == {"brand_name": "ExampleCo"}
    assert kwargs["dry_run"] is True
    assert kwargs["language"] == "de"
    assert kwargs["agent_budget_usd"] == 7.5


# ── A5 step 3: resolving a gate held by ANOTHER worker ────────────────────────


def _decided_row(db, run_id: str, ws: str, state: str = "approved") -> None:
    db.gates[(run_id, "plan", "plan")] = {
        "run_id": run_id,
        "gate": "plan",
        "node_id": "plan",
        "workspace_id": ws,
        "content_sha": "abc",
        "state": state,
        "edited_content": None,
        "decided_at": "now",
        "applied_at": None,
    }


def test_a_gate_wake_from_another_worker_resolves_the_run():
    """T12 — the operator's POST landed on worker A; the run is held by worker B. The
    wake sets B's local waiter, and B reads the DECISION from the durable row."""
    db = GateDb()
    _scope.db = db
    run_id = str(uuid.uuid4())
    _decided_row(db, run_id, WS_ID)

    async def _go():
        event = asyncio.Event()
        runs_router._gate_events[run_id] = event
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope):
            waiter = asyncio.create_task(
                runs_lifecycle._wait_for_decision(
                    MagicMock(), WS_ID, run_id, event, gate="plan", node_id="plan", durable=True
                )
            )
            await asyncio.sleep(0)
            # Exactly what backend.broker's listener does with a `gtm:gate:` message.
            runs_events.on_broker_message(f"gtm:gate:{run_id}", {"run_id": run_id})
            return await asyncio.wait_for(waiter, timeout=5)

    assert asyncio.run(_go()) == {"decision": "approve", "edited_content": None}


def test_a_gate_resolves_on_the_poll_backstop_with_no_broker_at_all():
    """T13 — correctness lives in the row, latency lives in Redis. Nothing wakes this
    waiter; the durable decision is applied on the next poll tick regardless."""
    db = GateDb()
    _scope.db = db
    run_id = str(uuid.uuid4())
    _decided_row(db, run_id, WS_ID, state="rejected")

    async def _go():
        event = asyncio.Event()  # never set — no wake ever arrives
        with (
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
            patch.object(runs_lifecycle, "GATE_POLL_S", 0.01),
        ):
            return await asyncio.wait_for(
                runs_lifecycle._wait_for_decision(
                    MagicMock(), WS_ID, run_id, event, gate="plan", node_id="plan", durable=True
                ),
                timeout=5,
            )

    assert asyncio.run(_go()) == {"decision": "reject", "edited_content": None}


def test_a_wake_with_nothing_recorded_never_resolves_the_gate():
    """A wake is not a decision. Returning an empty decision here would read as 'not
    approved' downstream and silently REJECT a run nobody decided — so a spurious wake
    must re-arm and keep waiting."""
    db = GateDb()  # no gate row at all
    _scope.db = db
    run_id = str(uuid.uuid4())

    async def _go():
        event = asyncio.Event()
        with (
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
            patch.object(runs_lifecycle, "GATE_POLL_S", 0.01),
        ):
            waiter = asyncio.create_task(
                runs_lifecycle._wait_for_decision(
                    MagicMock(), WS_ID, run_id, event, gate="plan", node_id="plan", durable=True
                )
            )
            for _ in range(5):
                event.set()  # spurious wakes
                await asyncio.sleep(0.02)
            still_waiting = not waiter.done()
            waiter.cancel()
            return still_waiting

    assert asyncio.run(_go()), "an undecided gate must stay open through a spurious wake"


def test_the_gate_channel_carries_a_wake_and_never_the_decision():
    """T14 — a decision travelling over an unauthenticated cache would be a second,
    weaker path to resolving a human gate. The message names the run and nothing else."""
    db = GateDb()
    _scope.db = db
    run_id = str(uuid.uuid4())
    db.gates[(run_id, "plan", "plan")] = {
        "run_id": run_id,
        "gate": "plan",
        "node_id": "plan",
        "workspace_id": WS_ID,
        "content_sha": "abc",
        "state": "open",
        "edited_content": None,
        "decided_at": None,
        "applied_at": None,
    }

    async def _go():
        with (
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
            fake_broker() as broker,
        ):
            ok = await runs_decisions.record_decision(
                MagicMock(), WS_ID, run_id, "edit", "the approved bytes"
            )
            return ok, broker.channels("gtm:gate:")

    ok, messages = asyncio.run(_go())
    assert ok is True
    assert len(messages) == 1
    payload = messages[0]
    assert payload["run_id"] == run_id
    assert set(payload) == {"worker", "run_id"}, f"gate wake leaked fields: {sorted(payload)}"
    assert "the approved bytes" not in json.dumps(payload)
