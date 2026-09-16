"""Run-lifecycle scenario matrix for ``backend/routers/runs.py``.

Entry criterion for the pure-motion refactor that moves the lifecycle coroutines into
``backend/services/runs/``. Every test observes BEHAVIOUR — ``runs`` status writes, the
``run_nodes`` / ``run_blocks`` / ``run_artifacts`` / ``run_gates`` rows, the SSE event
stream, the in-memory registries and spies on the seams — never code structure, so the
refactor may move functions freely while the lifecycle stays byte-for-byte the same.

Matrix rows → tests
  happy path (pack)           test_happy_path_pack_golden_sequence_and_artifact_download
  gate decline                test_gate_decline_pack_rejects_and_releases_everything
  gate edit                   test_gate_edit_pack_records_edited_bytes_and_completes
  node failure mid-run        test_node_failure_mid_run_fails_run_and_frees_slot
  cancel mid-node             test_cancel_mid_node_linear_finishes_in_flight_stage
                              test_cancel_mid_node_fan_out_siblings_run_to_completion
                              test_cancel_mid_fanout_batch_stops_the_next_batch_from_dispatching
  budget exceeded             test_budget_exceeded_before_dispatch_never_calls_executor
  restart-mid-gate            test_restart_mid_gate_reconcile_resumes_and_claims_durable_decision
  resume onto an open gate    test_resume_onto_an_open_undecided_gate_skips_reserve_start_and_repush
                              test_resume_onto_a_gate_with_changed_bytes_still_reopens_and_notifies
                              test_a_fresh_queued_run_still_reserves_and_starts
                              test_resuming_a_run_parked_at_a_gate_ignores_an_exhausted_cap
  SSE no / late subscriber    test_sse_no_subscribers_then_late_subscriber_gets_snapshot_and_done
  push-notify hook            test_push_notify_hook_awaited_once_per_gate_wait (+ restart row)
  prompt mode (_execute_run)  test_prompt_mode_* (five, side by side with the pack twins)

Conventions: no pytest-asyncio (``asyncio.run`` bodies); ``_execute_pack_run`` is awaited
INSIDE the patch context; every patch target lives in the SEAMS block so a later refactor
re-points them in one edit; fixture data is fictional (ExampleCo); ``asyncio.Event`` and
bounded polling are the only synchronisation — never a bare sleep.

Observed-and-pinned (not endorsed — a behaviour change here must be a deliberate step,
not a refactor side effect):
  - RL-02 (fixed 2026-09-16): cancel now stops the runner from dispatching past whatever
    batch was already in flight when POST /cancel landed — ``pack_executor._budget_guard``'s
    injected predicate checks ``runs.status`` before every dispatch batch and returns
    False once it reads ``canceled``. An in-flight batch still runs to completion (the
    graph runner's own contract, unchanged); only the NEXT batch never starts. See
    test_cancel_mid_node_linear_finishes_in_flight_stage and
    test_cancel_mid_fanout_batch_stops_the_next_batch_from_dispatching. (Previously —
    until RL-02 — every downstream node was still dispatched after POST /cancel; only the
    terminal ``ok`` write was suppressed.)
  - files promoted at the plan gate (``plans/<week>-plan.json|md``) are attributed to
    the NEXT completed node's artifact rescan, not to ``plan``.
  - a run resumed by ``reconcile_gates`` re-dispatches the gated node's executor before
    the durable decision is claimed, and re-emits ``awaiting_approval`` for a gate that
    is already decided (no push is sent for it).
  - RL-13/ST-06 (fixed 2026-09-16): a resumed run whose ``runs.status`` is ALREADY
    ``awaiting_approval`` (a boot reconcile, or a lease reclaim — ``dispatch_claimed``
    with ``prev_status == "awaiting_approval"``) skips the admission-time
    ``_reserve_or_deny``/``start_run`` pair entirely — no redundant §R2 check against a
    run that is only waiting on a person, no second ``status: running`` write. See
    test_restart_mid_gate_reconcile_resumes_and_claims_durable_decision's now-shorter
    run_status/frame sequences for process 2, and the dedicated resume tests below. A
    gate found still ``open`` on the EXACT SAME bytes as the resumed turn reproduces
    (``_open_gate_row``'s pre-check) additionally skips re-emitting ``awaiting_approval``
    and the gate push — the operator was already shown this and hasn't decided; a gate
    whose bytes changed (the node re-ran and wrote something different) still
    re-announces, exactly as before. An ALREADY-DECIDED resume (the row above) is
    unaffected by this second part — see the bullet above.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent.pipeline import STAGES, StageOutcome  # noqa: E402
from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from backend.schemas import GateRequest  # noqa: E402
from backend.services.runs import lifecycle as runs_lifecycle  # noqa: E402
from backend.services.runs import pack_executor as runs_pack_executor  # noqa: E402
from gtm_core.gating import entitled_skills  # noqa: E402
from tests.backend._protocol1 import (  # noqa: E402
    BUDGET_MODULES,
    PUSH_MODULES,
    REPO,
    SCOPE_MODULES,
    FakeRequest,
    StateConn,
    drive_gate,
    fake_executor,
    patch_everywhere,
    validate_frame,
)
from tests.backend.test_durable_gates import GateDb  # noqa: E402
from tests.backend.test_packs_api import PROFILE, _provision  # noqa: E402

# ── SEAMS — the ONE place a later refactor re-points ──────────────────────────
lifecycle = runs_router  # where the lifecycle coroutines + handlers live
state = runs_router  # where the in-memory run registries live
ROUTER = runs_router.router  # the FastAPI router (artifact routes via TestClient)
SEAM_EXECUTOR = "agent.packs.execute_stage"  # the pack executor's per-stage call
# Since the Phase 1a split each collaborator is bound by SEVERAL service modules, so a
# seam is a module TUPLE patched in all of them — patching one leaves the others live
# and the real call runs (PRD 2026-09-01 §6.1 row 4). _protocol1 owns the lists.
SEAM_SCOPE = (SCOPE_MODULES, "workspace_scope")  # DB scope → LifecycleDb
SEAM_BUDGET = (BUDGET_MODULES, "acheck_budget")  # §R2 cap predicate
SEAM_PUSH = (PUSH_MODULES, "send_gate_push")  # gate push notification

ENTITLEMENT = "pro_plus"
PLAN_GATE = "⟦GATE:plan⟧"
PACK, VARIANT = "marketing", "linkedin-post"
PACK_AUDIT_LINE = f"[pack] {PACK}/{VARIANT} inputs={json.dumps({}, sort_keys=True)}"
DRAFT_ITEMS = [{"id": "item-1", "platform": "linkedin", "slot": "mon", "status": "draft"}]
REPORT_BYTES = b"# ExampleCo report\n"


@pytest.fixture(autouse=True)
def _clean_lifecycle_state():
    """Nothing this file does may leak into another suite."""
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


# ── fakes ─────────────────────────────────────────────────────────────────────

_STATUS_RE = re.compile(r"UPDATE runs\s+SET status = '(\w+)'")
_ERROR_LITERAL_RE = re.compile(r"error = '([^']*)'")
# The gate-open UPDATE is single-line in pack mode and multi-line in prompt mode.
_GATE_OPEN_RE = re.compile(r"pending_gate = \$2,\s+pending_content = \$3")


class LifecycleDb(StateConn):
    """StateConn (run_nodes / run_blocks / run_artifacts) + GateDb (run_gates + the
    reconcile read) + ONE live ``runs`` row, updated from every ``UPDATE runs`` the
    lifecycle issues — so the same conn answers the router's SELECTs (cancel / decide
    status checks, the stream snapshot, artifact listing + download) from what was
    actually written. Emulates the SQL runs.py issues, nothing more."""

    def __init__(self, run_id: str, workspace_id: str) -> None:
        super().__init__()
        self.gate_db = GateDb()
        self.gates = self.gate_db.gates
        self.runs = self.gate_db.runs  # awaiting_approval_runs() rows (reconcile read)
        self.workspace_id = workspace_id
        self.row: dict = {
            "id": run_id,
            "status": "pending",
            "profile_name": PROFILE,
            "output": None,
            "error": None,
            "error_code": None,
            "pending_gate": None,
            "pending_content": None,
        }
        self.pending_content_rewrites: list[str] = []

    async def execute(self, sql: str, *args):
        if "UPDATE run_gates" in sql:  # cancel(): close any OPEN gate row for this run
            run_id = args[0]
            for row in self.gates.values():
                if row["run_id"] == run_id and row["state"] == "open":
                    row.update(state="rejected", decided_at="now", applied_at="now")
            return None
        if sql.lstrip().startswith("UPDATE runs"):
            self._apply_runs_update(sql, args)
            return None
        return await super().execute(sql, *args)

    def _apply_runs_update(self, sql: str, args: tuple) -> None:
        status = _STATUS_RE.search(sql)
        if status:
            self.row["status"] = status.group(1)
            self.run_status.append(status.group(1))
        if "output = $2" in sql:
            self.row["output"] = args[1]
        if "error = $2" in sql:
            self.row["error"] = args[1]
            if "error_code = $3" in sql:
                self.row["error_code"] = args[2]
        elif (literal := _ERROR_LITERAL_RE.search(sql)) is not None:
            self.row["error"] = literal.group(1)
        if _GATE_OPEN_RE.search(sql):
            self.row["pending_gate"], self.row["pending_content"] = args[1], args[2]
        elif "SET pending_content = $2" in sql:
            self.row["pending_content"] = args[1]
            self.pending_content_rewrites.append(args[1])
        # RL-09: resume_run/reject_run/complete_run/_fail_run now clear the raw
        # `runs.pending_gate`/`pending_content` columns in the SAME guarded UPDATE as
        # their terminal-row write — model that literal-NULL clause here too.
        if "pending_gate = NULL, pending_content = NULL" in sql:
            self.row["pending_gate"] = None
            self.row["pending_content"] = None

    async def fetchval(self, sql: str, *args):
        artifact_id = await super().fetchval(sql, *args)
        if artifact_id is not None:  # run_artifacts insert: args[2] is rel_path
            self.artifacts[args[2]].setdefault("created_at", datetime.now(UTC))
        return artifact_id

    async def fetchrow(self, sql: str, *args):
        if (
            "INSERT INTO run_gates" in sql
            or "UPDATE run_gates" in sql
            or sql.lstrip().startswith("SELECT opened_at, now() AS db_now FROM run_gates")
        ):
            # The last is _open_gate_row's RL-12 companion read — GateDb already knows
            # how to answer it from the same `self.gates` dict this class shares.
            return await self.gate_db.fetchrow(sql, *args)
        if sql.lstrip().startswith("UPDATE runs") and "RETURNING id" in sql:
            return self._cancel_write(sql, args)
        if "SELECT entitlement FROM subscriptions" in sql:
            return {"entitlement": ENTITLEMENT}
        if "FROM run_artifacts" in sql:
            return self._artifact_row(args)
        if "FROM runs" in sql and args and args[0] == self.row["id"]:
            return self._runs_row(sql, args)
        return None

    def _cancel_write(self, sql: str, args: tuple) -> dict | None:
        """cancel()'s conditional write — only when NOT already terminal (the real
        `status NOT IN (...)` clause), mirroring its fail-closed shape."""
        if self.row["status"] in state._TERMINAL_STATUSES:
            return None
        self._apply_runs_update(sql, args)
        return {"id": self.row["id"]}

    def _artifact_row(self, args: tuple) -> dict | None:
        art = next((a for a in self.artifacts.values() if a["id"] == args[0]), None)
        if art is None or args[1] != self.row["id"]:
            return None
        return {k: art[k] for k in ("rel_path", "name", "media_type")}

    def _runs_row(self, sql: str, args: tuple) -> dict:
        if sql.lstrip().startswith("SELECT 1"):
            return {"?column?": 1}
        row = dict(self.row)
        if "AS gate_kind" in sql:  # _RUN_DETAIL_SQL's open-gate subqueries
            row["gate_kind"], row["gate_node_id"] = self._open_gate(args[0])
        return row

    def _open_gate(self, run_id: str) -> tuple[str | None, str | None]:
        """The gate_kind/gate_node_id subqueries: the newest OPEN run_gates row's
        (gate, node_id), else (None, None) — mirrors the real `state = 'open'` filter,
        so closing the row (cancel, decide) makes it disappear from here too."""
        open_rows = [
            (gate, node)
            for (run, gate, node), row in self.gates.items()
            if run == run_id and row.get("state") == "open"
        ]
        return open_rows[-1] if open_rows else (None, None)

    async def fetch(self, sql: str, *args):
        if "awaiting_approval_runs()" in sql:
            return list(self.runs)
        if "FROM run_nodes" in sql:
            return [{"node_id": n, "state": v["state"]} for n, v in sorted(self.nodes.items())]
        if "FROM run_blocks" in sql:
            ordered = sorted(self.blocks.values(), key=lambda b: b["ord"])
            return [{"block": b["block"]} for b in ordered]
        if "FROM run_artifacts" in sql:
            return sorted(self.artifacts.values(), key=lambda a: (a["created_at"], a["rel_path"]))
        return []


class FakePool:
    """asyncpg pool double: ``acquire()`` yields the LifecycleDb (reconcile's raw read)."""

    def __init__(self, db: LifecycleDb) -> None:
        self.db = db

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield self.db


class FakeSessions:
    """BackendSessionStore twin for prompt mode: streams the given chunks, records every
    ``run`` call and exactly which chunks were pulled through the generator."""

    def __init__(self, chunks) -> None:
        self.chunks = tuple(chunks)
        self.calls: list[dict] = []
        self.yielded: list[str] = []

    def run(self, pool, workspace_id, profile_name, prompt, run_id, **kwargs):
        self.calls.append({"run_id": run_id, "prompt": prompt, **kwargs})
        return self._stream()

    async def _stream(self):
        for chunk in self.chunks:
            self.yielded.append(chunk)
            yield chunk


class PausingSessions(FakeSessions):
    """FakeSessions twin that pauses after the FIRST chunk until ``release`` — the prompt-
    mode twin of ``blocking_executor``: lets a test land a cancel between two chunks so
    RL-02's per-chunk check is actually exercised, rather than a cancel that always loses
    the race to a stream that yields everything instantly. ``closed`` proves the
    generator's own cleanup ran (via ``contextlib.aclosing``, not merely GC) — the
    ``finally`` only fires on an explicit ``aclose()`` or a normal return, never on the
    consumer simply stopping without asking for the next value."""

    def __init__(self, chunks, release: asyncio.Event) -> None:
        super().__init__(chunks)
        self._release = release
        self.closed = False

    async def _stream(self):
        try:
            first, *rest = self.chunks
            self.yielded.append(first)
            yield first
            await self._release.wait()
            for chunk in rest:
                self.yielded.append(chunk)
                yield chunk
        finally:
            self.closed = True


def blocking_executor(recorded: list[str], *, block_on: set[str], release: asyncio.Event):
    """execute_stage twin that parks the named stages on ``release`` (so a cancel can land
    while they are in flight), recording ``<stage>:start`` / ``<stage>:end``."""

    async def _fake(cfg, profile, stage_name, manifest, prompts=None, stage_roles=None, **_kw):
        recorded.append(f"{stage_name}:start")
        if stage_name in block_on:
            await release.wait()
        recorded.append(f"{stage_name}:end")
        return StageOutcome(status="ok", outputs=(stage_name,), text=f"{stage_name} text")

    return _fake


# ── harness ───────────────────────────────────────────────────────────────────


@dataclass
class Harness:
    db: LifecycleDb
    pool: FakePool
    push: AsyncMock
    budget: AsyncMock


def _budget_predicate(verdicts) -> AsyncMock:
    """acheck_budget twin: the verdicts in order, then the last one repeated."""
    remaining = list(verdicts)

    async def _check(*_args, **_kwargs) -> bool:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return AsyncMock(side_effect=_check)


@contextlib.contextmanager
def lifecycle_harness(db: LifecycleDb, executor=None, *, budget=(True,)):
    """Every patch a lifecycle scenario needs, applied through the SEAMS block only.
    Yields the spies. ``executor`` (an execute_stage twin) is omitted for prompt mode."""

    @contextlib.asynccontextmanager
    async def _scope(pool, workspace_id):
        yield db

    hz = Harness(
        db=db,
        pool=FakePool(db),
        push=AsyncMock(return_value=0),
        budget=_budget_predicate(budget),
    )
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch_everywhere(*SEAM_SCOPE, _scope))
        stack.enter_context(patch_everywhere(*SEAM_BUDGET, hz.budget))
        stack.enter_context(patch_everywhere(*SEAM_PUSH, hz.push))
        if executor is not None:
            stack.enter_context(patch(SEAM_EXECUTOR, executor))
        yield hz


def _ws_ctx(workspace_id: str) -> WorkspaceCtx:
    return WorkspaceCtx(
        user_id=str(uuid.uuid4()), workspace_id=workspace_id, entitlement=ENTITLEMENT
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _until(predicate, *, what: str, ticks: int = 800) -> None:
    """Bounded polling at drive_gate's cadence — never a bare sleep as synchronisation."""
    for _ in range(ticks):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {what}")


def _drain(q: asyncio.Queue) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    with contextlib.suppress(asyncio.QueueEmpty):
        while True:
            frames.append(q.get_nowait())
    return frames


def _key(event: str, data: dict) -> tuple:
    """Collapse one SSE (event, data) pair to its lifecycle-relevant key fields."""
    if event == "status":
        return ("status", data["status"])
    if event == "node":
        return ("node", data["node_id"], data["state"])
    if event == "awaiting_approval":
        return (event, data["gate"], data.get("node_id"), data["pending_content_sha"])
    if event == "content":
        block = data["block"]
        if block["type"] == "file":
            return ("content", "file", block["props"]["name"])
        return ("content", block["id"])
    if event == "gate_resolved":
        return (event, data["decision"], data.get("edited"))
    if event == "done":
        terminal = (event, data["status"])
        return (*terminal, data["error"]) if "error" in data else terminal
    return (event,)


def _keys(frames: list[tuple[str, dict]]) -> list[tuple]:
    return [_key(event, data) for event, data in frames]


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Reassemble text/event-stream output into (event, data) pairs."""
    frames: list[tuple[str, dict]] = []
    current: str | None = None
    for line in text.split("\n"):
        if line.startswith("event:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current is not None:
            frames.append((current, json.loads(line.split(":", 1)[1].strip())))
            current = None
    return frames


def _provision_gated(ws_env, *, packs=(PACK,)) -> str:
    """Workspace profile + pack activation + one pending plan draft; returns the draft."""
    _provision(ws_env.profiles_root, packs_toml=f"active = {json.dumps(list(packs))}\n")
    pending = ws_env.content_root / PROFILE / "plans" / ".pending"
    pending.mkdir(parents=True, exist_ok=True)
    draft = json.dumps(DRAFT_ITEMS)
    (pending / "2026-32.draft.json").write_text(draft)
    return draft


def _pack_run(hz: Harness, ws_env, run_id: str, *, pack: str = PACK, variant: str = VARIANT):
    return lifecycle._execute_pack_run(
        hz.pool, REPO, ws_env.ws_id, run_id, PROFILE, pack, variant, {}, entitlement=ENTITLEMENT
    )


def _prompt_run(hz: Harness, sessions: FakeSessions, workspace_id: str, run_id: str):
    return lifecycle._execute_run(
        hz.pool,
        sessions,
        workspace_id,
        run_id,
        PROFILE,
        "Draft a launch note for ExampleCo",
        False,
        entitlement=ENTITLEMENT,
    )


def _tracked(hz: Harness, workspace_id: str, run_id: str, coro) -> asyncio.Task:
    """create_run's shape: reserve the concurrency slot, then hand the task to _track_run
    (whose done-callback is what must free the slot on ANY terminal state)."""
    state._workspace_runs.setdefault(workspace_id, set()).add(run_id)
    return lifecycle._track_run(asyncio.create_task(coro), hz.pool, workspace_id, run_id)


async def _settle(task: asyncio.Task) -> None:
    """Await the task, then one loop turn so its done-callbacks (slot release) have run."""
    await asyncio.wait_for(task, timeout=10)
    await asyncio.sleep(0)


async def _decide_via_endpoint(hz: Harness, run_id: str, decision: str, edited=None) -> dict:
    """POST /gate's exact path — the durable run_gates decision AND the in-memory waiter
    — driven through the real handler. (drive_gate only sets the in-memory decision,
    so it can never move the durable row; the rows that assert row state use this.)"""
    await _until(
        lambda: run_id in state._gate_events and hz.db.row["status"] == "awaiting_approval",
        what="the gate waiter",
    )
    body = GateRequest(
        decision=decision,
        edited_content=edited,
        content_sha=_sha(hz.db.row["pending_content"]),
    )
    return await lifecycle.decide_gate(
        run_id, body, _ws_ctx(hz.db.workspace_id), FakeRequest(hz.pool)
    )


def _artifact_client(hz: Harness, workspace_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(ROUTER, prefix="/v1")
    app.state.pool = hz.pool
    app.state.cfg = MagicMock(repo_root=REPO)
    app.dependency_overrides[require_auth] = lambda: _ws_ctx(workspace_id)
    return TestClient(app)


# ── happy path (pack) ─────────────────────────────────────────────────────────


def test_happy_path_pack_golden_sequence_and_artifact_download(ws_env):
    """linkedin-post pauses at the plan gate, is approved, writes one file in studio,
    and completes ok — the EXACT event sequence a subscriber sees, the runs.status write
    sequence, every node row, the artifact row, and the bytes served back."""
    draft = _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    recorded: list[str] = []
    files = {"studio": [("accounts/example-co/report.md", REPORT_BYTES)]}
    executor = fake_executor(recorded, awaiting_on="plan", files_by_stage=files)

    async def _go():
        with lifecycle_harness(db, executor) as hz:
            q = state._subscribe(run_id)  # BEFORE the run starts
            task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
            await drive_gate(run_id, "approve")
            await _settle(task)
            state._unsubscribe(run_id, q)
            return _drain(q), hz

    frames, hz = asyncio.run(_go())

    assert _keys(frames) == [
        ("status", "running"),
        ("node", "radar", "running"),
        ("node", "radar", "completed"),
        ("content", "node-radar"),
        ("node", "plan", "running"),
        ("node", "plan", "running"),  # awaiting_approval reads 'running' on the wire
        ("awaiting_approval", "plan", "plan", _sha(draft)),
        ("node", "plan", "completed"),  # the approval flip, outside the runner
        ("status", "running"),
        ("node", "research", "running"),
        ("node", "research", "completed"),
        ("content", "node-research"),
        ("content", "file", "2026-32-plan.json"),  # promoted at the gate, seen by the
        ("content", "file", "2026-32-plan.md"),  # next rescan → attributed to research
        ("node", "studio", "running"),
        ("node", "studio", "completed"),
        ("content", "node-studio"),
        ("content", "file", "report.md"),
        ("node", "publish", "running"),
        ("node", "publish", "completed"),
        ("content", "node-publish"),
        ("done", "ok"),
    ]
    done = frames[-1][1]
    assert json.loads(done["output"])["variant"] == VARIANT
    assert all(s["status"] == "ok" for s in json.loads(done["output"])["stages"])

    assert db.run_status == ["running", "awaiting_approval", "running", "ok"]
    assert db.row["status"] == "ok" and db.row["output"] == done["output"]
    # RL-09: resume_run (the approval re-entry) and complete_run both clear the raw
    # pending_gate/pending_content columns — a client reading RunResponse.pending_content
    # directly must not see the LAST gate's bytes once the run has moved on.
    assert db.row["pending_gate"] is None and db.row["pending_content"] is None
    assert {n: v["state"] for n, v in db.nodes.items()} == dict.fromkeys(STAGES, "completed")
    assert recorded == list(STAGES)
    hz.push.assert_awaited_once_with(hz.pool, ws, run_id, "plan", node_id="plan")
    assert run_id not in state._gate_events and run_id not in state._gate_decisions
    assert ws not in state._workspace_runs

    report = next(a for a in db.artifacts.values() if a["name"] == "report.md")
    assert report["node_id"] == "studio"
    assert report["sha256"] == hashlib.sha256(REPORT_BYTES).hexdigest()
    assert report["size_bytes"] == len(REPORT_BYTES)
    with lifecycle_harness(db), _artifact_client(hz, ws) as client:
        listing = client.get(f"/v1/runs/{run_id}/artifacts")
        download = client.get(f"/v1/runs/{run_id}/artifacts/{report['id']}")
    assert listing.status_code == 200
    assert {a["name"] for a in listing.json()["artifacts"]} == {
        "2026-32-plan.json",
        "2026-32-plan.md",
        "report.md",
    }
    assert download.status_code == 200 and download.content == REPORT_BYTES
    assert download.headers["content-disposition"].startswith("attachment")
    assert download.headers["content-type"].startswith("text/markdown")


# ── gate decline / edit (pack) ────────────────────────────────────────────────


def test_gate_decline_pack_rejects_and_releases_everything(ws_env):
    """POST /gate reject: runs.status ends rejected, the run_gates row is rejected AND
    claimed, `done` carries rejected, nothing downstream runs, the draft is discarded,
    and every registry (waiter, decision, workspace slot) is released."""
    _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    recorded: list[str] = []

    async def _go():
        with lifecycle_harness(db, fake_executor(recorded, awaiting_on="plan")) as hz:
            q = state._subscribe(run_id)
            task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
            resp = await _decide_via_endpoint(hz, run_id, "reject")
            await _settle(task)
            state._unsubscribe(run_id, q)
            return resp, _drain(q)

    resp, frames = asyncio.run(_go())

    assert resp == {"run_id": run_id, "decision": "reject"}
    assert db.run_status == ["running", "awaiting_approval", "rejected"]
    row = db.gates[(run_id, "plan", "plan")]
    assert row["state"] == "rejected" and row["applied_at"] is not None
    assert _keys(frames)[-2:] == [("gate_resolved", "reject", False), ("done", "rejected")]
    assert recorded == ["radar", "plan"]
    assert not list((ws_env.content_root / PROFILE / "plans" / ".pending").glob("*.draft.json"))
    assert run_id not in state._gate_events and run_id not in state._gate_decisions
    assert ws not in state._workspace_runs


def test_gate_edit_pack_records_edited_bytes_and_completes(ws_env):
    """POST /gate edit: the row is `edited` with the operator's bytes, pending_content is
    rewritten to them, the promoted plan IS the edited draft, and the run completes ok."""
    _provision_gated(ws_env)
    edited = json.dumps([{**DRAFT_ITEMS[0], "slot": "tue"}])
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    recorded: list[str] = []

    async def _go():
        with lifecycle_harness(db, fake_executor(recorded, awaiting_on="plan")) as hz:
            q = state._subscribe(run_id)
            task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
            await _decide_via_endpoint(hz, run_id, "edit", edited)
            await _settle(task)
            state._unsubscribe(run_id, q)
            return _drain(q)

    frames = asyncio.run(_go())

    row = db.gates[(run_id, "plan", "plan")]
    assert row["state"] == "edited" and row["edited_content"] == edited
    assert row["applied_at"] is not None
    # The rewrite history keeps the edited bytes; the raw `runs` column is cleared by
    # the resume_run that follows (RL-09) — the audit trail lives in run_gates, not here.
    assert db.pending_content_rewrites == [edited] and db.row["pending_content"] is None
    assert db.run_status == ["running", "awaiting_approval", "running", "ok"]
    resolved = next(d for e, d in frames if e == "gate_resolved")
    assert resolved["edited"] is True and resolved["content_sha"] == _sha(edited)
    assert _keys(frames)[-1] == ("done", "ok")
    promoted = json.loads(
        (ws_env.content_root / PROFILE / "plans" / "2026-32-plan.json").read_text()
    )
    assert promoted == [{**DRAFT_ITEMS[0], "slot": "tue", "status": "planned"}]
    assert recorded == list(STAGES)
    assert run_id not in state._gate_events and ws not in state._workspace_runs


# ── node failure mid-run ──────────────────────────────────────────────────────


def test_node_failure_mid_run_fails_run_and_frees_slot(ws_env):
    """The second stage fails: runs.status ends failed with the stage's error text, the
    node row is failed, nothing downstream is dispatched, and the _track_run
    done-callback frees the workspace slot."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    recorded: list[str] = []

    async def _go():
        with lifecycle_harness(db, fake_executor(recorded, fail_on="plan")) as hz:
            q = state._subscribe(run_id)
            task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
            assert run_id in state._workspace_runs[ws]
            await _settle(task)
            state._unsubscribe(run_id, q)
            return _drain(q)

    frames = asyncio.run(_go())

    assert db.run_status == ["running", "failed"] and db.row["error"] == "plan exploded"
    assert _keys(frames)[-1] == ("done", "failed", "plan exploded")
    assert db.nodes["plan"] == {"state": "failed", "error": "plan exploded"}
    assert db.nodes["radar"]["state"] == "completed" and "research" not in db.nodes
    assert recorded == ["radar", "plan"]
    assert run_id not in state._gate_events
    assert ws not in state._workspace_runs


# ── cancel mid-node ───────────────────────────────────────────────────────────


def test_cancel_mid_node_linear_finishes_in_flight_stage(ws_env):
    """POST /cancel lands while radar is inside the executor. The in-flight stage runs
    to completion (the graph runner's own contract — an in-flight batch is never
    interrupted), the run never writes `ok` after the cancel, and the state is clean.

    RL-02: unlike before, cancel now also stops the runner from dispatching anything
    PAST that in-flight stage — `plan`'s batch never reaches the executor at all, because
    `_budget_guard`'s injected predicate reads `runs.status == 'canceled'` before the
    runner would dispatch it. The runner still marks `plan` FAILED locally (its own
    "cost cap reached" bookkeeping — `agent/pipeline.py` is unmodified), but
    `_execute_pack_run` recognises that failure as RL-02's cancel case and never calls
    `_fail_run`, so the row stays `canceled`, not `failed`, and no `error_code` is ever
    set."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    recorded: list[str] = []

    async def _go():
        release = asyncio.Event()
        executor = blocking_executor(recorded, block_on={"radar"}, release=release)
        with lifecycle_harness(db, executor) as hz:
            q = state._subscribe(run_id)
            task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
            await _until(lambda: "radar:start" in recorded, what="radar to enter the executor")
            resp = await lifecycle.cancel_run(run_id, _ws_ctx(ws), FakeRequest(hz.pool))
            at_cancel = (list(db.run_status), list(recorded))
            release.set()
            await _settle(task)
            state._unsubscribe(run_id, q)
            return resp, at_cancel, _drain(q)

    resp, (status_at_cancel, recorded_at_cancel), frames = asyncio.run(_go())

    assert resp == {"run_id": run_id, "status": "canceled"}
    assert status_at_cancel == ["running", "canceled"] and recorded_at_cancel == ["radar:start"]
    assert db.run_status == ["running", "canceled"]  # no `ok` after the cancel
    assert db.row["error"] == "canceled by user"
    assert db.row["error_code"] is None  # never mislabeled cost_cap_reached
    assert recorded[:2] == ["radar:start", "radar:end"]
    # RL-02: radar (already in flight) finishes, but plan/research/studio/publish are
    # NEVER dispatched — the executor is never called for any of them.
    assert recorded == ["radar:start", "radar:end"]
    keys = _keys(frames)
    cancel_done = ("done", "canceled", "canceled by user")
    assert [k for k in keys if k[0] == "done"] == [cancel_done]
    assert keys.index(cancel_done) < keys.index(("node", "radar", "completed"))
    assert run_id not in state._cancelled_runs  # consumed when the cancel stops dispatch
    assert run_id not in state._gate_events
    assert ws not in state._workspace_runs


def test_cancel_mid_node_fan_out_siblings_run_to_completion(ws_env):
    """packs/planning/graphs/planning.toml is a genuine multi-root fan-out: all three
    roots sit in the first frontier and are dispatched in ONE batch. A cancel landing
    while all three are in flight cancels none of them — the batch runs to completion,
    every sibling's node row is completed, and `ok` is still suppressed."""
    _provision(ws_env.profiles_root, packs_toml='active = ["planning"]\n')
    roots = ("gtm-plan", "account-plan", "event-plan")
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    recorded: list[str] = []

    async def _go():
        release = asyncio.Event()
        executor = blocking_executor(recorded, block_on=set(roots), release=release)
        with lifecycle_harness(db, executor) as hz:
            q = state._subscribe(run_id)
            coro = _pack_run(hz, ws_env, run_id, pack="planning", variant="planning")
            task = _tracked(hz, ws, run_id, coro)
            await _until(lambda: len(recorded) == 3, what="all three roots to be in flight")
            await lifecycle.cancel_run(run_id, _ws_ctx(ws), FakeRequest(hz.pool))
            at_cancel = list(recorded)
            release.set()
            await _settle(task)
            state._unsubscribe(run_id, q)
            return at_cancel, _drain(q)

    at_cancel, frames = asyncio.run(_go())

    assert sorted(at_cancel) == sorted(f"{r}:start" for r in roots)  # in flight, none done
    assert sorted(recorded) == sorted(f"{r}:{m}" for r in roots for m in ("start", "end"))
    assert {n: v["state"] for n, v in db.nodes.items()} == dict.fromkeys(roots, "completed")
    assert db.run_status == ["running", "canceled"]
    assert [k for k in _keys(frames) if k[0] == "done"] == [
        ("done", "canceled", "canceled by user")
    ]
    assert run_id not in state._cancelled_runs
    assert ws not in state._workspace_runs


def test_cancel_mid_fanout_batch_stops_the_next_batch_from_dispatching(ws_env):
    """RL-02's other half: packs/marketing/graphs/long-form-blog.toml fans `plan` out into
    TWO independent nodes (research-evidence, research-competitive — one batch), which
    both feed into `studio` (the NEXT batch). A cancel landing while both siblings are in
    flight cancels neither — same engine invariant as the planning-pack fan-out above,
    an in-flight batch always runs to completion — but unlike that pack, long-form-blog
    HAS a batch after the fan-out, so this is the one that actually proves the next batch
    never starts: `studio`'s executor is never called."""
    _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    recorded: list[str] = []
    siblings = ("research-evidence", "research-competitive")

    async def _go():
        release = asyncio.Event()

        async def executor(
            cfg, profile, stage_name, manifest, prompts=None, stage_roles=None, **_kw
        ):
            """radar dispatches unrecorded (nothing to prove there); plan short-circuits
            to its gate (agent.packs.execute_stage is fully bypassed here, so the
            gate-pause has to be reproduced by this fake rather than the real engine);
            only the siblings and the next batch (studio/publish, which must NEVER
            dispatch) are recorded, so the equality assertions below stay exact."""
            from agent.pipeline import AWAITING_APPROVAL

            if stage_name == "plan":
                return StageOutcome(status=AWAITING_APPROVAL, outputs=(stage_name,))
            if stage_name not in (*siblings, "studio", "publish"):
                return StageOutcome(status="ok", outputs=(stage_name,), text=f"{stage_name} text")
            recorded.append(f"{stage_name}:start")
            if stage_name in siblings:
                await release.wait()
            recorded.append(f"{stage_name}:end")
            return StageOutcome(status="ok", outputs=(stage_name,), text=f"{stage_name} text")

        with lifecycle_harness(db, executor) as hz:
            coro = _pack_run(hz, ws_env, run_id, pack="marketing", variant="long-form-blog")
            task = _tracked(hz, ws, run_id, coro)
            await drive_gate(run_id, "approve")  # the plan gate
            await _until(lambda: len(recorded) == 2, what="both siblings to be in flight")
            resp = await lifecycle.cancel_run(run_id, _ws_ctx(ws), FakeRequest(hz.pool))
            at_cancel = list(recorded)
            release.set()
            await _settle(task)
            return resp, at_cancel

    resp, at_cancel = asyncio.run(_go())

    assert resp == {"run_id": run_id, "status": "canceled"}
    assert sorted(at_cancel) == sorted(f"{s}:start" for s in siblings)  # in flight, none done
    # Both siblings (already in flight) finish — the engine's own contract — but studio
    # (and publish after it) are NEVER dispatched.
    assert sorted(recorded) == sorted(f"{s}:{m}" for s in siblings for m in ("start", "end"))
    assert "studio:start" not in recorded and "publish:start" not in recorded
    assert db.row["status"] == "canceled" and db.row["error"] == "canceled by user"
    assert db.row["error_code"] is None  # never mislabeled cost_cap_reached
    assert run_id not in state._cancelled_runs
    assert ws not in state._workspace_runs


def test_cancel_at_open_gate_closes_the_durable_row_and_clears_pending_node_id(ws_env):
    """RL-04: cancelling a run parked at an open durable gate writes `canceled` on the
    run row AND closes the `run_gates` row (state -> rejected) in the SAME write — so a
    polling client sees gate=None/pending_node_id=None immediately, instead of a stale
    open gate dangling off a dead run."""
    draft = _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)

    async def _go():
        with lifecycle_harness(db, fake_executor([], awaiting_on="plan")) as hz:
            task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
            await _until(
                lambda: run_id in state._gate_events and db.row["status"] == "awaiting_approval",
                what="the gate waiter",
            )
            with _artifact_client(hz, ws) as client:
                before = client.get(f"/v1/runs/{run_id}")
            resp = await lifecycle.cancel_run(run_id, _ws_ctx(ws), FakeRequest(hz.pool))
            await _settle(task)
            return resp, before, hz

    resp, before, hz = asyncio.run(_go())

    # Before the cancel, the open gate is visible on the polling read (client issue #240).
    before_body = before.json()
    assert before_body["gate"] == "plan" and before_body["pending_node_id"] == "plan"
    assert before_body["pending_content"] == draft

    assert resp == {"run_id": run_id, "status": "canceled"}
    assert db.row["status"] == "canceled" and db.row["error"] == "canceled by user"
    row = db.gates[(run_id, "plan", "plan")]
    assert row["state"] == "rejected" and row["applied_at"] is not None

    with lifecycle_harness(db), _artifact_client(hz, ws) as client:
        after = client.get(f"/v1/runs/{run_id}")
    after_body = after.json()
    assert after_body["status"] == "canceled"
    assert after_body["gate"] is None and after_body["pending_node_id"] is None
    # The raw pending_gate/pending_content columns are cleared too (RL-09's pattern),
    # not just the derived run_gates-backed fields above — a cancelled run must not keep
    # serving the draft it was cancelled out of. Caught missing by tests/live's p2 suite.
    assert after_body["pending_content"] is None
    assert db.row["pending_gate"] is None and db.row["pending_content"] is None
    assert run_id not in state._gate_events and ws not in state._workspace_runs


def test_cancel_on_an_already_terminal_run_is_a_no_op(ws_env):
    """decisions.cancel() itself, called directly (bypassing the router's own 409-on-
    terminal guard), is a defensive no-op on a run that is already `ok`: no `done` frame,
    no row write, no gate touch. (The router's 409 check in cancel_run is the PRIMARY
    guard and is unaffected by this — this test is about cancel()'s own behaviour.)"""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    db.row.update(status="ok", output="already done")
    db.run_status.append("ok")

    async def _go():
        with lifecycle_harness(db) as hz:
            q = state._subscribe(run_id)
            await lifecycle.cancel(hz.pool, ws, run_id)
            frames = _drain(q)
            state._unsubscribe(run_id, q)
            return frames

    frames = asyncio.run(_go())

    assert frames == []
    assert db.row["status"] == "ok" and db.row["output"] == "already done"
    assert db.row["error"] is None
    assert db.run_status == ["ok"]


# ── terminal-row guard (RL-03) ────────────────────────────────────────────────
# cancel() already guards its own write against a terminal row (test above); every
# OTHER lifecycle write must too — a background task's own admission/completion write
# can race a cancel (or any other terminal write) landing on the same run.


def test_start_run_on_an_already_terminal_run_is_a_no_op(ws_env):
    """start_run's own UPDATE must not resurrect a `canceled` row back to `running`:
    a cancel from another worker can land between admission and this write."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    db.row.update(status="canceled", error="canceled by user")
    db.run_status.append("canceled")

    async def _go():
        with lifecycle_harness(db) as hz:
            q = state._subscribe(run_id)
            await runs_lifecycle.start_run(hz.pool, ws, run_id)
            frames = _drain(q)
            state._unsubscribe(run_id, q)
            return frames

    frames = asyncio.run(_go())

    assert frames == []
    assert db.row["status"] == "canceled" and db.row["error"] == "canceled by user"
    assert db.run_status == ["canceled"]


def test_resume_run_on_an_already_terminal_run_is_a_no_op(ws_env):
    """resume_run (the post-approval re-entry) must not resurrect a terminal row —
    a cancel can land in the window between a gate decision and this write."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    db.row.update(status="failed", error="node failed")
    db.run_status.append("failed")

    async def _go():
        with lifecycle_harness(db) as hz:
            q = state._subscribe(run_id)
            await runs_lifecycle.resume_run(hz.pool, ws, run_id)
            frames = _drain(q)
            state._unsubscribe(run_id, q)
            return frames

    frames = asyncio.run(_go())

    assert frames == []
    assert db.row["status"] == "failed" and db.row["error"] == "node failed"
    assert db.run_status == ["failed"]


def test_reject_run_on_an_already_terminal_run_is_a_no_op(ws_env):
    """reject_run must not overwrite an `ok` row with `rejected` — a duplicate/late
    decision applied after the run already completed some other way."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    db.row.update(status="ok", output="already done")
    db.run_status.append("ok")

    async def _go():
        with lifecycle_harness(db) as hz:
            q = state._subscribe(run_id)
            await runs_lifecycle.reject_run(hz.pool, ws, run_id)
            frames = _drain(q)
            state._unsubscribe(run_id, q)
            return frames

    frames = asyncio.run(_go())

    assert frames == []
    assert db.row["status"] == "ok" and db.row["output"] == "already done"
    assert db.run_status == ["ok"]


def test_complete_run_on_an_already_terminal_run_is_a_no_op(ws_env):
    """complete_run must not overwrite a `canceled` row with `ok` + stale output: the
    RL-03 scenario where a cancelled run still finishes its in-flight stage
    (test_cancel_mid_node_linear_finishes_in_flight_stage) must not resurrect it."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    db.row.update(status="canceled", error="canceled by user")
    db.run_status.append("canceled")

    async def _go():
        with lifecycle_harness(db) as hz:
            q = state._subscribe(run_id)
            await runs_lifecycle.complete_run(hz.pool, ws, run_id, "late output")
            frames = _drain(q)
            state._unsubscribe(run_id, q)
            return frames

    frames = asyncio.run(_go())

    assert frames == []
    assert db.row["status"] == "canceled" and db.row["output"] is None
    assert db.run_status == ["canceled"]


def test_fail_run_on_an_already_terminal_run_is_a_no_op(ws_env):
    """_fail_run must not overwrite a `rejected` row with `failed` — a stray exception
    from a background task that raced past an already-decided gate."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    db.row.update(status="rejected")
    db.run_status.append("rejected")

    async def _go():
        with lifecycle_harness(db) as hz:
            q = state._subscribe(run_id)
            await lifecycle._fail_run(
                hz.pool, ws, run_id, "stray error", error_code="internal_error"
            )
            frames = _drain(q)
            state._unsubscribe(run_id, q)
            return frames

    frames = asyncio.run(_go())

    assert frames == []
    assert db.row["status"] == "rejected" and db.row["error"] is None
    assert db.row["error_code"] is None
    assert db.run_status == ["rejected"]


# ── pending-gate clear on decision (RL-09) ────────────────────────────────────
# resume_run / reject_run / complete_run / _fail_run now clear pending_gate AND
# pending_content in the SAME guarded UPDATE as the terminal-row write above — the
# raw `runs` columns a client reads via RunResponse.pending_content directly (not
# only the derived gate/pending_node_id fields) must not keep showing the LAST
# gate's bytes once that gate has been decided. Each test seeds a NON-terminal
# `awaiting_approval` row (so RL-03's guard does not block the write) with a held
# gate, then asserts both columns are null after the call succeeds.


def test_resume_run_clears_pending_gate_and_content(ws_env):
    """resume_run (the post-approval re-entry) must wipe the held gate's bytes — a
    prompt run has no run_gates row, so this write is the ONLY record it closed."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    db.row.update(status="awaiting_approval", pending_gate=PLAN_GATE, pending_content="draft")

    async def _go():
        with lifecycle_harness(db) as hz:
            await runs_lifecycle.resume_run(hz.pool, ws, run_id)

    asyncio.run(_go())

    assert db.row["status"] == "running"
    assert db.row["pending_gate"] is None and db.row["pending_content"] is None


def test_reject_run_clears_pending_gate_and_content(ws_env):
    """reject_run must wipe the held gate's bytes too — a rejected run has no more
    open gate, so a client must not keep seeing the draft that was declined."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    db.row.update(status="awaiting_approval", pending_gate=PLAN_GATE, pending_content="draft")

    async def _go():
        with lifecycle_harness(db) as hz:
            await runs_lifecycle.reject_run(hz.pool, ws, run_id)

    asyncio.run(_go())

    assert db.row["status"] == "rejected"
    assert db.row["pending_gate"] is None and db.row["pending_content"] is None


def test_complete_run_clears_pending_gate_and_content(ws_env):
    """complete_run's terminal success write must wipe any still-held gate bytes —
    a linear run's LAST gate stays cleared once the run has finished ok."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    db.row.update(status="awaiting_approval", pending_gate=PLAN_GATE, pending_content="draft")

    async def _go():
        with lifecycle_harness(db) as hz:
            await runs_lifecycle.complete_run(hz.pool, ws, run_id, "final output")

    asyncio.run(_go())

    assert db.row["status"] == "ok" and db.row["output"] == "final output"
    assert db.row["pending_gate"] is None and db.row["pending_content"] is None


def test_fail_run_clears_pending_gate_and_content(ws_env):
    """_fail_run (e.g. a gate timeout) must wipe the held gate's bytes — a failed
    run's stale draft must not linger on the raw `runs` row."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    db.row.update(status="awaiting_approval", pending_gate=PLAN_GATE, pending_content="draft")

    async def _go():
        with lifecycle_harness(db) as hz:
            await lifecycle._fail_run(
                hz.pool, ws, run_id, "gate timeout", error_code="gate_timeout"
            )

    asyncio.run(_go())

    assert db.row["status"] == "failed" and db.row["error"] == "gate timeout"
    assert db.row["pending_gate"] is None and db.row["pending_content"] is None


def test_hold_gate_when_the_row_goes_terminal_before_the_update_returns_cancelled(ws_env):
    """RL-03's hold_gate case: `_cancelled_runs` is NOT set (the early in-process check
    does not fire), but by the time hold_gate's own UPDATE runs the row is already
    terminal — the same race the router's cancel() guard exists for. The UPDATE's own
    `status NOT IN (...)` guard catches it: hold_gate returns CANCELLED without ever
    opening a durable run_gates row or sending a push."""
    _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    db.row.update(status="canceled", error="canceled by user")

    async def _go():
        with lifecycle_harness(db) as hz:
            decision = await runs_lifecycle.hold_gate(
                hz.pool,
                ws,
                run_id,
                sentinel=PLAN_GATE,
                gate="plan",
                pending_content="draft bytes",
                node_id="plan",
                durable=True,
            )
            return decision, hz

    decision, hz = asyncio.run(_go())

    assert decision == runs_lifecycle.CANCELLED
    assert db.row["status"] == "canceled" and db.row["pending_gate"] is None
    assert db.gates == {}
    assert db.run_status == []
    assert hz.push.await_count == 0
    assert run_id not in state._gate_events


# ── budget exceeded ───────────────────────────────────────────────────────────


def test_budget_exceeded_before_dispatch_never_calls_executor(ws_env):
    """The §R2 admission check denies: no stage runs, no gate opens, no push fires; the
    run fails with the cap error on the row AND the `done` event, and the slot is freed.

    Regression for RL-02: a genuine budget exhaustion (not a cancel) must still fail the
    run with error_code="cost_cap_reached" and status="failed" — `cancelled[-1:]` is
    False here, so `_fail_from_outcome` takes the ORIGINAL branch, unchanged by the
    cancel-vs-budget distinction."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    recorded: list[str] = []

    async def _go():
        with lifecycle_harness(db, fake_executor(recorded), budget=(False,)) as hz:
            q = state._subscribe(run_id)
            task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
            await _settle(task)
            state._unsubscribe(run_id, q)
            return _drain(q), hz

    frames, hz = asyncio.run(_go())

    assert recorded == []
    assert db.run_status == ["failed"] and db.row["error"] == "monthly cost cap reached"
    assert db.row["error_code"] == "cost_cap_reached"
    assert _keys(frames) == [("done", "failed", "monthly cost cap reached")]
    assert hz.budget.await_count == 1
    assert hz.push.await_count == 0
    assert run_id not in state._gate_events and db.gates == {}
    assert ws not in state._workspace_runs


# ── restart mid-gate ──────────────────────────────────────────────────────────


def test_restart_mid_gate_reconcile_resumes_and_claims_durable_decision(ws_env):
    """Process 1 runs to the plan gate and dies (task cancelled, in-memory gate dicts
    emptied). The operator's approve lands durably with NO waiter. Process 2's
    reconcile_gates re-dispatches the REAL _execute_pack_run from the stranded row,
    which claims the decision (applied_at set) and completes ok without opening a new
    wait or sending a second push.

    RL-13/ST-06: process 2 is a RESUME (``runs.status`` is already ``awaiting_approval``
    when it is dispatched), so it also skips its own admission-time
    ``_reserve_or_deny``/``start_run`` — one fewer ``status: running`` write/event than
    a fresh dispatch would produce. The already-decided gate still re-emits
    ``awaiting_approval`` (unaffected — see the docstring bullet above); only the
    redundant admission disappears."""
    draft = _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    before: list[str] = []
    after: list[str] = []

    async def _go():
        # ── process 1: run to the plan gate, then the process dies ──
        with lifecycle_harness(db, fake_executor(before, awaiting_on="plan")) as hz1:
            task = _tracked(hz1, ws, run_id, _pack_run(hz1, ws_env, run_id))
            await _until(lambda: hz1.push.await_count == 1, what="the gate push")
            assert run_id in state._gate_events
            assert db.gates[(run_id, "plan", "plan")]["state"] == "open"
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
        state._gate_events.clear()
        state._gate_decisions.clear()
        assert ws not in state._workspace_runs  # the dying task still freed its slot
        assert db.run_status == ["running", "awaiting_approval"]

        # ── while down: the decision lands durably, with no in-process waiter ──
        assert await lifecycle._record_gate_decision(db, ws, run_id, "approve", None) is True
        db.runs.append(
            {
                "id": run_id,
                "workspace_id": ws,
                "profile_name": PROFILE,
                "prompt": PACK_AUDIT_LINE,
                "agent_id": None,
            }
        )

        # ── process 2: boot reconciliation re-dispatches the real pack runner ──
        with lifecycle_harness(db, fake_executor(after, awaiting_on="plan")) as hz2:
            q = state._subscribe(run_id)
            known = set(state._background_tasks)
            resumed = await lifecycle.reconcile_gates(hz2.pool, REPO)
            (resumed_task,) = set(state._background_tasks) - known
            await _settle(resumed_task)
            state._unsubscribe(run_id, q)
            return resumed, _drain(q), hz1, hz2

    resumed, frames, hz1, hz2 = asyncio.run(_go())

    assert resumed == 1
    row = db.gates[(run_id, "plan", "plan")]
    assert row["state"] == "approved" and row["applied_at"] is not None
    assert db.run_status == [
        "running",
        "awaiting_approval",
        # RL-13/ST-06: process 2 is a resume — no "running" here from a redundant
        # start_run; hold_gate's own re-open write is the first thing it does.
        "awaiting_approval",
        "running",
        "ok",
    ]
    assert run_id not in state._gate_events and run_id not in state._gate_decisions
    assert before == ["radar", "plan"]
    assert after == ["plan", "research", "studio", "publish"]  # gated node re-dispatched
    assert _keys(frames) == [
        # RL-13/ST-06: no leading ("status", "running") — process 2 skipped start_run.
        ("node", "plan", "running"),
        ("node", "plan", "running"),
        ("awaiting_approval", "plan", "plan", _sha(draft)),  # emitted although decided
        ("node", "plan", "completed"),
        ("status", "running"),
        ("node", "research", "running"),
        ("node", "research", "completed"),
        ("content", "node-research"),
        ("content", "file", "2026-32-plan.json"),
        ("content", "file", "2026-32-plan.md"),
        ("node", "studio", "running"),
        ("node", "studio", "completed"),
        ("content", "node-studio"),
        ("node", "publish", "running"),
        ("node", "publish", "completed"),
        ("content", "node-publish"),
        ("done", "ok"),
    ]
    hz1.push.assert_awaited_once_with(hz1.pool, ws, run_id, "plan", node_id="plan")
    assert hz2.push.await_count == 0  # a durable decision never re-notifies
    assert (ws_env.content_root / PROFILE / "plans" / "2026-32-plan.json").is_file()
    assert ws not in state._workspace_runs


# ── resume onto an open, UNDECIDED gate (RL-13/ST-06) ─────────────────────────
# Distinct from the restart-mid-gate test above: there, a decision was already
# recorded durably before process 2 ever ran. Here, NOBODY has decided yet — process 2
# lands on a gate that is still open and, when it re-runs the node, reproduces the
# EXACT SAME bytes the operator was already shown. That is the one case with nothing
# new to say: no redundant admission, no second push, no second announcement.


async def _die_at_the_gate(hz, ws_env, run_id: str, ws: str) -> None:
    """Run to the plan gate and kill the task before anyone decides — the shared first
    half of every resume scenario below (process 1)."""
    task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
    await _until(lambda: hz.push.await_count == 1, what="the gate push")
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    state._gate_events.clear()
    state._gate_decisions.clear()


def test_resume_onto_an_open_undecided_gate_skips_reserve_start_and_repush(ws_env):
    """The core RL-13/ST-06 scenario. Resuming (process 2, ``runs.status`` already
    ``awaiting_approval``) must not re-admit (``_reserve_or_deny``/``start_run`` spied at
    0 calls before the decision), must not push again, and must not re-emit
    ``awaiting_approval`` — the gate is exactly where it was. Deciding it afterward must
    still complete the run normally: the resume path really does re-enter and finish."""
    _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    before: list[str] = []
    after: list[str] = []

    async def _go():
        with lifecycle_harness(db, fake_executor(before, awaiting_on="plan")) as hz1:
            await _die_at_the_gate(hz1, ws_env, run_id, ws)
        assert db.row["status"] == "awaiting_approval"

        reserve = AsyncMock(wraps=runs_pack_executor._reserve_or_deny)
        start = AsyncMock(wraps=runs_pack_executor.start_run)
        with (
            lifecycle_harness(db, fake_executor(after, awaiting_on="plan")) as hz2,
            patch.object(runs_pack_executor, "_reserve_or_deny", reserve),
            patch.object(runs_pack_executor, "start_run", start),
        ):
            q = state._subscribe(run_id)
            task2 = _tracked(hz2, ws, run_id, _pack_run(hz2, ws_env, run_id))
            await _until(
                lambda: run_id in state._gate_events and db.row["status"] == "awaiting_approval",
                what="the resumed gate wait",
            )
            await asyncio.sleep(0)  # let anything already scheduled (e.g. a push) run
            snapshot = (reserve.await_count, start.await_count, hz2.push.await_count)
            events_before_decision = [e for e, _ in _drain(q)]
            await _decide_via_endpoint(hz2, run_id, "approve")
            await _settle(task2)
            state._unsubscribe(run_id, q)
            return snapshot, events_before_decision

    snapshot, events_before_decision = asyncio.run(_go())

    reserve_calls, start_calls, push_calls = snapshot
    assert reserve_calls == 0, "resuming onto an open, undecided gate must not re-admit"
    assert start_calls == 0, "resuming onto an open, undecided gate must not restart the row"
    assert push_calls == 0, "the operator was already pushed for these exact bytes"
    assert "awaiting_approval" not in events_before_decision, "must not re-announce the gate"
    assert "status" not in events_before_decision, "no redundant status:running from start_run"
    assert after == ["plan", "research", "studio", "publish"], "the resume must still finish"
    assert db.row["status"] == "ok"


def test_resume_onto_a_gate_with_changed_bytes_still_reopens_and_notifies(ws_env):
    """The positive control: when the resumed node writes DIFFERENT draft bytes (its
    content_sha changed — the "genuinely new" case), ``_open_gate_row`` still resets the
    row and ``hold_gate`` still re-announces it (push + event) — the suppression above
    must never apply to a gate whose bytes actually changed."""
    _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    before: list[str] = []
    rel = "plans/.pending/2026-32.draft.json"
    draft_b = json.dumps([{**DRAFT_ITEMS[0], "slot": "rewritten-by-the-resumed-turn"}])

    async def _go():
        with lifecycle_harness(db, fake_executor(before, awaiting_on="plan")) as hz1:
            await _die_at_the_gate(hz1, ws_env, run_id, ws)
        assert db.row["status"] == "awaiting_approval"

        reclaim = fake_executor(
            [], awaiting_on="plan", files_by_stage={"plan": [(rel, draft_b.encode())]}
        )
        with lifecycle_harness(db, reclaim) as hz2:
            task2 = _tracked(hz2, ws, run_id, _pack_run(hz2, ws_env, run_id))
            await _until(
                lambda: run_id in state._gate_events and db.row["status"] == "awaiting_approval",
                what="the reopened gate wait",
            )
            await asyncio.sleep(0)
            push_calls = hz2.push.await_count
            row = dict(db.gates[(run_id, "plan", "plan")])
            await _decide_via_endpoint(hz2, run_id, "reject")
            await _settle(task2)
            return push_calls, row

    push_calls, row = asyncio.run(_go())

    assert push_calls == 1, "changed bytes must still re-notify the operator"
    assert row["content_sha"] == _sha(draft_b)
    assert row["state"] == "open" and row["applied_at"] is None
    assert db.row["status"] == "rejected"


def test_a_fresh_queued_run_still_reserves_and_starts(ws_env):
    """Regression: a fresh dispatch (never parked at a gate) must still call
    ``_reserve_or_deny``/``start_run`` exactly as before RL-13/ST-06 — only a resume
    (``runs.status`` already ``awaiting_approval`` at dispatch) skips them."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    recorded: list[str] = []

    reserve = AsyncMock(wraps=runs_pack_executor._reserve_or_deny)
    start = AsyncMock(wraps=runs_pack_executor.start_run)

    async def _go():
        with (
            lifecycle_harness(db, fake_executor(recorded)) as hz,
            patch.object(runs_pack_executor, "_reserve_or_deny", reserve),
            patch.object(runs_pack_executor, "start_run", start),
        ):
            task = _tracked(hz, ws, run_id, _pack_run(hz, ws_env, run_id))
            await _settle(task)

    asyncio.run(_go())

    assert reserve.await_count == 1
    assert start.await_count == 1
    assert db.row["status"] == "ok"


def test_resuming_a_run_parked_at_a_gate_ignores_an_exhausted_cap(ws_env):
    """The concrete ST-06 bug: a workspace over its monthly cap must not have a run
    legitimately PARKED at a gate failed with "monthly cost cap reached" merely for
    being resumed — ``_reserve_or_deny`` (admission) has no user action behind it on a
    resume and must not even be consulted. (The runner's own §R2 per-batch guard, which
    DOES run once the still-gated node is re-dispatched, is untouched by this task and
    stays permissive here via the default budget seam, so a failure could only ever come
    from the admission check this test pins as skipped.)"""
    _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)
    before: list[str] = []

    async def _go():
        with lifecycle_harness(db, fake_executor(before, awaiting_on="plan")) as hz1:
            await _die_at_the_gate(hz1, ws_env, run_id, ws)
        assert db.row["status"] == "awaiting_approval"

        at_cap = AsyncMock(return_value=False)  # would refuse if admission ran at all
        with (
            lifecycle_harness(db, fake_executor([], awaiting_on="plan")) as hz2,
            patch.object(runs_pack_executor, "_reserve_or_deny", at_cap),
        ):
            task2 = _tracked(hz2, ws, run_id, _pack_run(hz2, ws_env, run_id))
            await _until(
                lambda: run_id in state._gate_events and db.row["status"] == "awaiting_approval",
                what="the resumed gate wait",
            )
            await asyncio.sleep(0)
            reached_gate_without_failing = db.row["status"] == "awaiting_approval"
            at_cap_calls = at_cap.await_count
            await _decide_via_endpoint(hz2, run_id, "reject")
            await _settle(task2)
            return reached_gate_without_failing, at_cap_calls

    reached_gate_without_failing, at_cap_calls = asyncio.run(_go())

    assert at_cap_calls == 0, "resuming a parked run must never consult admission at all"
    assert reached_gate_without_failing
    assert db.row["status"] == "rejected"
    assert db.row["error_code"] is None, "must not have been failed with cost_cap_reached"


# ── SSE: no subscribers / late subscriber ─────────────────────────────────────


def test_sse_no_subscribers_then_late_subscriber_gets_snapshot_and_done(ws_env):
    """A run with nobody listening leaves no subscriber key and never touches the
    stream count; a stream opened AFTER completion yields snapshot (full DAG + content
    rebuilt from the rows) then done, and cleans both registries up."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)

    async def _go():
        with lifecycle_harness(db, fake_executor([])) as hz:
            await _pack_run(hz, ws_env, run_id)
            assert run_id not in state._run_subscribers
            assert ws not in state._workspace_stream_count
            resp = await lifecycle.stream_run(run_id, _ws_ctx(ws), FakeRequest(hz.pool))
            chunks = [chunk async for chunk in resp.body_iterator]
            return _parse_sse("".join(chunks))

    frames = asyncio.run(_go())

    assert [event for event, _ in frames] == ["snapshot", "done"]
    for event, data in frames:
        assert not validate_frame(event, data), (event, data)
    snapshot, done = frames[0][1], frames[1][1]
    assert snapshot["status"] == "ok" and snapshot["protocol"] == 2
    assert snapshot["nodes"] == [{"id": s, "state": "completed"} for s in sorted(STAGES)]
    assert [b["id"] for b in snapshot["content"]] == [f"node-{s}" for s in STAGES]
    assert done["status"] == "ok" and json.loads(done["output"])["variant"] == VARIANT
    assert run_id not in state._run_subscribers
    assert ws not in state._workspace_stream_count


# ── push-notify hook ──────────────────────────────────────────────────────────


def test_push_notify_hook_awaited_once_per_gate_wait(ws_env):
    """One gate wait ⇒ send_gate_push awaited exactly once with (pool, workspace_id,
    run_id, gate kind, node_id) — the kind, not the raw sentinel. (The restart row pins that a resumed, already-decided gate does
    NOT push again; the budget row pins that no gate ⇒ no push.)"""
    _provision_gated(ws_env)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = LifecycleDb(run_id, ws)

    async def _go():
        with lifecycle_harness(db, fake_executor([], awaiting_on="plan")) as hz:
            task = asyncio.create_task(_pack_run(hz, ws_env, run_id))
            await _until(lambda: hz.push.await_count == 1, what="the gate push")
            await drive_gate(run_id, "reject")
            await asyncio.wait_for(task, timeout=10)
            return hz

    hz = asyncio.run(_go())
    hz.push.assert_awaited_once_with(hz.pool, ws, run_id, "plan", node_id="plan")


# ── prompt mode (_execute_run) — the lifecycle a later step unifies with pack mode ──

PROMPT_CHUNKS = ("Draft: ExampleCo launch note.\n", PLAN_GATE, "\nTail after the gate.")


def _run_prompt_scenario(
    sessions: FakeSessions, decision: str | None, *, budget=(True,), edited=None
):
    """Drive _execute_run under the harness; resolve the gate with `decision` (None ⇒ no
    gate is expected). Returns (frames, harness, run_id, workspace_id, db)."""
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    db = LifecycleDb(run_id, ws)

    async def _go():
        with lifecycle_harness(db, budget=budget) as hz:
            q = state._subscribe(run_id)
            task = _tracked(hz, ws, run_id, _prompt_run(hz, sessions, ws, run_id))
            if decision is not None:
                await drive_gate(run_id, decision, edited)
            await _settle(task)
            state._unsubscribe(run_id, q)
            return _drain(q), hz

    frames, hz = asyncio.run(_go())
    return frames, hz, run_id, ws, db


def test_prompt_mode_happy_path_gate_approve_joins_output():
    """A ⟦GATE:plan⟧ chunk pauses the stream: awaiting_approval (gate=plan, NO node_id,
    sha of the bytes streamed so far) → approve → the stream resumes → ok with the
    joined output; the session got the entitlement skill scope; both budget checks ran."""
    sessions = FakeSessions(PROMPT_CHUNKS)
    frames, hz, run_id, ws, db = _run_prompt_scenario(sessions, "approve")
    at_gate, joined = "".join(PROMPT_CHUNKS[:2]), "".join(PROMPT_CHUNKS)

    assert _keys(frames) == [
        ("status", "running"),
        ("awaiting_approval", "plan", None, _sha(at_gate)),
        # RL-09: resume_run flips the row back to 'running' (and clears pending_gate/
        # pending_content) right after approval — a prompt run has no run_gates row, so
        # this frame is the ONLY signal a client sees that the gate closed and streaming
        # resumed, ahead of (not merely inferred from) the terminal `done`.
        ("status", "running"),
        ("done", "ok"),
    ]
    assert frames[-1][1]["output"] == joined
    assert db.run_status == ["running", "awaiting_approval", "running", "ok"]
    assert db.row["output"] == joined
    assert db.row["pending_gate"] is None and db.row["pending_content"] is None
    assert [c["run_id"] for c in sessions.calls] == [run_id]
    assert sessions.calls[0]["allowed_skills"] == entitled_skills(ENTITLEMENT)
    assert sessions.yielded == list(PROMPT_CHUNKS)
    hz.push.assert_awaited_once_with(hz.pool, ws, run_id, "plan", node_id=None)
    assert hz.budget.await_count == 2  # admission + the post-approval re-gate
    assert run_id not in state._gate_events and run_id not in state._gate_decisions
    assert ws not in state._workspace_runs


def test_prompt_mode_gate_reject_rejects_without_output():
    """Reject at the gate: runs.status ends rejected, `done` carries rejected, no output
    is ever written and the stream is never pulled past the gate chunk."""
    sessions = FakeSessions(PROMPT_CHUNKS)
    frames, _hz, run_id, ws, db = _run_prompt_scenario(sessions, "reject")

    assert db.run_status == ["running", "awaiting_approval", "rejected"]
    assert _keys(frames)[-1] == ("done", "rejected")
    assert db.row["output"] is None
    assert sessions.yielded == list(PROMPT_CHUNKS[:2])
    assert run_id not in state._gate_decisions
    assert ws not in state._workspace_runs


def test_prompt_mode_gate_edit_persists_edited_bytes():
    """Edit at the gate: the operator's bytes replace the streamed buffer — the
    persisted output, the `done` output and the rewritten pending_content are all
    exactly the edited bytes (nothing streams after the gate here, so output == edit)."""
    edited = "EDITED: ExampleCo launch note, approved wording."
    sessions = FakeSessions(PROMPT_CHUNKS[:2])
    frames, _hz, _run_id, _ws, db = _run_prompt_scenario(sessions, "edit", edited=edited)

    assert db.run_status == ["running", "awaiting_approval", "running", "ok"]
    assert db.row["output"] == edited and frames[-1][1]["output"] == edited
    # The rewrite happens before resume_run clears the raw pending_content column
    # (RL-09) — the history list keeps the edited bytes, the live column does not.
    assert db.pending_content_rewrites == [edited] and db.row["pending_content"] is None


def test_prompt_mode_resume_run_called_between_approval_and_the_rest_of_the_stream():
    """RL-09's second bug: before this fix, `_execute_run` approved a gate and just kept
    pulling chunks — the row stayed `awaiting_approval` while it streamed post-approval
    content. Prove `resume_run` actually runs, and runs BEFORE the tail chunk is pulled,
    not merely that the row ends up `ok` (which `complete_run` alone would also produce):
    the `status: running` write must land strictly between `awaiting_approval` and the
    tail chunk being consumed."""
    sessions = FakeSessions(PROMPT_CHUNKS)
    frames, _hz, _run_id, _ws, db = _run_prompt_scenario(sessions, "approve")

    statuses = [d["status"] for e, d in frames if e == "status"]
    gate_idx = next(i for i, (e, _) in enumerate(frames) if e == "awaiting_approval")
    done_idx = next(i for i, (e, _) in enumerate(frames) if e == "done")
    resume_idxs = [
        i
        for i, (e, d) in enumerate(frames)
        if e == "status" and d["status"] == "running" and i > gate_idx
    ]
    assert statuses == ["running", "running"]  # start_run, then resume_run — never a third
    assert resume_idxs and gate_idx < resume_idxs[0] < done_idx
    # The tail chunk ("\nTail after the gate.") was pulled — resume_run ran on the way
    # there, not as a side effect of the run simply finishing.
    assert sessions.yielded == list(PROMPT_CHUNKS)
    assert db.run_status == ["running", "awaiting_approval", "running", "ok"]


def test_prompt_mode_budget_refused_before_session_starts():
    """Over cap at admission: sessions.run is never called; the run fails with the cap
    error on the row and on `done`; the slot is freed."""
    sessions = FakeSessions(PROMPT_CHUNKS)
    frames, hz, run_id, ws, db = _run_prompt_scenario(sessions, None, budget=(False,))

    assert sessions.calls == []
    assert db.run_status == ["failed"] and db.row["error"] == "monthly cost cap reached"
    assert _keys(frames) == [("done", "failed", "monthly cost cap reached")]
    assert hz.budget.await_count == 1 and hz.push.await_count == 0
    assert run_id not in state._gate_events
    assert ws not in state._workspace_runs


def test_prompt_mode_regate_after_approval_refuses_over_cap():
    """The post-approval re-gate (H6) denies: the run fails with the cap error instead
    of resuming the stream, the waiter is popped, and the chunk after the gate is never
    pulled."""
    sessions = FakeSessions(PROMPT_CHUNKS)
    frames, hz, run_id, ws, db = _run_prompt_scenario(sessions, "approve", budget=(True, False))

    assert db.run_status == ["running", "awaiting_approval", "failed"]
    assert db.row["error"] == "monthly cost cap reached"
    assert _keys(frames)[-1] == ("done", "failed", "monthly cost cap reached")
    assert sessions.yielded == list(PROMPT_CHUNKS[:2])
    assert hz.budget.await_count == 2
    assert run_id not in state._gate_events and run_id not in state._gate_decisions
    assert ws not in state._workspace_runs


def test_prompt_mode_cancel_mid_stream_stops_pulling_further_chunks(ws_env):
    """RL-02: `_execute_run` checks for cancellation on EVERY chunk, not only at the
    stream's start/end. No gate is involved here — plain mid-stream cancellation.

    The FIRST chunk is processed normally. The fake session then pauses before producing
    the SECOND; a cancel lands during that pause, so by the time the second chunk is
    actually produced the run is already cancelled — it must be discarded (never
    appended to the persisted output, no gate-sentinel scan) and the run must stop before
    ever asking for a THIRD chunk. The THIRD chunk never being in `sessions.yielded`
    proves the generator itself was closed (via `contextlib.aclosing`), not merely that
    the consumer stopped asking — a lazily-driven generator would never produce it on its
    own either way, but `sessions.closed` (set in the fake's own ``finally``) proves the
    close actually ran, deterministically, rather than being left to GC."""
    release = asyncio.Event()
    chunks = (
        "first chunk.\n",
        "second chunk — pulled after cancel lands, but never processed.\n",
        "third chunk — never pulled at all.\n",
    )
    sessions = PausingSessions(chunks, release)
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    db = LifecycleDb(run_id, ws)

    async def _go():
        with lifecycle_harness(db) as hz:
            q = state._subscribe(run_id)
            task = _tracked(hz, ws, run_id, _prompt_run(hz, sessions, ws, run_id))
            await _until(lambda: sessions.yielded == [chunks[0]], what="the first chunk")
            resp = await lifecycle.cancel_run(run_id, _ws_ctx(ws), FakeRequest(hz.pool))
            release.set()
            await _settle(task)
            state._unsubscribe(run_id, q)
            return resp, _drain(q)

    resp, frames = asyncio.run(_go())

    assert resp == {"run_id": run_id, "status": "canceled"}
    # The second chunk WAS produced by the fake (it was already in flight when the
    # cancel landed) but the third — which required one more pull — never was.
    assert sessions.yielded == list(chunks[:2])
    assert sessions.closed is True  # aclosing actually closed the generator
    assert db.row["output"] is None  # the second chunk was never appended/persisted
    assert db.run_status == ["running", "canceled"]
    assert db.row["error"] == "canceled by user"
    cancel_done = ("done", "canceled", "canceled by user")
    assert [k for k in _keys(frames) if k[0] == "done"] == [cancel_done]
    assert run_id not in state._cancelled_runs
    assert ws not in state._workspace_runs
