"""M-07 — a failed run carries a machine-readable ``error_code`` from a closed set.

A refusal at ``POST /v1/runs`` already carries ``error.code``; a run that fails AFTER admission
used to report only prose, so a client string-matched it to choose between an upgrade prompt,
"start again", a re-run, setup, or a generic failure. Every scenario here is observed where a
client reads it: the persisted row, ``GET /v1/runs/{id}``, the live ``done`` frame, and the
``done`` frame a stream opened after the run ended synthesises from the row.

Reuses the lifecycle-matrix harness (fake DB, patched scope/budget/push). Fixture data is
fictional.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.runs import lifecycle as runs_lifecycle
from backend.services.runs import pack_executor as runs_pack_executor
from backend.services.runs import persistence as runs_persistence
from backend.services.runs import queue as runs_queue
from tests.backend._protocol1 import FakeRequest, fake_executor, validate_frame
from tests.backend.test_email_enroll_gate_outcomes import _ENROLL_DRAFT, _PENDING
from tests.backend.test_fake_runs import (
    PROMPT_PAYLOAD,
    PROSPECTING_PAYLOAD,
    _claimed,
    _fake_run,
    _injected,
)
from tests.backend.test_packs_api import _provision
from tests.backend.test_run_lifecycle_matrix import (
    LifecycleDb,
    _decide_via_endpoint,
    _drain,
    _pack_run,
    _parse_sse,
    _provision_gated,
    _settle,
    _tracked,
    _ws_ctx,
    lifecycle,
    lifecycle_harness,
    state,
)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    for name in ("GTM_FAKE_RUNS", "GTM_FAKE_RUN_DELAY_S", "GTM_FAKE_RUN_FAIL_NODE"):
        monkeypatch.delenv(name, raising=False)
    yield
    for registry in (
        state._gate_events,
        state._gate_decisions,
        state._cancelled_runs,
        state._run_subscribers,
        state._workspace_stream_count,
        state._workspace_runs,
    ):
        registry.clear()


def _observed(db: LifecycleDb, frames: list[tuple[str, dict]]) -> SimpleNamespace:
    """The failed run's code on the row, the poll, the live ``done`` and a late stream's
    ``done``, plus the error prose from the same four places (it must not change)."""
    run_id, ws = db.row["id"], db.workspace_id

    async def _read():
        with lifecycle_harness(db) as hz:
            # `run_id` passed as the plain canonical str, not `uuid.UUID(run_id)`: these
            # handlers now rely on FastAPI's Annotated[uuid.UUID, AfterValidator(str)]
            # boundary (UuidStr) to do that coercion on a real request — calling the
            # coroutine directly bypasses that machinery, so the caller must hand it
            # what FastAPI would have: a str.
            polled = await lifecycle.get_run(run_id, _ws_ctx(ws), FakeRequest(hz.pool))
            resp = await lifecycle.stream_run(run_id, _ws_ctx(ws), FakeRequest(hz.pool))
            chunks = [chunk async for chunk in resp.body_iterator]
            return polled, _parse_sse("".join(chunks))

    polled, late = asyncio.run(_read())
    live = [data for event, data in frames if event == "done"]
    replayed = [data for event, data in late if event == "done"]
    assert len(live) == 1 and len(replayed) == 1, (live, replayed)
    for data in (live[0], replayed[0]):
        assert not validate_frame("done", data), data
    return SimpleNamespace(
        codes=(
            db.row["error_code"],
            polled.error_code,
            live[0]["error_code"],
            replayed[0]["error_code"],
        ),
        errors=(db.row["error"], polled.stages[0]["error"], live[0]["error"], replayed[0]["error"]),
        status=(db.row["status"], polled.status),
    )


def _run_pack(ws_env, db: LifecycleDb, executor, *, budget=(True,)):
    async def _go():
        with lifecycle_harness(db, executor, budget=budget) as hz:
            q = state._subscribe(db.row["id"])
            task = _tracked(hz, db.workspace_id, db.row["id"], _pack_run(hz, ws_env, db.row["id"]))
            await _settle(task)
            state._unsubscribe(db.row["id"], q)
            return _drain(q)

    return asyncio.run(_go())


# ── the failure exit itself ───────────────────────────────────────────────────


def test_error_code_is_a_required_keyword_on_the_failure_exit():
    param = inspect.signature(runs_persistence._fail_run).parameters["error_code"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_a_code_outside_the_closed_set_is_refused_before_anything_is_written():
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    db = LifecycleDb(run_id, ws)
    q = state._subscribe(run_id)

    async def _go():
        with lifecycle_harness(db) as hz:
            await runs_persistence._fail_run(
                hz.pool, ws, run_id, "monthly cost cap reached", error_code="cap_reached"
            )

    with pytest.raises(ValueError, match="cap_reached"):
        asyncio.run(_go())
    state._unsubscribe(run_id, q)
    assert db.run_status == [] and db.row["error"] is None
    assert _drain(q) == []


# ── pack runs ─────────────────────────────────────────────────────────────────


def test_a_run_refused_at_admission_by_the_cost_cap(ws_env):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    db = LifecycleDb(str(uuid.uuid4()), ws_env.ws_id)
    frames = _run_pack(ws_env, db, fake_executor([]), budget=(False,))
    seen = _observed(db, frames)
    assert seen.codes == ("cost_cap_reached",) * 4
    assert seen.errors == ("monthly cost cap reached",) * 4
    assert seen.status == ("failed", "failed")


def test_a_batch_the_budget_guard_stops_mid_run_is_the_cost_cap_not_a_node_failure(ws_env):
    """The runner marks the first node of a refused batch ``failed`` with its own prose; the
    code comes from the guard's verdict, not from that prose."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    db = LifecycleDb(str(uuid.uuid4()), ws_env.ws_id)
    recorded: list[str] = []
    frames = _run_pack(ws_env, db, fake_executor(recorded), budget=(True, False))
    seen = _observed(db, frames)
    assert recorded == []
    assert db.nodes["radar"]["state"] == "failed"
    assert seen.codes == ("cost_cap_reached",) * 4
    assert seen.errors == ("monthly cost cap reached — run aborted before any paid call",) * 4


def test_a_node_that_fails_is_a_node_failure(ws_env):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    db = LifecycleDb(str(uuid.uuid4()), ws_env.ws_id)
    frames = _run_pack(ws_env, db, fake_executor([], fail_on="plan"))
    seen = _observed(db, frames)
    assert seen.codes == ("node_failed",) * 4
    assert seen.errors == ("plan exploded",) * 4


def test_an_undecided_gate_is_a_gate_timeout(ws_env):
    _provision_gated(ws_env)
    db = LifecycleDb(str(uuid.uuid4()), ws_env.ws_id)
    with patch.object(runs_lifecycle, "GATE_TIMEOUT_S", 0.05):
        frames = _run_pack(ws_env, db, fake_executor([], awaiting_on="plan"))
    seen = _observed(db, frames)
    assert db.run_status == ["running", "awaiting_approval", "failed"]
    assert seen.codes == ("gate_timeout",) * 4
    assert seen.errors == ("gate timeout",) * 4


def test_an_approved_enrollment_with_no_sender_configured_is_email_not_configured(ws_env):
    """A Gate-2 dispatch refusal: the approval is recorded, nothing is enrolled, and the code
    says what the operator must set up."""
    _provision(ws_env.profiles_root, packs_toml='active = ["prospecting"]\n')
    run_id = str(uuid.uuid4())
    db = LifecycleDb(run_id, ws_env.ws_id)
    executor = fake_executor(
        [],
        awaiting_on="sequence",
        files_by_stage={
            "sequence": [(f"{_PENDING}/{run_id}.enroll-draft.json", _ENROLL_DRAFT.encode())]
        },
    )
    enroll = AsyncMock(return_value=None)

    async def _go():
        with (
            lifecycle_harness(db, executor) as hz,
            patch.object(runs_pack_executor, "dispatch_backend_email_enroll", enroll),
        ):
            q = state._subscribe(run_id)
            coro = _pack_run(hz, ws_env, run_id, pack="prospecting", variant="prospect-outreach")
            task = _tracked(hz, db.workspace_id, run_id, coro)
            await _decide_via_endpoint(hz, run_id, "approve")
            await _settle(task)
            state._unsubscribe(run_id, q)
            return _drain(q)

    frames = asyncio.run(_go())
    seen = _observed(db, frames)
    assert enroll.await_count == 1
    assert seen.codes == ("email_not_configured",) * 4
    assert seen.errors == (
        ("no Saleshandy API key configured for this workspace — not enrolled",) * 4
    )


# ── queue dispatch ────────────────────────────────────────────────────────────


def _dispatch_claimed(row: dict, sessions) -> tuple[bool, LifecycleDb, list]:
    db = LifecycleDb(row["id"], row["workspace_id"])

    async def _go():
        with lifecycle_harness(db) as hz:
            q = state._subscribe(row["id"])
            dispatched = await runs_queue.dispatch_claimed(hz.pool, MagicMock(), sessions, row)
            state._unsubscribe(row["id"], q)
            return dispatched, _drain(q)

    dispatched, frames = asyncio.run(_go())
    return dispatched, db, frames


def test_a_prompt_run_reclaimed_after_a_restart_is_interrupted():
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    row = _claimed(run_id, ws, PROMPT_PAYLOAD, prev_status="running")
    dispatched, db, frames = _dispatch_claimed(row, MagicMock())
    seen = _observed(db, frames)
    assert dispatched is False
    assert seen.codes == ("run_interrupted",) * 4
    assert seen.errors == ("run did not survive a backend restart — start a new run",) * 4


def test_a_run_claimed_too_many_times_has_exhausted_its_retries():
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    row = {**_claimed(run_id, ws, PROMPT_PAYLOAD), "attempts": runs_queue.MAX_ATTEMPTS + 1}
    dispatched, db, frames = _dispatch_claimed(row, MagicMock())
    seen = _observed(db, frames)
    assert dispatched is False
    assert seen.codes == ("retries_exhausted",) * 4
    assert seen.errors == ("run failed too many times — not retried",) * 4


def test_a_prompt_run_on_a_worker_with_no_session_store_is_worker_unavailable():
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    dispatched, db, frames = _dispatch_claimed(_claimed(run_id, ws, PROMPT_PAYLOAD), None)
    seen = _observed(db, frames)
    assert dispatched is False
    assert seen.codes == ("worker_unavailable",) * 4


# ── fake runs ─────────────────────────────────────────────────────────────────


def test_a_fake_run_failed_at_an_injected_node_is_a_node_failure(ws_env, monkeypatch):
    monkeypatch.setenv("GTM_FAKE_RUNS", "1")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("GTM_FAKE_RUN_DELAY_S", "0")
    monkeypatch.setenv("GTM_FAKE_RUN_FAIL_NODE", "prospect")
    _provision(ws_env.profiles_root, packs_toml='active = ["prospecting"]\n')
    run = _fake_run(ws_env, PROSPECTING_PAYLOAD)
    seen = _observed(run.db, run.frames)
    assert seen.codes == ("node_failed",) * 4
    assert seen.errors == (_injected("prospect"),) * 4
