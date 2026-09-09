"""Shared helpers for the A2/A11 protocol-1 suites (stream, persistence, artifacts).

Not collected by pytest (no test_ prefix). Three pieces:

- ``validate_frame`` — validates one reassembled SSE frame against
  schemas/run-event.schema.json. minijsonschema has no oneOf/$ref support, so the
  branch is selected by the event const and $refs are inlined (bounded — deeper
  nesting validates permissively, which is fine: emitted blocks are depth ≤ 2).
- ``StateConn`` — a stateful fake asyncpg connection that emulates just enough of
  V015 (run_nodes / run_blocks / run_artifacts upserts + runs.status writes) for
  the mirror assertions rows-match-events. Unmatched queries return None/[].
- ``pack_run_harness`` / ``fake_executor`` — the test_packs_api harness extended
  with per-stage file writes (A11 attribution) and StageOutcome.text (A2 blocks).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.routers import runs as runs_router
from backend.services.runs import admission as runs_admission
from backend.services.runs import budget as runs_budget
from backend.services.runs import decisions as runs_decisions
from backend.services.runs import events as runs_events
from backend.services.runs import gates as runs_gates
from backend.services.runs import lifecycle as runs_lifecycle
from backend.services.runs import pack_executor as runs_pack_executor
from backend.services.runs import persistence as runs_persistence
from backend.services.runs import queries as runs_queries
from backend.services.runs import queue as runs_queue
from backend.services.runs import reconcile as runs_reconcile
from backend.services.runs import stream as runs_stream
from tests.contracts.minijsonschema import validate as schema_validate

REPO = Path(__file__).resolve().parents[2]
RUN_EVENT_SCHEMA = json.loads((REPO / "schemas" / "run-event.schema.json").read_text())
RUN_ARTIFACT_SCHEMA = json.loads((REPO / "schemas" / "run-artifact.schema.json").read_text())


def _inline_refs(node, defs, expansions: int = 0):
    if isinstance(node, dict):
        if "$ref" in node:
            if expansions >= 4:  # beyond tested depth: validate permissively
                return {}
            name = node["$ref"].rsplit("/", 1)[-1]
            return _inline_refs(defs[name], defs, expansions + 1)
        return {k: _inline_refs(v, defs, expansions) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, defs, expansions) for v in node]
    return node


_DATA_BY_EVENT = {
    branch["properties"]["event"]["const"]: _inline_refs(
        branch["properties"]["data"], RUN_EVENT_SCHEMA["$defs"]
    )
    for branch in RUN_EVENT_SCHEMA["oneOf"]
}


def validate_frame(event: str, data: dict, seq: int | None = None) -> list[str]:
    """[] when the (event, seq, data) triple is a valid RunEvent frame."""
    errs: list[str] = []
    if event not in RUN_EVENT_SCHEMA["properties"]["event"]["enum"]:
        errs.append(f"unknown event type {event!r}")
    if seq is not None and (not isinstance(seq, int) or seq < 0):
        errs.append(f"bad seq {seq!r}")
    branch = _DATA_BY_EVENT.get(event)
    if branch is None:
        errs.append(f"no oneOf branch for {event!r}")
    else:
        errs.extend(schema_validate(data, branch))
    return errs


# ── stateful fake DB conn (V015 emulation) ────────────────────────────────────

_RUNS_STATUS_RE = re.compile(r"UPDATE runs SET status = '(\w+)'")


class StateConn:
    """Emulates the exact SQL runs.py issues against the V015 tables."""

    def __init__(self):
        self.nodes: dict[str, dict] = {}  # node_id → {state, error}
        self.blocks: dict[str, dict] = {}  # block_id → {ord, node_id, block}
        self.artifacts: dict[str, dict] = {}  # rel_path → row (with "id")
        self.run_status: list[str] = []  # runs.status write sequence

    async def execute(self, sql: str, *args):
        if "INSERT INTO run_nodes" in sql:
            _run, _ws, node_id, state, error, _started = args
            self.nodes[node_id] = {"state": state, "error": error}
        elif "INSERT INTO run_blocks" in sql:
            _run, _ws, block_id, node_id, block_json = args
            if block_id in self.blocks:
                self.blocks[block_id]["block"] = json.loads(block_json)
            else:
                nxt = max((b["ord"] for b in self.blocks.values()), default=-1) + 1
                self.blocks[block_id] = {
                    "ord": nxt,
                    "node_id": node_id,
                    "block": json.loads(block_json),
                }
        else:
            m = _RUNS_STATUS_RE.search(sql)
            if m:
                self.run_status.append(m.group(1))

    async def fetchval(self, sql: str, *args):
        if "INSERT INTO run_artifacts" in sql:
            run_id, _ws, rel_path, name, size_bytes, media_type, sha256, node_id = args
            row = self.artifacts.get(rel_path)
            if row is None:
                row = {"id": str(uuid.uuid4()), "run_id": run_id, "rel_path": rel_path}
                self.artifacts[rel_path] = row
            row.update(
                name=name,
                size_bytes=size_bytes,
                media_type=media_type,
                sha256=sha256,
                node_id=node_id,
            )
            return row["id"]
        return None

    async def fetchrow(self, sql: str, *args):
        return None

    async def fetch(self, sql: str, *args):
        return []


# ── pack-run harness with file writes + node text ─────────────────────────────


def fake_executor(
    recorded: list[str],
    awaiting_on: str | None = None,
    files_by_stage: dict[str, list[tuple[str, bytes]]] | None = None,
    fail_on: str | None = None,
):
    """execute_stage twin: records order, writes files into the run's content
    scope (cfg is the workspace-scoped Config), returns StageOutcome.text."""
    from agent.pipeline import AWAITING_APPROVAL, StageOutcome

    async def _fake(cfg, profile, stage_name, manifest, prompts=None, stage_roles=None, **_kw):
        recorded.append(stage_name)
        for rel, data in (files_by_stage or {}).get(stage_name, []):
            path = cfg.content_root / profile / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if fail_on == stage_name:
            return StageOutcome(status="failed", outputs=(), error=f"{stage_name} exploded")
        if awaiting_on == stage_name:
            return StageOutcome(status=AWAITING_APPROVAL, outputs=(stage_name,))
        return StageOutcome(
            status="ok", outputs=(stage_name,), text=f"{stage_name} produced this text"
        )

    return _fake


# Where the run lifecycle binds its collaborators after the Phase 1a split
# (backend/services/runs/). A mock must patch the module that USES a name — patching
# the router's re-export alias intercepts nothing (PRD 2026-09-01 §6.1 row 4). The seams
# test (tests/backend/test_services_runs_seams.py) asserts SCOPE_MODULES covers every
# service module that binds ``workspace_scope``, so a new module cannot slip past the fake.
SCOPE_MODULES = (
    runs_router,
    runs_admission,
    runs_budget,
    runs_decisions,
    runs_events,
    runs_gates,
    runs_lifecycle,
    runs_pack_executor,
    runs_persistence,
    runs_queries,
    runs_queue,
    runs_reconcile,
    runs_stream,
)
BUDGET_MODULES = (runs_budget, runs_pack_executor)  # acheck_budget bindings
PUSH_MODULES = (runs_lifecycle,)  # send_gate_push — one binding since the 1b spine


@contextlib.contextmanager
def patch_everywhere(modules, name, value):
    """Patch ``name`` to ``value`` in EVERY module that binds it, and yield the value.

    After the split a collaborator is imported by several service modules, so a single
    ``patch.object(one_module, name, …)`` leaves the other bindings live — the mock stops
    being the only path and the real call runs (PRD 2026-09-01 §6.1 row 4). The module
    tuples above are the one place a later split re-points.
    """
    with contextlib.ExitStack() as stack:
        for mod in modules:
            stack.enter_context(patch.object(mod, name, value))
        yield value


@contextlib.contextmanager
def pack_run_harness(conn, executor):
    """Patch _execute_pack_run's collaborators: fake executor + given conn +
    always-allowing budget + no-op push. Yields the conn back."""

    @contextlib.asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    budget_ok = AsyncMock(return_value=True)
    push = AsyncMock(return_value=0)
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("agent.packs.execute_stage", executor))
        for mod in SCOPE_MODULES:
            stack.enter_context(patch.object(mod, "workspace_scope", _scope))
        for mod in BUDGET_MODULES:
            stack.enter_context(patch.object(mod, "acheck_budget", budget_ok))
        for mod in PUSH_MODULES:
            stack.enter_context(patch.object(mod, "send_gate_push", push))
        yield conn


async def drive_gate(run_id: str, decision: str, edited_content: str | None = None) -> None:
    """Wait for the run's gate waiter to register, then resolve it (test-side twin
    of POST /gate — the endpoint's own emission is tested separately)."""
    for _ in range(800):
        if run_id in runs_router._gate_events:
            break
        await asyncio.sleep(0.005)
    assert run_id in runs_router._gate_events, "gate waiter never registered"
    async with runs_router._state_lock:
        runs_router._gate_decisions[run_id] = {
            "decision": decision,
            "edited_content": edited_content,
        }
        runs_router._gate_events[run_id].set()


class FakeRequest:
    """Minimal Request stand-in for calling stream_run() directly (see
    test_phase_f4_sse.py for why ASGITransport can't drive an endless stream)."""

    def __init__(self, pool):
        self.app = SimpleNamespace(state=SimpleNamespace(pool=pool))
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


class FakeBroker:
    """Stand-in for backend.broker.Broker: records publishes and (optionally) delivers
    them to a handler, the way the real pattern subscription delivers ANOTHER worker's
    message into this process.

    ``deliver_as`` names the worker the message is treated as coming from, so a test can
    reproduce the two-worker case inside one process — the real listener drops a message
    whose ``worker`` is its own, which is what stops a frame being delivered twice."""

    def __init__(self, handler=None) -> None:
        self.published: list[tuple[str, dict]] = []
        self.handler = handler

    async def publish(self, channel: str, payload: dict) -> None:
        self.published.append((channel, payload))
        if self.handler is not None:
            self.handler(channel, payload)

    async def close(self) -> None:
        return None

    def channels(self, prefix: str) -> list[dict]:
        return [p for c, p in self.published if c.startswith(prefix)]


@contextlib.contextmanager
def fake_broker(handler=None):
    """Install a FakeBroker as the process-wide broker for the duration of the block."""
    from backend import broker as broker_mod

    fake = FakeBroker(handler)
    previous = broker_mod.get_broker()
    broker_mod.set_broker(fake)
    try:
        yield fake
    finally:
        broker_mod.set_broker(previous)
