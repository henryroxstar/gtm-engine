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
  budget exceeded             test_budget_exceeded_before_dispatch_never_calls_executor
  restart-mid-gate            test_restart_mid_gate_reconcile_resumes_and_claims_durable_decision
  SSE no / late subscriber    test_sse_no_subscribers_then_late_subscriber_gets_snapshot_and_done
  push-notify hook            test_push_notify_hook_awaited_once_per_gate_wait (+ restart row)
  prompt mode (_execute_run)  test_prompt_mode_* (five, side by side with the pack twins)

Conventions: no pytest-asyncio (``asyncio.run`` bodies); ``_execute_pack_run`` is awaited
INSIDE the patch context; every patch target lives in the SEAMS block so a later refactor
re-points them in one edit; fixture data is fictional (ExampleCo); ``asyncio.Event`` and
bounded polling are the only synchronisation — never a bare sleep.

Observed-and-pinned (not endorsed — a behaviour change here must be a deliberate step,
not a refactor side effect):
  - cancel does not stop the graph runner: every downstream node is still dispatched
    after POST /cancel; only the terminal ``ok`` write is suppressed.
  - files promoted at the plan gate (``plans/<week>-plan.json|md``) are attributed to
    the NEXT completed node's artifact rescan, not to ``plan``.
  - a run resumed by ``reconcile_gates`` re-dispatches the gated node's executor before
    the durable decision is claimed, and re-emits ``awaiting_approval`` for a gate that
    is already decided (no push is sent for it).
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
            "output": None,
            "error": None,
            "pending_gate": None,
            "pending_content": None,
        }
        self.pending_content_rewrites: list[str] = []

    async def execute(self, sql: str, *args):
        if "INSERT INTO run_gates" in sql:
            return await self.gate_db.execute(sql, *args)
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
        elif (literal := _ERROR_LITERAL_RE.search(sql)) is not None:
            self.row["error"] = literal.group(1)
        if _GATE_OPEN_RE.search(sql):
            self.row["pending_gate"], self.row["pending_content"] = args[1], args[2]
        elif "SET pending_content = $2" in sql:
            self.row["pending_content"] = args[1]
            self.pending_content_rewrites.append(args[1])

    async def fetchval(self, sql: str, *args):
        artifact_id = await super().fetchval(sql, *args)
        if artifact_id is not None:  # run_artifacts insert: args[2] is rel_path
            self.artifacts[args[2]].setdefault("created_at", datetime.now(UTC))
        return artifact_id

    async def fetchrow(self, sql: str, *args):
        if "UPDATE run_gates" in sql:
            return await self.gate_db.fetchrow(sql, *args)
        if "SELECT entitlement FROM subscriptions" in sql:
            return {"entitlement": ENTITLEMENT}
        if "FROM run_artifacts" in sql:
            art = next((a for a in self.artifacts.values() if a["id"] == args[0]), None)
            if art is None or args[1] != self.row["id"]:
                return None
            return {k: art[k] for k in ("rel_path", "name", "media_type")}
        if "FROM runs" in sql and args and args[0] == self.row["id"]:
            return {"?column?": 1} if sql.lstrip().startswith("SELECT 1") else dict(self.row)
        return None

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
    assert db.row["pending_gate"] == PLAN_GATE and db.row["pending_content"] == draft
    assert {n: v["state"] for n, v in db.nodes.items()} == dict.fromkeys(STAGES, "completed")
    assert recorded == list(STAGES)
    hz.push.assert_awaited_once_with(hz.pool, ws, run_id, PLAN_GATE)
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
    assert db.pending_content_rewrites == [edited] and db.row["pending_content"] == edited
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
    to completion, the run never writes `ok` after the cancel, and the state is clean.

    Pinned observation: cancel does not stop the runner — every downstream node is still
    dispatched (the `done rejected` a stream client sees is followed, on the queue, by
    node events it will never read); only the terminal `ok` write is suppressed."""
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

    assert resp == {"run_id": run_id, "status": "rejected"}
    assert status_at_cancel == ["running", "rejected"] and recorded_at_cancel == ["radar:start"]
    assert db.run_status == ["running", "rejected"]  # no `ok` after the cancel
    assert db.row["error"] == "canceled by user"
    assert recorded[:2] == ["radar:start", "radar:end"]
    assert recorded == [f"{s}:{m}" for s in STAGES for m in ("start", "end")]
    keys = _keys(frames)
    cancel_done = ("done", "rejected", "canceled by user")
    assert [k for k in keys if k[0] == "done"] == [cancel_done]
    assert keys.index(cancel_done) < keys.index(("node", "radar", "completed"))
    assert run_id not in state._cancelled_runs  # consumed at the suppressed `ok` exit
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
    assert db.run_status == ["running", "rejected"]
    assert [k for k in _keys(frames) if k[0] == "done"] == [
        ("done", "rejected", "canceled by user")
    ]
    assert run_id not in state._cancelled_runs
    assert ws not in state._workspace_runs


# ── budget exceeded ───────────────────────────────────────────────────────────


def test_budget_exceeded_before_dispatch_never_calls_executor(ws_env):
    """The §R2 admission check denies: no stage runs, no gate opens, no push fires; the
    run fails with the cap error on the row AND the `done` event, and the slot is freed."""
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
    wait or sending a second push."""
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
        "running",  # process 2
        "awaiting_approval",
        "running",
        "ok",
    ]
    assert run_id not in state._gate_events and run_id not in state._gate_decisions
    assert before == ["radar", "plan"]
    assert after == ["plan", "research", "studio", "publish"]  # gated node re-dispatched
    assert _keys(frames) == [
        ("status", "running"),
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
    hz1.push.assert_awaited_once_with(hz1.pool, ws, run_id, PLAN_GATE)
    assert hz2.push.await_count == 0  # a durable decision never re-notifies
    assert (ws_env.content_root / PROFILE / "plans" / "2026-32-plan.json").is_file()
    assert ws not in state._workspace_runs


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
    assert snapshot["status"] == "ok" and snapshot["protocol"] == 1
    assert snapshot["nodes"] == [{"id": s, "state": "completed"} for s in sorted(STAGES)]
    assert [b["id"] for b in snapshot["content"]] == [f"node-{s}" for s in STAGES]
    assert done["status"] == "ok" and json.loads(done["output"])["variant"] == VARIANT
    assert run_id not in state._run_subscribers
    assert ws not in state._workspace_stream_count


# ── push-notify hook ──────────────────────────────────────────────────────────


def test_push_notify_hook_awaited_once_per_gate_wait(ws_env):
    """One gate wait ⇒ send_gate_push awaited exactly once with (pool, workspace_id,
    run_id, sentinel). (The restart row pins that a resumed, already-decided gate does
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
    hz.push.assert_awaited_once_with(hz.pool, ws, run_id, PLAN_GATE)


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
        ("done", "ok"),
    ]
    assert frames[-1][1]["output"] == joined
    assert db.run_status == ["running", "awaiting_approval", "ok"]
    assert db.row["output"] == joined
    assert db.row["pending_gate"] == PLAN_GATE and db.row["pending_content"] == at_gate
    assert [c["run_id"] for c in sessions.calls] == [run_id]
    assert sessions.calls[0]["allowed_skills"] == entitled_skills(ENTITLEMENT)
    assert sessions.yielded == list(PROMPT_CHUNKS)
    hz.push.assert_awaited_once_with(hz.pool, ws, run_id, PLAN_GATE)
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

    assert db.run_status == ["running", "awaiting_approval", "ok"]
    assert db.row["output"] == edited and frames[-1][1]["output"] == edited
    assert db.pending_content_rewrites == [edited] and db.row["pending_content"] == edited


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
