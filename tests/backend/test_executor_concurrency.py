"""Phase 2 — Asynchronous Concurrency & Gate State Harness.

Tests the concurrency, race conditions, and state machine invariants of the pack
executor and durable gates:
1. Concurrent gate approval vs cancel: proves state transitions fail closed and idempotently.
2. Concurrent competing decisions: proves atomic single-consumption (winner takes all, loser gets False/409).
3. Resuming a paused gate bypasses exhausted monthly cost cap (§R2 / RL-13 / ST-06).
4. Starting a fresh run on an exhausted cap is rejected immediately.
5. Dual-store billing parity: asserts that spend recorded in content/<profile>/costs.jsonl
   matches Postgres cost_records within 0.0001 USD.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from backend.routers import runs as runs_router  # noqa: E402
from backend.services.runs import decisions as runs_decisions  # noqa: E402
from backend.services.runs import pack_executor as runs_pack_executor  # noqa: E402
from gtm_core.metering import CostRecord, JsonlSink, PgSink  # noqa: E402
from tests.backend._protocol1 import (  # noqa: E402
    REPO,
    SCOPE_MODULES,
    fake_executor,
    pack_run_harness,
    patch_everywhere,
)
from tests.backend.test_durable_gates import (  # noqa: E402
    GateDb,
    _await_gate_row,
    _provision_gated,
    _run_pack,
    _scope,
)
from tests.backend.test_packs_api import PROFILE  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state():
    yield
    runs_router._gate_events.clear()
    runs_router._gate_decisions.clear()
    runs_router._cancelled_runs.clear()


def test_concurrent_gate_approval_vs_cancel(ws_env):
    """Simultaneous gate approval and cancellation race:

    When both events fire concurrently via asyncio.gather on an awaiting_approval run:
    - The run resolves deterministically to either 'ok' or 'canceled' without crashing.
    - No corrupt intermediate state is left.
    - The gate row is not left stranded in 'open'.
    """
    _provision_gated(ws_env)
    run_id = str(uuid.uuid4())
    db = GateDb()

    async def _go():
        runner_task = asyncio.create_task(
            _run_pack(ws_env, db, run_id, fake_executor([], awaiting_on="plan"))
        )

        assert await _await_gate_row(db, run_id), "gate row was not persisted"

        # Concurrently fire approve and cancel
        results = await asyncio.gather(
            runs_decisions.record_decision(db, ws_env.ws_id, run_id, "approve", None),
            runs_decisions.cancel(db, ws_env.ws_id, run_id),
            return_exceptions=True,
        )

        # Neither should raise an unhandled exception
        for res in results:
            assert not isinstance(res, Exception), f"Unexpected exception: {res}"

        await runner_task

        # Terminal state must be valid
        assert len(db.run_status) > 0
        terminal = db.run_status[-1]
        assert terminal in ("ok", "canceled"), f"Invalid terminal status: {terminal}"

        # Gate row must not be stranded in 'open'
        gate_row = next((v for k, v in db.gates.items() if k[0] == run_id), None)
        assert gate_row is not None
        assert gate_row["state"] != "open", "Gate row was left stranded in 'open'"

    asyncio.run(_go())


def test_concurrent_competing_decisions_idempotency(ws_env):
    """Two concurrent decisions on the same open gate row:

    Only one can claim the open row; the loser returns False (producing a 409 in the router).
    """
    run_id = str(uuid.uuid4())
    db = GateDb()
    _scope.db = db

    # Seed an open gate row
    db.gates[(run_id, "plan", "plan")] = {
        "run_id": run_id,
        "gate": "plan",
        "node_id": "plan",
        "workspace_id": ws_env.ws_id,
        "content_sha": runs_router._content_sha("[]"),
        "state": "open",
        "edited_content": None,
        "opened_at": db.now_fn(),
        "decided_at": None,
        "applied_at": None,
    }

    async def _go():
        _scope.db = db
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope):
            # Concurrently submit two different decisions
            res1, res2 = await asyncio.gather(
                runs_decisions.record_decision(db, ws_env.ws_id, run_id, "approve", None),
                runs_decisions.record_decision(db, ws_env.ws_id, run_id, "reject", None),
            )
            # Exactly one succeeded in recording onto the open row
            assert (res1 is True and res2 is False) or (res1 is False and res2 is True)
            winner_state = "approved" if res1 else "rejected"
            row = db.gates[(run_id, "plan", "plan")]
            assert row["state"] == winner_state
            assert row["decided_at"] is not None

    asyncio.run(_go())


def test_gate_resume_bypasses_exhausted_monthly_cap(ws_env):
    """RL-13 / ST-06 / §R2 Invariant:

    Resuming a run sitting at awaiting_approval must NOT fail with 'cost cap reached'
    even if the workspace's monthly cost cap is completely exhausted.
    Admission is one-time at start; a resume of a paused gate continues the workflow.
    """
    _provision_gated(ws_env)
    run_id = str(uuid.uuid4())
    db = GateDb()
    db.status = "awaiting_approval"

    # Pre-seed rejected gate row so resume resolves gate without post-approval spend
    db.gates[(run_id, "plan", "plan")] = {
        "run_id": run_id,
        "gate": "plan",
        "node_id": "plan",
        "workspace_id": ws_env.ws_id,
        "content_sha": runs_router._content_sha("[]"),
        "state": "rejected",
        "edited_content": None,
        "opened_at": db.now_fn(),
        "decided_at": db.now_fn(),
        "applied_at": None,
    }

    # Simulate exhausted cap: _reserve_or_deny returns False
    reserve_mock = AsyncMock(return_value=False)

    async def _go():
        _scope.db = db
        with (
            pack_run_harness(db, fake_executor([], awaiting_on="plan")),
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
            patch.object(runs_pack_executor, "_reserve_or_deny", reserve_mock),
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

        # _reserve_or_deny was NOT called on start
        assert reserve_mock.await_count == 0
        # Run completed with rejected gate, NOT failed with cost_cap_reached
        assert db.run_status[-1] == "rejected"

    asyncio.run(_go())


def test_fresh_run_fails_closed_on_exhausted_monthly_cap(ws_env):
    """§R2 Invariant:

    A fresh run (not resuming) when the workspace is over-cap must be refused
    immediately before execution and marked failed with 'cost_cap_reached'.
    """
    _provision_gated(ws_env)
    run_id = str(uuid.uuid4())
    db = GateDb()
    db.status = "pending"

    # Simulate exhausted cap: _reserve_or_deny returns False
    reserve_mock = AsyncMock(return_value=False)

    async def _go():
        _scope.db = db
        with (
            pack_run_harness(db, fake_executor([])),
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
            patch.object(runs_pack_executor, "_reserve_or_deny", reserve_mock),
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

        # _reserve_or_deny WAS called at start
        assert reserve_mock.await_count == 1
        # Run failed with cost cap reason
        assert db.run_status[-1] == "failed"

    asyncio.run(_go())


class _MockPgPool:
    """In-memory mock pool capturing PgSink writes for parity assertions."""

    def __init__(self):
        self.rows: list[dict] = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def execute(self, sql: str, *args):
        if "INSERT INTO cost_records" in sql:
            (
                ws_id,
                profile,
                run_id,
                stage,
                model_or_sku,
                input_tokens,
                output_tokens,
                cost_usd,
                agent_id,
            ) = args
            self.rows.append(
                {
                    "workspace_id": ws_id,
                    "profile_name": profile,
                    "run_id": run_id,
                    "stage": stage,
                    "model": model_or_sku,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": Decimal(str(cost_usd)),
                    "agent_id": agent_id,
                }
            )


def test_dual_store_billing_parity(tmp_path):
    """Dual-store billing parity assertion:

    Ensures that metered spend recorded in content/<profile>/costs.jsonl
    (via JsonlSink) matches Postgres cost_records (via PgSink) within 0.0001 USD.
    """
    from types import SimpleNamespace

    content_root = tmp_path / "content"
    content_root.mkdir()
    cfg = SimpleNamespace(content_root=content_root)

    profile = "acme"
    ws_id = str(uuid.uuid4())
    run_id = f"run-{uuid.uuid4().hex[:8]}"

    jsonl_sink = JsonlSink(cfg, profile)
    mock_pool = _MockPgPool()
    pg_sink = PgSink(mock_pool, "cost_records")

    stages_data = [
        ("radar", "claude-3-5-haiku-20241022", 1500, 450, 0.003125),
        ("research", "claude-3-7-sonnet-20250219", 4200, 1100, 0.029100),
        ("studio", "claude-3-7-sonnet-20250219", 6800, 1950, 0.049650),
        ("judge", "claude-3-5-haiku-20241022", 2100, 320, 0.003700),
    ]

    async def _write_records():
        for stage, model, in_tok, out_tok, cost in stages_data:
            rec = CostRecord(
                workspace_id=ws_id,
                run_id=run_id,
                stage=stage,
                model_or_sku=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=cost,
                source="llm",
                runtime="backend",
                profile=profile,
            )
            # Write to JSONL
            jsonl_sink.write(rec)
            # Write to Postgres
            await pg_sink.awrite(rec)

    asyncio.run(_write_records())

    # 1. Total spend parity
    jsonl_total = jsonl_sink.month_total()
    pg_total = float(sum(r["cost_usd"] for r in mock_pool.rows))
    expected_total = sum(c for _, _, _, _, c in stages_data)

    assert abs(jsonl_total - pg_total) < 0.0001, (
        f"Billing discrepancy: jsonl={jsonl_total}, pg={pg_total}"
    )
    assert abs(jsonl_total - expected_total) < 0.0001

    # 2. Row count and field parity
    assert len(mock_pool.rows) == len(stages_data)
    for i, (stage, model, in_tok, out_tok, cost) in enumerate(stages_data):
        pg_row = mock_pool.rows[i]
        assert pg_row["stage"] == stage
        assert pg_row["model"] == model
        assert pg_row["input_tokens"] == in_tok
        assert pg_row["output_tokens"] == out_tok
        assert abs(float(pg_row["cost_usd"]) - cost) < 0.000001
