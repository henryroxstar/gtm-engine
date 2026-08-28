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
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from backend.routers import runs as runs_router  # noqa: E402
from tests.backend._protocol1 import REPO, fake_executor, pack_run_harness  # noqa: E402
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
    with pack_run_harness(db, executor), patch.object(runs_router, "workspace_scope", _scope):
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
        with patch.object(runs_router, "workspace_scope", _scope):
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
        with patch.object(runs_router, "workspace_scope", _scope):
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
        with patch.object(runs_router, "workspace_scope", _scope):
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
            patch.object(runs_router, "_execute_pack_run", _record_dispatch),
            patch.object(runs_router, "_fail_run", _record_fail),
            patch.object(runs_router, "workspace_scope", _scope),
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
