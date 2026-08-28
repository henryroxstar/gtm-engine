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


@contextlib.contextmanager
def pack_run_harness(conn, executor):
    """Patch _execute_pack_run's collaborators: fake executor + given conn +
    always-allowing budget + no-op push. Yields the conn back."""
    from backend.routers import runs as runs_router

    @contextlib.asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    with (
        patch("agent.packs.execute_stage", executor),
        patch.object(runs_router, "workspace_scope", _scope),
        patch.object(runs_router, "acheck_budget", AsyncMock(return_value=True)),
        patch.object(runs_router, "send_gate_push", AsyncMock(return_value=0)),
    ):
        yield conn


async def drive_gate(run_id: str, decision: str, edited_content: str | None = None) -> None:
    """Wait for the run's gate waiter to register, then resolve it (test-side twin
    of POST /gate — the endpoint's own emission is tested separately)."""
    from backend.routers import runs as runs_router

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
