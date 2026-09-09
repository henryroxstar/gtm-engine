"""Characterization tests for PRD 2026-09-01 Phase 1b (§6.4).

1b unifies the two run lifecycles. Its failure mode is not a bug slipping in — it is a
**deliberate difference being flattened because it looked like drift**. These tests are
written against Phase 1a's code and are green BEFORE the unified spine exists, so any
behaviour the spine changes shows up here as a failing assertion rather than as a
plausible-looking diff.

Two groups:

* ``P…`` — the four properties PRD §6.4 names by number, all on the pack path: every node
  in a batch is persisted before any of them dispatches, a batch runs to completion even
  when a sibling fails, the §R2 guard runs before EVERY dispatch batch (not once at run
  start), and the whole run holds exactly ONE ``profile_lock``.
* ``C…`` — the rows the behavioral-diff table classifies as *deliberate*
  (the 2026-09-02 run-lifecycle unification note,
  §1): the durable gate row, the suppressed re-notify, the plan-draft discard, the skill
  scope, the content of record, and the pack-only publish dispatch.

The harness is the V5 matrix's (one SEAMS block, one fake DB) — imported, never duplicated,
so a later re-point happens in exactly one file.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from unittest.mock import AsyncMock, patch

from agent.pipeline import AWAITING_APPROVAL, StageOutcome
from backend.services.runs import lifecycle as runs_lifecycle
from backend.services.runs import pack_executor as runs_pack_executor
from tests.backend.test_run_lifecycle_matrix import (
    ENTITLEMENT,
    PROFILE,
    FakeSessions,
    LifecycleDb,
    _drain,
    _keys,
    _pack_run,
    _prompt_run,
    _provision,
    _provision_gated,
    _settle,
    _tracked,
    _until,
    lifecycle_harness,
    state,
)

PLANNING_ROOTS = ("gtm-plan", "account-plan", "event-plan")


# ── ordered log: one list, so "before" is an assertion about indices ──────────


def logging_executor(log: list[str], *, block_on=(), release=None, fail=(), awaiting_on=None):
    """execute_stage twin that appends ``stage:<name>:start`` / ``:end`` to the shared log.

    ``block_on`` parks those stages on ``release`` (so a whole batch can be caught in
    flight); ``fail`` makes those stages return FAILED; ``awaiting_on`` is the stage that
    opens the gate — the runner pauses on the OUTCOME, never on the graph's `gate` flag
    alone, so a gated scenario must say which stage returns AWAITING_APPROVAL.
    """

    async def _fake(cfg, profile, stage_name, manifest, prompts=None, stage_roles=None, **_kw):
        log.append(f"stage:{stage_name}:start")
        if stage_name in block_on and release is not None:
            await release.wait()
        log.append(f"stage:{stage_name}:end")
        if stage_name in fail:
            return StageOutcome(status="failed", error=f"{stage_name} blew up")
        if stage_name == awaiting_on:
            return StageOutcome(status=AWAITING_APPROVAL, outputs=(stage_name,))
        return StageOutcome(status="ok", outputs=(stage_name,), text=f"{stage_name} text")

    return _fake


def gated_executor(log: list[str] | None = None):
    """The common case: `marketing/linkedin-post` paused at its plan gate."""
    return logging_executor([] if log is None else log, awaiting_on="plan")


@contextlib.contextmanager
def log_persist_and_budget(log: list[str], budget: AsyncMock):
    """Interleave the node persists and the §R2 checks into the same ordered log."""
    real_persist = runs_pack_executor._persist_node

    async def _persist(pool, workspace_id, run_id, node_id, entry):
        state_ = await real_persist(pool, workspace_id, run_id, node_id, entry)
        log.append(f"persist:{node_id}:{state_}")
        return state_

    real_budget = budget.side_effect

    async def _budget(*args, **kwargs):
        verdict = await real_budget(*args, **kwargs)
        log.append(f"budget:{verdict}")
        return verdict

    budget.side_effect = _budget
    with patch.object(runs_pack_executor, "_persist_node", _persist):
        yield


# ── P1 — persist-all-before-dispatch-any ──────────────────────────────────────


def test_p1_every_node_in_a_batch_is_persisted_before_any_of_them_dispatches(ws_env):
    """The three planning roots share one frontier. The runner marks and persists all of
    them RUNNING, then dispatches the batch — so a crash between the two never leaves a
    node that ran with no row. Unification must not turn this into per-node interleaving."""
    _provision(ws_env.profiles_root, packs_toml='active = ["planning"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    log: list[str] = []

    async def _go():
        release = asyncio.Event()
        executor = logging_executor(log, block_on=set(PLANNING_ROOTS), release=release)
        with lifecycle_harness(db, executor) as hz:
            with log_persist_and_budget(log, hz.budget):
                task = _tracked(
                    hz,
                    ws,
                    run_id,
                    _pack_run(hz, ws_env, run_id, pack="planning", variant="planning"),
                )
                await _until(
                    lambda: sum(1 for e in log if e.endswith(":start")) == 3,
                    what="all three roots dispatched",
                )
                release.set()
                await _settle(task)

    asyncio.run(_go())

    running = [i for i, e in enumerate(log) if e.startswith("persist:") and e.endswith(":running")]
    first_dispatch = next(i for i, e in enumerate(log) if e.endswith(":start"))
    assert len(running) == 3, log
    assert max(running) < first_dispatch, (
        "a node dispatched before every node in its batch had a row:\n  " + "\n  ".join(log)
    )


# ── P2 — the batch runs to completion when a sibling fails ────────────────────


def test_p2_a_failing_sibling_never_cancels_the_others_in_its_batch(ws_env):
    """`gtm-plan` fails while its two siblings are still parked. Neither is cancelled: both
    reach `:end`, both land a terminal node row, and only THEN does the run fail with the
    failing sibling's error. (`asyncio.gather` without `return_exceptions` would not do
    this on its own — the property is that a stage crash is a FAILED entry, not a raise.)"""
    _provision(ws_env.profiles_root, packs_toml='active = ["planning"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    log: list[str] = []

    async def _go():
        release = asyncio.Event()
        executor = logging_executor(
            log, block_on={"account-plan", "event-plan"}, release=release, fail={"gtm-plan"}
        )
        with lifecycle_harness(db, executor) as hz:
            q = state._subscribe(run_id)
            task = _tracked(
                hz, ws, run_id, _pack_run(hz, ws_env, run_id, pack="planning", variant="planning")
            )
            await _until(lambda: "stage:gtm-plan:end" in log, what="the sibling to fail")
            at_failure = list(log)
            release.set()
            await _settle(task)
            state._unsubscribe(run_id, q)
            return at_failure, _drain(q)

    at_failure, frames = asyncio.run(_go())

    assert "stage:account-plan:end" not in at_failure  # still parked when the sibling failed
    assert sorted(e for e in log if e.endswith(":end")) == sorted(
        f"stage:{r}:end" for r in PLANNING_ROOTS
    )
    assert {n: v["state"] for n, v in db.nodes.items()} == {
        "gtm-plan": "failed",
        "account-plan": "completed",
        "event-plan": "completed",
    }
    assert [k for k in _keys(frames) if k[0] == "done"] == [("done", "failed", "gtm-plan blew up")]


# ── P3 — the §R2 guard runs before EVERY dispatch batch ───────────────────────


def test_p3_the_r2_guard_runs_before_every_dispatch_batch_not_once_per_run(ws_env):
    """`marketing/linkedin-post` is five sequential nodes, so five frontiers. A cap crossed
    mid-run must stop the NEXT batch — which only holds if the guard is inside the loop.
    Proven positionally: every `stage:*:start` is preceded by a `budget:` entry with no
    other dispatch in between."""
    _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    log: list[str] = []

    async def _go():
        with lifecycle_harness(db, logging_executor(log, awaiting_on="plan")) as hz:
            with log_persist_and_budget(log, hz.budget):
                task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
                await _until(lambda: run_id in state._gate_events, what="the plan gate")
                # In-process gate drive; the endpoint's own path is the matrix's job.
                async with state._state_lock:
                    state._gate_decisions[run_id] = {"decision": "approve", "edited_content": None}
                    state._gate_events[run_id].set()
                await _settle(task)

    asyncio.run(_go())

    dispatches = [i for i, e in enumerate(log) if e.endswith(":start")]
    assert len(dispatches) >= 2, log
    for i in dispatches:
        preceding = log[:i]
        last_budget = max(
            (j for j, e in enumerate(preceding) if e.startswith("budget:")), default=-1
        )
        last_dispatch = max(
            (j for j, e in enumerate(preceding) if e.endswith(":start")), default=-1
        )
        assert last_budget > last_dispatch, (
            f"dispatch at {i} was not preceded by a fresh §R2 check:\n  " + "\n  ".join(log)
        )


# ── P4 — one profile_lock for the whole run ───────────────────────────────────


def test_p4_the_whole_run_holds_exactly_one_profile_lock(ws_env):
    """Concurrent nodes in a batch run under the run's single lock, never a second one —
    and the gate does not re-acquire: the runner RETURNS at `awaiting_approval` (releasing
    it, never held across a human wait) and the resume is a second `runner.run` call.
    So: one acquisition per `runner.run` entry, and a gated run enters exactly twice."""
    _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    acquisitions: list[str] = []

    import agent.pipeline as pipeline_mod

    real_lock = pipeline_mod.profile_lock

    def _counting_lock(content_root, profile):
        acquisitions.append(profile)
        return real_lock(content_root, profile)

    async def _go():
        with lifecycle_harness(db, gated_executor()) as hz:
            with patch.object(pipeline_mod, "profile_lock", _counting_lock):
                task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
                await _until(lambda: run_id in state._gate_events, what="the plan gate")
                at_gate = len(acquisitions)
                async with state._state_lock:
                    state._gate_decisions[run_id] = {"decision": "approve", "edited_content": None}
                    state._gate_events[run_id].set()
                await _settle(task)
                return at_gate

    at_gate = asyncio.run(_go())

    assert at_gate == 1, "the pre-gate half of the run took more than one lock"
    assert acquisitions == [PROFILE, PROFILE], (
        "a gated run acquires once before the gate and once on resume — never per batch, "
        f"and never across the human wait; got {acquisitions}"
    )


# ── C — the deliberate rows (unification must NOT flatten these) ──────────────


def test_c_row7_prompt_mode_opens_no_durable_gate_row_but_pack_mode_does(ws_env):
    """Row 7. A prompt run is unresumable (its SDK session dies with the process), so it
    deliberately has no `run_gates` row — `reconcile_gates` fails those explicitly rather
    than pretending to resume one. The pack path opens a row for the same gate."""
    ws = ws_env.ws_id

    def _run_pack() -> LifecycleDb:
        _provision_gated(ws_env)
        run_id = str(uuid.uuid4())
        db = LifecycleDb(run_id, ws)

        async def _go():
            with lifecycle_harness(db, gated_executor()) as hz:
                task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
                await _until(lambda: run_id in state._gate_events, what="the plan gate")
                async with state._state_lock:
                    state._gate_decisions[run_id] = {"decision": "reject", "edited_content": None}
                    state._gate_events[run_id].set()
                await _settle(task)

        asyncio.run(_go())
        return db

    def _run_prompt() -> LifecycleDb:
        run_id = str(uuid.uuid4())
        db = LifecycleDb(run_id, ws)
        sessions = FakeSessions(["a draft ⟦GATE:plan⟧ here"])

        async def _go():
            with lifecycle_harness(db) as hz:
                task = _tracked(hz, ws, run_id, _prompt_run(hz, sessions, ws, run_id))
                await _until(lambda: run_id in state._gate_events, what="the prompt gate")
                async with state._state_lock:
                    state._gate_decisions[run_id] = {"decision": "reject", "edited_content": None}
                    state._gate_events[run_id].set()
                await _settle(task)

        asyncio.run(_go())
        return db

    assert _run_pack().gates, "pack mode must open a durable run_gates row"
    assert not _run_prompt().gates, (
        "a prompt run opened a durable gate row — reconcile_gates would then try to resume "
        "a session that no longer exists"
    )


def test_c_row9_a_decision_already_recorded_during_downtime_suppresses_the_push(ws_env):
    """Row 9. Pack mode notifies only when there is no pending durable decision: a restart
    that finds the operator already decided must not buzz the phone again. Prompt mode has
    no durable decision to find, so it always notifies."""
    _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)

    async def _go():
        with lifecycle_harness(db, gated_executor()) as hz:
            with patch.object(
                runs_lifecycle,
                "_claim_gate_decision",
                AsyncMock(return_value={"decision": "approve", "edited_content": None}),
            ):
                await _settle(_tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id)))
            return hz.push

    push = asyncio.run(_go())
    assert push.await_count == 0, "re-notified for a gate whose decision was already recorded"


def test_c_row12_reject_discards_the_plan_draft_in_pack_mode(ws_env):
    """Row 12. Only pack mode has a draft to discard; a rejected plan must not survive to
    be promoted by the next run. Prompt mode's reject is the same row write with no draft."""
    draft = _provision_gated(ws_env)
    pending = ws_env.content_root / PROFILE / "plans" / ".pending"
    assert [p.read_text() for p in pending.glob("*.draft.json")] == [draft]
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)

    async def _go():
        with lifecycle_harness(db, gated_executor()) as hz:
            task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
            await _until(lambda: run_id in state._gate_events, what="the plan gate")
            async with state._state_lock:
                state._gate_decisions[run_id] = {"decision": "reject", "edited_content": None}
                state._gate_events[run_id].set()
            await _settle(task)

    asyncio.run(_go())
    assert list(pending.glob("*.draft.json")) == [], "a rejected plan draft was left behind"
    assert db.run_status[-1] == "rejected"


def test_c_row4_prompt_mode_scopes_skills_by_entitlement_not_pack_reachability(ws_env):
    """Row 4. A free-text prompt has no graph to narrow against, so the scope is the
    COMMERCIAL entitlement alone (gtm_core.gating.entitled_skills). Narrowing it to the
    profile's pack union would make every registered skill that sits in no pack
    unreachable — the exact regression CLAUDE.md calls out."""
    from gtm_core.gating import entitled_skills

    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    sessions = FakeSessions(["done"])

    async def _go():
        with lifecycle_harness(db) as hz:
            await _settle(_tracked(hz, ws, run_id, _prompt_run(hz, sessions, ws, run_id)))

    asyncio.run(_go())
    scope = sessions.calls[0]["allowed_skills"]
    assert scope == entitled_skills(ENTITLEMENT)
    assert "content-radar" in scope  # in the active pack…
    assert scope - {"content-radar"}, "…and the scope is not narrowed to the pack's skills"


def test_c_row6_the_gate_content_of_record_is_the_draft_in_pack_and_the_stream_in_prompt(ws_env):
    """Row 6. Pack mode promotes a draft FILE, so the bytes the operator approves are that
    file's; prompt mode has only text, so they are the accumulated stream. Both are the
    exact bytes `pending_content` records and `_content_sha` binds the approval to (H9)."""
    draft = _provision_gated(ws_env)
    ws = ws_env.ws_id

    def _pending_content(db: LifecycleDb) -> str:
        return db.row["pending_content"]

    pack_run_id = str(uuid.uuid4())
    pack_db = LifecycleDb(pack_run_id, ws)
    prompt_run_id = str(uuid.uuid4())
    prompt_db = LifecycleDb(prompt_run_id, ws)
    chunks = ["first half ", "second half ⟦GATE:plan⟧"]

    async def _go(db, run_id, coro_factory):
        with lifecycle_harness(db, gated_executor()) as hz:
            task = _tracked(hz, ws, run_id, coro_factory(hz))
            await _until(lambda: run_id in state._gate_events, what="the gate")
            async with state._state_lock:
                state._gate_decisions[run_id] = {"decision": "reject", "edited_content": None}
                state._gate_events[run_id].set()
            await _settle(task)

    asyncio.run(_go(pack_db, pack_run_id, lambda hz: _pack_run(hz, ws_env, pack_run_id)))
    asyncio.run(
        _go(
            prompt_db,
            prompt_run_id,
            lambda hz: _prompt_run(hz, FakeSessions(chunks), ws, prompt_run_id),
        )
    )

    assert _pending_content(pack_db) == draft
    assert _pending_content(prompt_db) == "".join(chunks)


def test_c_row15_prompt_mode_never_dispatches_a_publish(ws_env):
    """Row 15. The A10 Gate-2 dispatch is driven off a pack node's DECLARATION. Prompt mode
    has no declaration, so approving a ⟦GATE:publish⟧ in free text records the bytes and
    sends nothing — unification must not give the prompt path a publish edge."""
    ws, run_id = ws_env.ws_id, str(uuid.uuid4())
    db = LifecycleDb(run_id, ws)
    sessions = FakeSessions(["the post text ⟦GATE:publish⟧"])
    dispatch = AsyncMock()

    async def _go():
        with lifecycle_harness(db) as hz:
            with patch.object(runs_pack_executor, "dispatch_backend_publish", dispatch):
                task = _tracked(hz, ws, run_id, _prompt_run(hz, sessions, ws, run_id))
                await _until(lambda: run_id in state._gate_events, what="the publish gate")
                async with state._state_lock:
                    state._gate_decisions[run_id] = {"decision": "approve", "edited_content": None}
                    state._gate_events[run_id].set()
                await _settle(task)

    asyncio.run(_go())
    dispatch.assert_not_awaited()
    assert db.run_status[-1] == "ok"


# ── the two drift rows the spine aligns (diff table §1 rows 2, 10) ────────────


def test_drift_prompt_mode_failures_now_stamp_completed_at(ws_env):
    """Row 2. Prompt-mode failure writes predate `_fail_run` and omitted `completed_at`, so
    a failed prompt run had no end time while a failed pack run did. The spine routes both
    through `_fail_run`. Additive: nothing reads the column's absence."""
    ws, run_id = ws_env.ws_id, str(uuid.uuid4())
    db = LifecycleDb(run_id, ws)
    statements: list[str] = []
    real_execute = db.execute

    async def _recording_execute(sql, *args):
        statements.append(sql)
        return await real_execute(sql, *args)

    db.execute = _recording_execute

    async def _go():
        with lifecycle_harness(db, budget=(False,)) as hz:  # over cap → refused at admission
            await _settle(_tracked(hz, ws, run_id, _prompt_run(hz, FakeSessions([]), ws, run_id)))

    asyncio.run(_go())

    fails = [sql for sql in statements if "status = 'failed'" in sql]
    assert fails and all("completed_at = now()" in sql for sql in fails), fails
    assert db.row["error"] == "monthly cost cap reached"


def test_drift_a_rejected_prompt_gate_leaves_no_waiter_behind(ws_env):
    """Row 10. Before the spine the prompt path popped `_gate_events` on the approve path
    only — every rejected or timed-out prompt run leaked one `asyncio.Event` keyed by run id,
    unbounded over the process's life. The pack path's `finally` already had it right."""
    ws, run_id = ws_env.ws_id, str(uuid.uuid4())
    db = LifecycleDb(run_id, ws)

    async def _go():
        with lifecycle_harness(db) as hz:
            task = _tracked(
                hz, ws, run_id, _prompt_run(hz, FakeSessions(["draft ⟦GATE:plan⟧"]), ws, run_id)
            )
            await _until(lambda: run_id in state._gate_events, what="the prompt gate")
            async with state._state_lock:
                state._gate_decisions[run_id] = {"decision": "reject", "edited_content": None}
                state._gate_events[run_id].set()
            await _settle(task)

    asyncio.run(_go())

    assert db.run_status[-1] == "rejected"
    assert run_id not in state._gate_events, "the gate waiter outlived the rejected run"
    assert run_id not in state._gate_decisions
