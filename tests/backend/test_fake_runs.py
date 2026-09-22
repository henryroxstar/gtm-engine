"""GTM_FAKE_RUNS — the dev-only scripted run executor (backend/services/runs/fake.py).

What is pinned, and why each matters:

* the boot guard matrix (``backend.main.check_fake_runs`` and its lifespan wiring) — a
  scripted run on staging would serve fabricated output to a real tenant while looking
  healthy, so the flag outside ``ENV=development`` must stop the boot, never be ignored;
* the seam — every reference to a real executor anywhere under ``backend/`` sits behind
  ``if fake_runs_enabled():`` inside ``dispatch_claimed`` or ``reconcile_gates``. With the flag
  on neither reaches the SDK session, the pack stage executor, the publish dispatch, or any
  ``agent.publish`` publisher (and fake.py imports none of them); with it off (or ENV not
  development) the fake is unreachable;
* a fake run stays fake — dispatch stamps the durable payload, and a stamped run met again
  with the flag off is FAILED at both seams rather than handed to a real executor;
* the lifecycle around it stays REAL — queue dispatch, the §R2 pre-check, the durable
  run_gates row (one per declared gate in pack mode), the content_sha 409, push-on-gate,
  artifact registration + download — and every emitted frame validates against
  schemas/run-event.schema.json.

Reuses the lifecycle-matrix harness (fake DB, patched scope/budget/push), so a fake run is
observed through exactly the rows and frames a real one is. Fixture data is fictional.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import logging
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from fastapi import FastAPI, HTTPException  # noqa: E402

from agent import gate_actions, pipeline_executor  # noqa: E402
from agent import publish as agent_publish  # noqa: E402
from agent.email_dispatch import EnrollDispatchOutcome  # noqa: E402
from backend import main as backend_main  # noqa: E402
from backend.schemas import GateRequest  # noqa: E402
from backend.services.runs import executor as runs_executor  # noqa: E402
from backend.services.runs import fake as runs_fake  # noqa: E402
from backend.services.runs import fake_script as runs_fake_script  # noqa: E402
from backend.services.runs import lifecycle as runs_lifecycle  # noqa: E402
from backend.services.runs import pack_executor as runs_pack_executor  # noqa: E402
from backend.services.runs import queue as runs_queue  # noqa: E402
from backend.services.runs import reconcile as runs_reconcile  # noqa: E402
from gtm_core.packs.loader import load_pack_graph  # noqa: E402
from tests.backend._protocol1 import (  # noqa: E402
    REPO,
    FakeRequest,
    StateConn,
    fake_executor,
    pack_run_harness,
    validate_frame,
)
from tests.backend.test_packs_api import PROFILE, _provision  # noqa: E402
from tests.backend.test_run_lifecycle_matrix import (  # noqa: E402
    PACK_AUDIT_LINE,
    LifecycleDb,
    _artifact_client,
    _decide_via_endpoint,
    _drain,
    _keys,
    _parse_sse,
    _settle,
    _sha,
    _until,
    _ws_ctx,
    lifecycle,
    lifecycle_harness,
    state,
)

PLAN_GATE = "⟦GATE:plan⟧"
PROMPT = "Draft a LinkedIn post for Example Widgets Ltd"
PROMPT_PAYLOAD = {"mode": "prompt", "pack": None, "variant": None, "inputs": {}, "dry_run": True}
PACK_PAYLOAD = {
    "mode": "pack",
    "pack": "marketing",
    "variant": "linkedin-post",
    "inputs": {},
    "dry_run": False,
}
PROSPECTING_PAYLOAD = {**PACK_PAYLOAD, "pack": "prospecting", "variant": "prospect-outreach"}
CASE_STUDY_PAYLOAD = {**PACK_PAYLOAD, "variant": "case-study"}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """No flag leaks in from the shell, and no run registry leaks out to another suite."""
    monkeypatch.delenv("GTM_FAKE_RUNS", raising=False)
    monkeypatch.delenv("GTM_FAKE_RUN_DELAY_S", raising=False)
    monkeypatch.delenv("GTM_FAKE_RUN_FAIL_NODE", raising=False)
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


@pytest.fixture()
def fake_env(monkeypatch):
    monkeypatch.setenv("GTM_FAKE_RUNS", "1")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("GTM_FAKE_RUN_DELAY_S", "0")


# ── boot guard ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("env", [None, "", "production", "staging", "test", "development"])
def test_flag_off_boots_in_every_env(env):
    for raw in (None, "", "0", "false"):
        assert backend_main.check_fake_runs(raw, env) is False


def test_flag_on_in_development_is_enabled():
    for raw in ("1", "true", "TRUE", " true "):
        assert backend_main.check_fake_runs(raw, "development") is True


@pytest.mark.parametrize("env", [None, "", "production", "staging", "test", "Development"])
def test_flag_on_outside_development_refuses_to_boot(env):
    with pytest.raises(RuntimeError, match="GTM_FAKE_RUNS"):
        backend_main.check_fake_runs("1", env)


def test_lifespan_refuses_before_touching_the_database(monkeypatch):
    """The guard is the lifespan's FIRST step: with DATABASE_URL unset a missing guard would
    surface as a KeyError, so a RuntimeError here proves the wiring, not just the function."""
    monkeypatch.setenv("GTM_FAKE_RUNS", "1")
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    async def _boot():
        async with backend_main.lifespan(FastAPI()):
            pass

    with pytest.raises(RuntimeError, match="GTM_FAKE_RUNS"):
        asyncio.run(_boot())


def test_lifespan_warns_when_fake_runs_are_enabled(monkeypatch, caplog):
    """Boots in development — where every env guard passes — and stops at the first pool
    creation with a sentinel, so what is asserted is the warning and that the guard ran BEFORE
    any database work, not whichever missing setting a later boot step trips over first."""
    monkeypatch.setenv("GTM_FAKE_RUNS", "1")
    monkeypatch.setenv("ENV", "development")  # check_no_dev_secrets returns early here
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/fake_runs_test")
    monkeypatch.delenv("POSTGRES_MIGRATION_URL", raising=False)
    monkeypatch.delenv("TRUSTED_ISSUERS", raising=False)
    order: list[str] = []
    real_check = backend_main.check_fake_runs

    class _PoolSentinel(Exception):
        pass

    def _check(*args):
        order.append("check_fake_runs")
        return real_check(*args)

    async def _create_pool(*_args, **_kwargs):
        order.append("create_pool")
        raise _PoolSentinel

    async def _boot():
        async with backend_main.lifespan(FastAPI()):
            pass

    with (
        patch.object(backend_main, "check_fake_runs", _check),
        patch.object(backend_main, "create_pool", _create_pool),
        caplog.at_level(logging.WARNING),
        pytest.raises(_PoolSentinel),
    ):
        asyncio.run(_boot())
    assert order == ["check_fake_runs", "create_pool"]
    assert any(
        r.levelno == logging.WARNING and "GTM_FAKE_RUNS" in r.getMessage() for r in caplog.records
    )


# ── delay parsing (LD-05) ───────────────────────────────────────────────────


def test_total_delay_s_defaults_when_env_is_unset(monkeypatch):
    monkeypatch.delenv(runs_fake.DELAY_ENV, raising=False)
    assert runs_fake._total_delay_s(None) == runs_fake.DEFAULT_DELAY_S


def test_total_delay_s_falls_back_to_the_default_on_an_empty_string(monkeypatch):
    """docker-compose.dev.yml forwards ``GTM_FAKE_RUN_DELAY_S: ${GTM_FAKE_RUN_DELAY_S:-}``,
    so an operator who never sets the host var still gets it SET in the container, to an
    empty string, not left unset. ``float("")`` raises, and that must land on the same
    default as truly unset — never crash, and never silently become ``0.0``."""
    monkeypatch.setenv(runs_fake.DELAY_ENV, "")
    assert runs_fake._total_delay_s(None) == runs_fake.DEFAULT_DELAY_S


def test_total_delay_s_honours_an_explicit_env_value(monkeypatch):
    monkeypatch.setenv(runs_fake.DELAY_ENV, "3.5")
    assert runs_fake._total_delay_s(None) == 3.5


def test_total_delay_s_an_explicit_argument_wins_over_the_env(monkeypatch):
    monkeypatch.setenv(runs_fake.DELAY_ENV, "3.5")
    assert runs_fake._total_delay_s(0.0) == 0.0


# ── admission on resume (RL-13/ST-06) ──────────────────────────────────────────
# fake.py's ``_admit`` is the scripted twin of ``pack_executor._execute_pack_run``'s own
# top-of-function admission — same skip-on-resume rule, for the same reason (see that
# function's own comment): a run already sitting at ``awaiting_approval`` is being
# RESUMED (a boot reconcile, or a lease reclaim), not freshly started, so the one-time
# ``_reserve_or_deny``/``start_run`` pair must not run a second time against it.


def test_admit_on_a_resume_skips_reserve_and_start():
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    db = LifecycleDb(run_id, ws)
    db.row["status"] = "awaiting_approval"
    reserve = AsyncMock(return_value=True)
    start = AsyncMock()

    async def _go():
        with (
            lifecycle_harness(db) as hz,
            patch.object(runs_fake, "_reserve_or_deny", reserve),
            patch.object(runs_fake, "start_run", start),
        ):
            return await runs_fake._admit(hz.pool, ws, run_id)

    assert asyncio.run(_go()) is True
    reserve.assert_not_called()
    start.assert_not_called()


def test_admit_on_a_fresh_dispatch_still_reserves_and_starts():
    """Regression: a fresh dispatch (never parked at a gate) must still call
    ``_reserve_or_deny``/``start_run`` exactly as before RL-13/ST-06 — only a resume
    (``runs.status`` already ``awaiting_approval`` at dispatch) skips them."""
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    db = LifecycleDb(run_id, ws)  # default status is never "awaiting_approval"
    reserve = AsyncMock(return_value=True)
    start = AsyncMock()

    async def _go():
        with (
            lifecycle_harness(db) as hz,
            patch.object(runs_fake, "_reserve_or_deny", reserve),
            patch.object(runs_fake, "start_run", start),
        ):
            return await runs_fake._admit(hz.pool, ws, run_id)

    assert asyncio.run(_go()) is True
    reserve.assert_awaited_once()
    start.assert_awaited_once()


def test_admit_over_cap_still_fails_a_fresh_run():
    """Negative control: the cap-refusal branch (unmocked ``_reserve_or_deny``) must
    still work for a genuinely fresh dispatch — never conflated with the resume skip
    above, which reads a DIFFERENT signal (``runs.status``, not the budget verdict)."""
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    db = LifecycleDb(run_id, ws)

    async def _go():
        with lifecycle_harness(db, budget=(False,)) as hz:
            return await runs_fake._admit(hz.pool, ws, run_id)

    assert asyncio.run(_go()) is False
    assert db.row["status"] == "failed" and db.row["error_code"] == "cost_cap_reached"


# ── harness ───────────────────────────────────────────────────────────────────


#: The one payload write the queue may issue: a jsonb MERGE, whitespace-normalised.
STAMP_MERGE = (
    "UPDATE runs SET payload = COALESCE(payload, '{}'::jsonb) || jsonb_build_object($3::text, "
    "true) WHERE id = $1::uuid AND workspace_id = $2::uuid"
)


class RecordingDb(LifecycleDb):
    """LifecycleDb that also keeps every SQL statement, so "no cost row" is checkable, and
    emulates the fake stamp on ``row["payload"]`` (seed it with the run's real payload)."""

    def __init__(self, run_id: str, workspace_id: str, payload=None) -> None:
        super().__init__(run_id, workspace_id)
        self.sql: list[str] = []
        self.row["payload"] = None if payload is None else dict(payload)
        self.stamps: list[tuple] = []
        self.stamp_matches_row = True  # False: the stamp UPDATE finds no row

    async def execute(self, sql: str, *args):
        self.sql.append(sql)
        if "SET payload" in sql:
            return self._stamp(sql, args)
        return await super().execute(sql, *args)

    def _stamp(self, sql: str, args: tuple) -> str:
        """Emulates EXACTLY the merge; any other payload write fails the test — a replacing
        ``SET payload = jsonb_build_object(...)`` would wipe mode/pack/inputs. Returns the
        asyncpg status string, so a 0-row write is visible to the caller."""
        assert " ".join(sql.split()) == STAMP_MERGE, sql
        run_id, workspace_id, key = args
        self.stamps.append(args)
        hit = self.stamp_matches_row and (run_id, workspace_id) == (
            self.row["id"],
            self.workspace_id,
        )
        if hit:
            self.row["payload"] = {**(self.row["payload"] or {}), key: True}
        return f"UPDATE {int(hit)}"

    async def fetchval(self, sql: str, *args):
        self.sql.append(sql)
        return await super().fetchval(sql, *args)

    async def fetchrow(self, sql: str, *args):
        self.sql.append(sql)
        row = await super().fetchrow(sql, *args)
        if row is not None and "AS gate_kind" in sql:
            row = {**row, "gate_kind": self._open_gate_kind(args[0])}
        return row

    def _open_gate_kind(self, run_id: str) -> str | None:
        """The ``gate_kind`` subquery: the newest OPEN run_gates row's kind, else NULL."""
        kinds = [
            kind
            for (run, kind, _node), row in self.gates.items()
            if run == run_id and row.get("state") == "open"
        ]
        return kinds[-1] if kinds else None


def _publisher_classes() -> list[type]:
    """Every class agent.publish defines with its own ``publish`` — enumerated rather than
    named, so a sibling publisher is spied on the day it lands."""
    return [
        cls
        for cls in vars(agent_publish).values()
        if isinstance(cls, type)
        and cls.__module__ == agent_publish.__name__
        and "publish" in vars(cls)
    ]


@contextlib.contextmanager
def forbidden_paths():
    """Spies on every path a fake run must never reach: the pack stage executor (SDK), both
    bindings of the backend publish dispatch and of the email-enrollment dispatch, and
    ``publish`` on every agent.publish publisher.

    The publishers are patched on the CLASS: ``LinkedInPublisher.transport`` binds its default
    at class definition, so patching the module-level transport would be inert. Each publisher
    spy raises, so a call fails loudly as well as being counted."""
    spies = SimpleNamespace(stage=AsyncMock(), dispatch=AsyncMock(), enroll=AsyncMock())
    spies.publishers = []
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("agent.packs.execute_stage", spies.stage))
        stack.enter_context(
            patch.object(runs_pack_executor, "dispatch_backend_publish", spies.dispatch)
        )
        stack.enter_context(
            patch("backend.publish_dispatch.dispatch_backend_publish", spies.dispatch)
        )
        stack.enter_context(
            patch.object(runs_pack_executor, "dispatch_backend_email_enroll", spies.enroll)
        )
        stack.enter_context(
            patch("backend.email_dispatch.dispatch_backend_email_enroll", spies.enroll)
        )
        for cls in _publisher_classes():
            spy = AsyncMock(
                side_effect=AssertionError(f"a fake run reached {cls.__name__}.publish")
            )
            stack.enter_context(patch.object(cls, "publish", spy))
            spies.publishers.append(spy)
        yield spies


def _recorder(calls: list[str], kind: str):
    async def _record(*_args, **_kwargs):
        calls.append(kind)

    return _record


def _claimed(run_id: str, workspace_id: str, payload: dict, prev_status: str = "queued") -> dict:
    """A claim_next_run row as dispatch_claimed receives it."""
    return {
        "id": run_id,
        "workspace_id": workspace_id,
        "payload": payload,
        "prev_status": prev_status,
        "attempts": 1,
        "agent_id": None,
        "profile_name": PROFILE,
        "prompt": PROMPT,
    }


async def _dispatch(hz, row: dict, sessions) -> asyncio.Task:
    known = set(state._background_tasks)
    assert await runs_queue.dispatch_claimed(hz.pool, REPO, sessions, row) is True
    (task,) = set(state._background_tasks) - known
    return task


def _gate_open(db: LifecycleDb, run_id: str, node_id: str) -> bool:
    """The durable row at ``node_id`` is open, whatever gate kind it was opened as."""
    return any(
        (run, node) == (run_id, node_id) and row.get("state") == "open"
        for (run, _kind, node), row in db.gates.items()
    )


def _fake_run(ws_env, payload: dict, *decisions: tuple[str, str, str | None]):
    """Dispatch one claimed run through the REAL seam, decide each gate — ``(node_id,
    decision, edited)``, in order — through the REAL endpoint once that node's durable row is
    open, and return everything a test inspects."""
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = RecordingDb(run_id, ws, payload)
    sessions = MagicMock()

    async def _go():
        with lifecycle_harness(db) as hz, forbidden_paths() as spies:
            q = state._subscribe(run_id)
            task = await _dispatch(hz, _claimed(run_id, ws, payload), sessions)
            for node_id, decision, edited in decisions:
                await _until(
                    lambda n=node_id: _gate_open(db, run_id, n), what=f"the {node_id} gate"
                )
                await _decide_via_endpoint(hz, run_id, decision, edited)
            await _settle(task)
            state._unsubscribe(run_id, q)
            return _drain(q), hz, spies

    frames, hz, spies = asyncio.run(_go())
    return SimpleNamespace(
        run_id=run_id, ws=ws, db=db, frames=frames, hz=hz, spies=spies, sessions=sessions
    )


def _assert_spent_nothing_and_published_nothing(run) -> None:
    run.sessions.run.assert_not_called()
    assert run.spies.stage.await_count == 0
    assert run.spies.dispatch.await_count == 0
    assert run.spies.enroll.await_count == 0
    assert run.spies.publishers, "no publisher spied — the publish check would prove nothing"
    assert all(spy.call_count == 0 for spy in run.spies.publishers)
    assert not any("cost_records" in sql for sql in run.db.sql)


def test_publisher_spies_intercept_a_class_level_publish():
    """Negative control for the spies: a publish reached through the class (how every
    instance resolves it) is counted and fails loudly — unlike the module-transport patch it
    replaces, which a LinkedInPublisher never looked up."""
    assert agent_publish.LinkedInPublisher in _publisher_classes()
    with forbidden_paths() as spies:
        with pytest.raises(AssertionError, match="LinkedInPublisher.publish"):
            asyncio.run(agent_publish.LinkedInPublisher.publish(object()))
    assert sum(spy.call_count for spy in spies.publishers) == 1


# ── the seam ──────────────────────────────────────────────────────────────────


def test_prompt_mode_fake_run_gates_then_approve_registers_artifact_and_completes(ws_env, fake_env):
    """The full client flow through the real seam: queued row → running → nodes → one
    durable plan gate (push sent, stale sha 409s) → approve → one markdown artifact
    registered and downloadable → ok. Every frame, live and late, is contract-valid."""
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = RecordingDb(run_id, ws, PROMPT_PAYLOAD)
    sessions = MagicMock()
    name = f"fake-run-{run_id[:8]}.md"

    async def _go():
        with lifecycle_harness(db) as hz, forbidden_paths() as spies:
            q = state._subscribe(run_id)
            task = await _dispatch(hz, _claimed(run_id, ws, PROMPT_PAYLOAD), sessions)
            await _until(
                lambda: run_id in state._gate_events and db.row["status"] == "awaiting_approval",
                what="the fake gate",
            )
            stale = GateRequest(decision="approve", content_sha="0" * 64)
            with pytest.raises(HTTPException) as refused:
                await lifecycle.decide_gate(run_id, stale, _ws_ctx(ws), FakeRequest(hz.pool))
            await _decide_via_endpoint(hz, run_id, "approve")
            await _settle(task)
            state._unsubscribe(run_id, q)
            late = await lifecycle.stream_run(run_id, _ws_ctx(ws), FakeRequest(hz.pool))
            late_frames = _parse_sse("".join([c async for c in late.body_iterator]))
            return refused.value, _drain(q), late_frames, hz, spies

    refused, frames, late_frames, hz, spies = asyncio.run(_go())
    run = SimpleNamespace(db=db, frames=frames, spies=spies, sessions=sessions)
    gate_text = runs_fake_script._gate_content("draft", runs_fake_script.FAKE_DRAFT)

    assert refused.status_code == 409
    assert _keys(frames) == [
        ("status", "running"),
        ("node", "research", "running"),
        ("node", "research", "completed"),
        ("content", "node-research"),
        ("node", "draft", "running"),
        ("node", "draft", "running"),
        ("awaiting_approval", "plan", "draft", _sha(gate_text)),
        ("gate_resolved", "approve", False),
        ("node", "draft", "completed"),
        ("status", "running"),
        ("node", "deliver", "running"),
        ("node", "deliver", "completed"),
        ("content", "node-deliver"),
        ("content", "file", name),
        ("done", "ok"),
    ]
    for event, data in frames + late_frames:
        assert not validate_frame(event, data), (event, data)
    assert [event for event, _ in late_frames] == ["snapshot", "done"]
    awaiting = next(d for e, d in frames if e == "awaiting_approval")
    assert awaiting["pending_gate"] == PLAN_GATE
    assert awaiting["pending_content"] == gate_text
    assert "Example Widgets Ltd" in awaiting["pending_content"]
    assert "FAKE RUN" in awaiting["pending_content"].splitlines()[0]  # client guide + e2e rely
    assert frames[-1][1]["output"] == runs_fake_script.FAKE_DRAFT

    assert db.run_status == ["running", "awaiting_approval", "running", "ok"]
    gate_row = db.gates[(run_id, "plan", "draft")]
    assert gate_row["state"] == "approved" and gate_row["applied_at"] is not None
    hz.push.assert_awaited_once_with(hz.pool, ws, run_id, "plan", node_id="draft")
    assert hz.budget.await_count == 2  # admission + the post-approval re-gate
    _assert_spent_nothing_and_published_nothing(run)

    (artifact,) = db.artifacts.values()
    assert artifact["node_id"] == "deliver" and artifact["name"] == name
    with lifecycle_harness(db), _artifact_client(hz, ws) as client:
        listing = client.get(f"/v1/runs/{run_id}/artifacts")
        download = client.get(f"/v1/runs/{run_id}/artifacts/{artifact['id']}")
    assert listing.status_code == 200
    assert [a["name"] for a in listing.json()["artifacts"]] == [name]
    assert download.status_code == 200
    assert runs_fake_script.FAKE_DRAFT in download.text
    assert download.headers["content-type"].startswith("text/markdown")
    assert run_id not in state._gate_events and ws not in state._workspace_runs


def test_pack_mode_fake_run_holds_every_declared_gate_and_never_reaches_the_runner(
    ws_env, fake_env
):
    """A pack request scripts its variant's REAL node ids (so a client's DAG view joins onto
    the descriptor) and holds a durable gate at EVERY node the variant gates — plan and
    publish, as the real runner does — while the stage executor, the publish dispatch the
    `publish` node declares, and every publisher are never touched."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run = _fake_run(ws_env, PACK_PAYLOAD, ("plan", "approve", None), ("publish", "approve", None))
    name = f"fake-run-{run.run_id[:8]}.md"
    plan_text = runs_fake_script._gate_content("plan", runs_fake_script.FAKE_DRAFT)
    publish_text = runs_fake_script._gate_content("publish", runs_fake_script.FAKE_DRAFT)
    assert _sha(plan_text) != _sha(publish_text)  # H9: no two gates share bytes

    def _plain(node_id: str) -> list[tuple]:
        return [
            ("node", node_id, "running"),
            ("node", node_id, "completed"),
            ("content", f"node-{node_id}"),
        ]

    assert _keys(run.frames) == [
        ("status", "running"),
        *_plain("radar"),
        ("node", "plan", "running"),
        ("node", "plan", "running"),
        ("awaiting_approval", "plan", "plan", _sha(plan_text)),
        ("gate_resolved", "approve", False),
        ("node", "plan", "completed"),
        ("status", "running"),
        *_plain("research"),
        *_plain("studio"),
        ("node", "publish", "running"),
        ("node", "publish", "running"),
        ("awaiting_approval", "publish", "publish", _sha(publish_text)),
        ("gate_resolved", "approve", False),
        ("node", "publish", "completed"),
        ("content", "node-publish"),
        ("content", "file", name),
        ("status", "running"),
        ("done", "ok"),
    ]
    assert run.db.run_status == [
        "running",
        "awaiting_approval",
        "running",
        "awaiting_approval",
        "running",
        "ok",
    ]
    draft_first_line = runs_fake_script.FAKE_DRAFT.splitlines()[0]
    assert [
        d["pending_content"].splitlines()[:2] for e, d in run.frames if e == "awaiting_approval"
    ] == [
        [
            f"FAKE RUN — gate at node '{node}': approve, edit or reject the text below.",
            draft_first_line,
        ]
        for node in ("plan", "publish")
    ]
    assert run.frames[-1][1]["output"] == runs_fake_script.FAKE_DRAFT
    assert sorted((kind, node) for (_, kind, node) in run.db.gates) == [
        ("plan", "plan"),
        ("publish", "publish"),
    ]
    assert all(g["state"] == "approved" and g["applied_at"] for g in run.db.gates.values())
    assert run.hz.push.await_count == 2
    assert run.hz.budget.await_count == 3  # admission + one re-gate per approval
    assert [a["node_id"] for a in run.db.artifacts.values()] == ["publish"]
    for event, data in run.frames:
        assert not validate_frame(event, data), (event, data)
    _assert_spent_nothing_and_published_nothing(run)


def test_pack_mode_fake_run_carries_the_edit_to_the_publish_gate_and_a_reject_writes_nothing(
    ws_env, fake_env
):
    """Each later gate shows the text approved so far under its OWN naming line — an edit
    that kept the plan gate's line (a client editing the prefilled text) does not stack it —
    and rejecting the LAST gate still ends `rejected` with no artifact."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    body = "Example Widgets Ltd — edited at the plan gate of a fake pack run."
    edited = runs_fake_script._gate_content("plan", body)
    run = _fake_run(ws_env, PACK_PAYLOAD, ("plan", "edit", edited), ("publish", "reject", None))

    assert run.db.run_status == [
        "running",
        "awaiting_approval",
        "running",
        "awaiting_approval",
        "rejected",
    ]
    publish_gate = next(
        d for e, d in run.frames if e == "awaiting_approval" and d["node_id"] == "publish"
    )
    assert run.db.pending_content_rewrites == [edited]
    assert publish_gate["pending_content"] == runs_fake_script._gate_content("publish", body)
    assert run.db.gates[(run.run_id, "publish", "publish")]["state"] == "rejected"
    assert _keys(run.frames)[-1] == ("done", "rejected")
    assert run.db.artifacts == {}
    assert not (ws_env.content_root / PROFILE / "fake-runs").exists()
    _assert_spent_nothing_and_published_nothing(run)


def test_pack_mode_a_plan_decision_resent_at_the_publish_gate_is_refused(ws_env, fake_env):
    """H9: the plan gate's decision, re-sent (a retry, a double-tap, a stale tab) while the
    publish gate is open, carries the PLAN gate's content_sha — so it must 409 through the
    real decide path, leaving the publish gate open for its own decision."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = RecordingDb(run_id, ws, PACK_PAYLOAD)

    async def _go():
        with lifecycle_harness(db) as hz, forbidden_paths():
            task = await _dispatch(hz, _claimed(run_id, ws, PACK_PAYLOAD), MagicMock())
            await _until(lambda: _gate_open(db, run_id, "plan"), what="the plan gate")
            plan_sha = _sha(db.row["pending_content"])
            await _decide_via_endpoint(hz, run_id, "approve")
            await _until(lambda: _gate_open(db, run_id, "publish"), what="the publish gate")
            resent = GateRequest(decision="approve", content_sha=plan_sha)
            with pytest.raises(HTTPException) as refused:
                await lifecycle.decide_gate(run_id, resent, _ws_ctx(ws), FakeRequest(hz.pool))
            still_open = _gate_open(db, run_id, "publish")
            publish_sha = _sha(db.row["pending_content"])
            await _decide_via_endpoint(hz, run_id, "approve")
            await _settle(task)
            return refused.value, still_open, plan_sha, publish_sha

    refused, still_open, plan_sha, publish_sha = asyncio.run(_go())
    assert plan_sha != publish_sha
    assert refused.status_code == 409 and refused.detail["code"] == "content_sha_mismatch"
    assert still_open
    assert db.run_status[-1] == "ok"


def test_fake_run_reject_ends_rejected_with_no_artifact(ws_env, fake_env):
    run = _fake_run(ws_env, PROMPT_PAYLOAD, ("draft", "reject", None))

    assert run.db.run_status == ["running", "awaiting_approval", "rejected"]
    assert _keys(run.frames)[-2:] == [("gate_resolved", "reject", False), ("done", "rejected")]
    assert run.db.gates[(run.run_id, "plan", "draft")]["state"] == "rejected"
    assert run.db.artifacts == {}
    assert not (ws_env.content_root / PROFILE / "fake-runs").exists()
    assert run.hz.budget.await_count == 1  # no re-gate after a rejection
    _assert_spent_nothing_and_published_nothing(run)


def test_fake_run_edit_delivers_the_edited_bytes(ws_env, fake_env):
    edited = "Example Widgets Ltd — edited wording for the fake run."
    run = _fake_run(ws_env, PROMPT_PAYLOAD, ("draft", "edit", edited))

    assert run.db.pending_content_rewrites == [edited]
    resolved = next(d for e, d in run.frames if e == "gate_resolved")
    assert resolved["edited"] is True and resolved["content_sha"] == _sha(edited)
    assert run.frames[-1][1] == {"run_id": run.run_id, "status": "ok", "output": edited}
    (artifact,) = run.db.artifacts.values()
    written = (ws_env.content_root / artifact["rel_path"]).read_text(encoding="utf-8")
    assert edited in written and runs_fake_script.FAKE_DRAFT not in written


def test_fake_run_gate_timeout_fails_like_a_real_run(ws_env, fake_env):
    """hold_gate is the same code, so an undecided gate fails with `gate timeout`."""
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = RecordingDb(run_id, ws, PROMPT_PAYLOAD)

    async def _go():
        with (
            lifecycle_harness(db) as hz,
            forbidden_paths(),
            patch.object(runs_lifecycle, "GATE_TIMEOUT_S", 0.05),
        ):
            q = state._subscribe(run_id)
            task = await _dispatch(hz, _claimed(run_id, ws, PROMPT_PAYLOAD), MagicMock())
            await _settle(task)
            state._unsubscribe(run_id, q)
            return _drain(q)

    frames = asyncio.run(_go())
    assert db.run_status == ["running", "awaiting_approval", "failed"]
    assert db.row["error"] == "gate timeout"
    assert _keys(frames)[-1] == ("done", "failed", "gate timeout")


# ── gate kinds: parity with the real pack runner (LD-03) ──────────────────

_VARIANT_GRAPHS = sorted((REPO / "packs").glob("*/graphs/*.toml"))

#: The ONE named divergence — RL-01. A real pack run never pauses at a node declaring
#: ``external_effect = "publish"`` (``execute_stage`` short-circuits it to SKIPPED before its
#: gate check), while the fake holds a ``publish`` gate there because the app needs that screen
#: locally. The phase that fixes RL-01 deletes this: the parity test fails as soon as a real
#: run pauses there too, and on any divergence this does not name.
RL01_FAKE_ONLY_GATE_EFFECT = "publish"

#: A gate draft as the skill that declares it writes it (fictional contents). The enroll draft
#: is named after its run (``agent.gate_actions.enroll_draft_path``), so it carries a
#: ``{run_id}`` placeholder filled in by whoever writes it.
_DECLARED_DRAFTS = {
    "plan": ("plans/.pending/2026-01.draft.json", "[]"),
    "enroll": (
        "prospects/sequences/.pending/{run_id}.enroll-draft.json",
        json.dumps(
            {
                "tool": "import_prospects_to_sequence",
                "sequence_id": "seq-parity",
                "step_id": "step-parity",
                "steps": [
                    {
                        "step_id": "step-parity",
                        "variants": [
                            {"subject": "Hello {{Company}}", "content": "<p>Hi {{First Name}}</p>"}
                        ],
                    }
                ],
                "prospect_list": [
                    {
                        "Email": "pat.example@example.com",
                        "First Name": "Pat",
                        "Last Name": "Example",
                        "Company": "Example Widgets Ltd",
                    }
                ],
            }
        ),
    ),
    # SC9. Named after its run for the same reason the enroll draft is: the profile lock
    # is released while a run waits at its gate, so `.pending` can hold another run's.
    "dnc": (
        "prospects/sequences/.pending/{run_id}.dnc-draft.json",
        json.dumps(
            {
                "addresses": ["pat.example@example.com"],
                "evidence": {
                    "pat.example@example.com": {
                        "event": "optout_detected",
                        "thread_id": "thread-parity",
                        "ts": "2026-09-21T10:00:00Z",
                    }
                },
            }
        ),
    ),
}


def _declared_draft(skill: str | None) -> str | None:
    """Which gate draft a skill's own body says it writes — read from the skill, not from
    either executor, so the parity below is checked against an independent oracle."""
    if skill is None:
        return None
    body = (REPO / "plugin" / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    if "plans/.pending/" in body:
        return "plan"
    if ".enroll-draft.json" in body:
        return "enroll"
    if ".dnc-draft.json" in body:
        return "dnc"
    return None


def _gate_kinds_held(ws_env, graph_path) -> tuple[dict[str, str], dict[str, str]]:
    """Drive a REAL pack run and a FAKE one of the same variant to completion, approving every
    gate, and return ``{node_id: gate kind}`` each actually held. The real run uses the real
    ``execute_stage`` (only the model call is stubbed) and writes each draft its node's skill
    declares, so where it pauses and which draft it finds are the engine's own answers."""
    pack, variant = graph_path.parts[-3], graph_path.stem
    skills = {n.id: n.skill for n in load_pack_graph(graph_path).nodes}
    real_stage = pipeline_executor.execute_stage
    held: dict[str, dict[str, str]] = {"real": {}, "fake": {}}
    done = {"real": AsyncMock(), "fake": AsyncMock()}
    failed = AsyncMock()

    async def _stage(cfg, profile, stage_name, manifest, **kwargs):
        declared = _declared_draft(skills[stage_name])
        if declared is not None:
            rel, body = _DECLARED_DRAFTS[declared]
            path = cfg.content_root / profile / rel.format(run_id=manifest.get("run_id"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return await real_stage(cfg, profile, stage_name, manifest, **kwargs)

    def _approve(side: str):
        async def _hold(_pool, _workspace_id, _run_id, *, gate, node_id=None, **_kwargs):
            held[side][node_id] = gate
            return {"decision": "approve", "edited_content": None}

        return _hold

    async def _go():
        ws = ws_env.ws_id
        real_run, fake_run = str(uuid.uuid4()), str(uuid.uuid4())
        await runs_pack_executor._execute_pack_run(
            MagicMock(), REPO, ws, real_run, PROFILE, pack, variant, {}, entitlement="pro_plus"
        )
        await runs_fake._execute_fake_run(
            MagicMock(), REPO, ws, fake_run, PROFILE, pack=pack, variant=variant, delay_s=0
        )

    enrolled = AsyncMock(return_value=EnrollDispatchOutcome(ok=True, status="enrolled"))
    with (
        pack_run_harness(StateConn(), _stage),
        patch.object(pipeline_executor, "call_with_fallback", AsyncMock(return_value="")),
        patch.object(runs_pack_executor, "dispatch_backend_email_enroll", enrolled),
        patch.object(runs_pack_executor, "hold_gate", _approve("real")),
        patch.object(runs_fake, "hold_gate", _approve("fake")),
        patch.object(runs_pack_executor, "complete_run", done["real"]),
        patch.object(runs_fake, "complete_run", done["fake"]),
        patch.object(runs_pack_executor, "_fail_run", failed),
        patch.object(runs_fake, "_fail_run", failed),
    ):
        asyncio.run(_go())
    assert failed.await_args_list == [], f"{pack}/{variant} failed"
    assert (done["real"].await_count, done["fake"].await_count) == (1, 1), f"{pack}/{variant}"
    return held["real"], held["fake"]


def test_the_draft_oracle_reads_both_draft_kinds_from_the_skills():
    """§R12: the parity below means something only if the oracle can find each draft kind."""
    assert _declared_draft("content-plan") == "plan"
    assert _declared_draft("email-sequence") == "enroll"
    assert _declared_draft("case-study") is None


def test_every_fake_pack_gate_reports_the_kind_a_real_run_holds_there(ws_env):
    """For every shipped variant and every gate, a fake run holds the same gate kind a real
    run holds at that node — and holds none where a real run holds none (``sequence-enroll``
    is enrolled inline by the approval of ``sequence``, never gated itself). The only
    exception is the named RL-01 divergence, which must appear exactly where it is declared."""
    packs = sorted({path.parts[-3] for path in _VARIANT_GRAPHS})
    _provision(ws_env.profiles_root, packs_toml=f"active = {json.dumps(packs)}\n")
    mismatches: dict[str, dict] = {}
    real_kinds: set[str] = set()
    for graph_path in _VARIANT_GRAPHS:
        real, fake = _gate_kinds_held(ws_env, graph_path)
        real_kinds |= set(real.values())
        diverging = {
            node: (real.get(node), fake.get(node))
            for node in real.keys() | fake.keys()
            if real.get(node) != fake.get(node)
        }
        rl01 = {
            node.id: (None, RL01_FAKE_ONLY_GATE_EFFECT)
            for node in load_pack_graph(graph_path).nodes
            if node.external_effect == RL01_FAKE_ONLY_GATE_EFFECT
        }
        if diverging != rl01:
            mismatches[f"{graph_path.parts[-3]}/{graph_path.stem}"] = diverging
    assert mismatches == {}
    # Every kind was exercised. `dnc_add` joined the set on 2026-09-21 (SC9) — the third
    # member of the engine's closed `external_effect` vocabulary, and the third gate kind
    # a real pack run can hold.
    assert real_kinds == {"plan", "email_enroll", "dnc_add", "review"}


def _real_prospecting_frames(ws_env) -> list[tuple[str, dict]]:
    """The frames a REAL prospect-outreach run emits when its ``sequence`` gate is approved:
    the real pack runner and decide path, with only the stage work (a twin that writes the
    enroll-draft at ``sequence``) and the enrollment dispatch stubbed."""
    run_id = str(uuid.uuid4())
    db = LifecycleDb(run_id, ws_env.ws_id)
    rel, body = _DECLARED_DRAFTS["enroll"]
    stage = fake_executor(
        [],
        awaiting_on="sequence",
        files_by_stage={"sequence": [(rel.format(run_id=run_id), body.encode())]},
    )
    enrolled = AsyncMock(return_value=EnrollDispatchOutcome(ok=True, status="enrolled"))

    async def _go():
        with (
            lifecycle_harness(db, stage) as hz,
            patch.object(runs_pack_executor, "dispatch_backend_email_enroll", enrolled),
        ):
            q = state._subscribe(run_id)
            task = asyncio.create_task(
                lifecycle._execute_pack_run(
                    hz.pool,
                    REPO,
                    ws_env.ws_id,
                    run_id,
                    PROFILE,
                    "prospecting",
                    "prospect-outreach",
                    {},
                    entitlement="pro_plus",
                )
            )
            await _decide_via_endpoint(hz, run_id, "approve")
            await _settle(task)
            state._unsubscribe(run_id, q)
            return _drain(q)

    frames = asyncio.run(_go())
    assert enrolled.await_count == 1 and db.run_status[-1] == "ok"
    return frames


def _shape(frames: list[tuple[str, dict]], *, drop_file: bool = False) -> list[tuple]:
    """Frame keys with the gate's sha elided (its bytes differ by design), and optionally the
    fake-only ``file`` block dropped."""
    shaped = []
    for key in _keys(frames):
        if key[0] == "awaiting_approval":
            key = key[:3]
        if not (drop_file and key[:2] == ("content", "file")):
            shaped.append(key)
    return shaped


def test_pack_mode_fake_prospecting_run_holds_an_email_enroll_gate_and_enrolls_nothing(
    ws_env, fake_env
):
    """The prospecting ``sequence`` gate surfaces locally as a real one does: kind
    ``email_enroll``, its pending content an ``import_prospects_to_sequence`` enroll-draft the
    real decision path parses. Approving it completes ``sequence-enroll`` inline in the REAL
    run's frame order — asserted against a real run of the same variant — and neither
    dispatcher is ever reached."""
    _provision(ws_env.profiles_root, packs_toml='active = ["prospecting"]\n')
    real = _real_prospecting_frames(ws_env)
    run = _fake_run(ws_env, PROSPECTING_PAYLOAD, ("sequence", "approve", None))
    name = f"fake-run-{run.run_id[:8]}.md"
    (awaiting,) = [d for e, d in run.frames if e == "awaiting_approval"]
    stub = awaiting["pending_content"]

    draft = gate_actions.parse_enroll_draft(stub, "the fake sequence gate")
    assert draft["tool"] == "import_prospects_to_sequence"
    assert draft["prospect_list"] and draft["steps"]
    assert all(
        {"Email", "First Name", "Last Name", "Company"} <= set(p) for p in draft["prospect_list"]
    )
    assert all(p["Email"].endswith("@example.com") for p in draft["prospect_list"])
    assert "FAKE RUN" in stub.splitlines()[0]

    def _plain(node_id: str) -> list[tuple]:
        return [
            ("node", node_id, "running"),
            ("node", node_id, "completed"),
            ("content", f"node-{node_id}"),
        ]

    assert _shape(run.frames, drop_file=True) == _shape(real)
    assert _keys(run.frames) == [
        ("status", "running"),
        *_plain("prospect"),
        *_plain("dossier"),
        *_plain("outreach"),
        *_plain("quality"),
        ("node", "sequence", "running"),
        ("node", "sequence", "running"),  # the pause, re-emitted as a real runner does
        ("awaiting_approval", "email_enroll", "sequence", _sha(stub)),
        ("gate_resolved", "approve", False),
        ("node", "sequence", "completed"),
        ("node", "sequence-enroll", "completed"),
        ("content", "node-sequence-enroll"),
        ("content", "file", name),
        ("status", "running"),
        ("done", "ok"),
    ]
    (outcome,) = [
        d["block"]
        for e, d in run.frames
        if e == "content" and d["node_id"] == "sequence-enroll" and d["block"]["type"] == "markdown"
    ]
    assert outcome == {
        "id": "node-sequence-enroll",
        "type": "markdown",
        "props": {"text": runs_fake_script.FAKE_ENROLL_OUTCOME},
        "fallback_text": runs_fake_script.FAKE_ENROLL_OUTCOME,
    }
    assert "FAKE RUN" in outcome["props"]["text"] and "nobody" in outcome["props"]["text"]
    assert run.db.run_status == ["running", "awaiting_approval", "running", "ok"]
    assert run.db.gates[(run.run_id, "email_enroll", "sequence")]["state"] == "approved"
    assert list(run.db.gates) == [(run.run_id, "email_enroll", "sequence")]
    run.hz.push.assert_awaited_once_with(
        run.hz.pool, run.ws, run.run_id, "email_enroll", node_id="sequence"
    )
    assert run.frames[-1][1]["output"] == stub
    for event, data in run.frames:
        assert not validate_frame(event, data), (event, data)
    _assert_spent_nothing_and_published_nothing(run)


def test_pack_mode_fake_review_gate_refuses_an_edit_as_a_real_one_does(ws_env, fake_env):
    """A gate with no draft of its own is kind ``review`` locally too, so the real decide
    path's 422-on-edit is reproducible without a model key; approve still completes."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = RecordingDb(run_id, ws, CASE_STUDY_PAYLOAD)

    async def _go():
        with lifecycle_harness(db) as hz, forbidden_paths():
            task = await _dispatch(hz, _claimed(run_id, ws, CASE_STUDY_PAYLOAD), MagicMock())
            await _until(lambda: _gate_open(db, run_id, "case-study"), what="the review gate")
            edit = GateRequest(
                decision="edit",
                edited_content="Example Widgets Ltd — an edit nothing would apply.",
                content_sha=_sha(db.row["pending_content"]),
            )
            with pytest.raises(HTTPException) as refused:
                await lifecycle.decide_gate(run_id, edit, _ws_ctx(ws), FakeRequest(hz.pool))
            await _decide_via_endpoint(hz, run_id, "approve")
            await _settle(task)
            return refused.value

    refused = asyncio.run(_go())
    assert refused.status_code == 422
    assert list(db.gates) == [(run_id, "review", "case-study")]
    assert db.gates[(run_id, "review", "case-study")]["state"] == "approved"
    assert db.run_status[-1] == "ok"


# ── failure injection: GTM_FAKE_RUN_FAIL_NODE (LD-04) ─────────────────────


def _injected(node_id: str) -> str:
    return f"fake failure injected at node '{node_id}' by GTM_FAKE_RUN_FAIL_NODE"


def test_pack_mode_fake_run_fails_at_the_named_node_like_a_real_node_failure(
    ws_env, fake_env, monkeypatch
):
    """The node fails and the run ends ``failed`` through the real failure path: a failed
    ``run_nodes`` row carrying the error, a ``node … failed`` frame, then ``done failed``."""
    monkeypatch.setenv("GTM_FAKE_RUN_FAIL_NODE", "prospect")
    _provision(ws_env.profiles_root, packs_toml='active = ["prospecting"]\n')
    run = _fake_run(ws_env, PROSPECTING_PAYLOAD)
    error = _injected("prospect")

    assert _keys(run.frames) == [
        ("status", "running"),
        ("node", "prospect", "running"),
        ("node", "prospect", "failed"),
        ("done", "failed", error),
    ]
    assert run.db.run_status == ["running", "failed"]
    assert run.db.row["error"] == error
    assert run.db.nodes == {"prospect": {"state": "failed", "error": error}}
    assert run.db.gates == {} and run.db.artifacts == {}
    for event, data in run.frames:
        assert not validate_frame(event, data), (event, data)
    _assert_spent_nothing_and_published_nothing(run)


def test_prompt_mode_fake_run_fails_at_its_scripted_gate_node_before_the_gate(
    ws_env, fake_env, monkeypatch
):
    """A failing node never reaches its gate — a failed stage is not a pause."""
    monkeypatch.setenv("GTM_FAKE_RUN_FAIL_NODE", "draft")
    run = _fake_run(ws_env, PROMPT_PAYLOAD)
    error = _injected("draft")

    assert _keys(run.frames) == [
        ("status", "running"),
        ("node", "research", "running"),
        ("node", "research", "completed"),
        ("content", "node-research"),
        ("node", "draft", "running"),
        ("node", "draft", "failed"),
        ("done", "failed", error),
    ]
    assert run.db.run_status == ["running", "failed"]
    assert run.db.nodes["draft"] == {"state": "failed", "error": error}
    assert "deliver" not in run.db.nodes
    assert run.db.gates == {} and run.db.artifacts == {}
    run.hz.push.assert_not_awaited()


def test_fake_run_failing_at_a_later_node_completes_the_earlier_ones_and_never_runs_the_rest(
    ws_env, fake_env, monkeypatch
):
    """Earlier nodes — an approved gate included — complete; the named one fails; nothing
    after it runs, so the later ``publish`` gate never opens."""
    monkeypatch.setenv("GTM_FAKE_RUN_FAIL_NODE", "studio")
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run = _fake_run(ws_env, PACK_PAYLOAD, ("plan", "approve", None))
    error = _injected("studio")

    assert run.db.nodes == {
        "radar": {"state": "completed", "error": None},
        "plan": {"state": "completed", "error": None},
        "research": {"state": "completed", "error": None},
        "studio": {"state": "failed", "error": error},
    }
    assert _keys(run.frames)[-3:] == [
        ("node", "studio", "running"),
        ("node", "studio", "failed"),
        ("done", "failed", error),
    ]
    assert run.db.run_status == ["running", "awaiting_approval", "running", "failed"]
    assert list(run.db.gates) == [(run.run_id, "plan", "plan")]
    assert run.db.artifacts == {}


@pytest.mark.parametrize("raw", [None, "", "  "], ids=["unset", "empty", "blank"])
def test_fake_run_with_no_fail_node_completes_as_before(ws_env, fake_env, monkeypatch, raw):
    """docker-compose.dev.yml forwards ``${GTM_FAKE_RUN_FAIL_NODE:-}``, so a host that never
    sets it hands the container an EMPTY string — which must mean off, exactly like unset."""
    if raw is not None:
        monkeypatch.setenv("GTM_FAKE_RUN_FAIL_NODE", raw)
    run = _fake_run(ws_env, PROMPT_PAYLOAD, ("draft", "approve", None))

    assert run.db.run_status == ["running", "awaiting_approval", "running", "ok"]
    assert _keys(run.frames)[-1] == ("done", "ok")
    assert {node: row["state"] for node, row in run.db.nodes.items()} == {
        "research": "completed",
        "draft": "completed",
        "deliver": "completed",
    }


def test_a_fail_node_the_run_does_not_have_is_ignored_with_one_warning(
    ws_env, fake_env, monkeypatch, caplog
):
    """The knob is stack-wide while node ids differ per variant, so a value this run lacks
    (another variant's node, or a typo) must not fail every other flow — the run completes,
    and a WARNING names the value and the nodes it could have been, once per script."""
    monkeypatch.setenv("GTM_FAKE_RUN_FAIL_NODE", "no-such-node")
    runs_fake_script._warn_unknown_fail_node.cache_clear()  # "once" is per process
    with caplog.at_level(logging.WARNING, logger=runs_fake_script.__name__):
        first = _fake_run(ws_env, PROMPT_PAYLOAD, ("draft", "approve", None))
        second = _fake_run(ws_env, PROMPT_PAYLOAD, ("draft", "approve", None))

    assert first.db.run_status[-1] == "ok" and second.db.run_status[-1] == "ok"
    warnings = [r.getMessage() for r in caplog.records if r.name == runs_fake_script.__name__]
    assert warnings == [
        "GTM_FAKE_RUN_FAIL_NODE='no-such-node' names no node of this fake run "
        "(research, draft, deliver) — ignored, the run is not failed"
    ]


def test_a_fail_node_an_approval_completes_inline_is_one_the_run_never_reaches(
    ws_env, fake_env, monkeypatch, caplog
):
    """``sequence-enroll`` never runs on its own — approving ``sequence`` completes it — so
    naming it fails nothing; the warning says so instead of the knob silently doing nothing."""
    monkeypatch.setenv("GTM_FAKE_RUN_FAIL_NODE", "sequence-enroll")
    _provision(ws_env.profiles_root, packs_toml='active = ["prospecting"]\n')
    runs_fake_script._warn_unknown_fail_node.cache_clear()
    with caplog.at_level(logging.WARNING, logger=runs_fake_script.__name__):
        run = _fake_run(ws_env, PROSPECTING_PAYLOAD, ("sequence", "approve", None))

    assert run.db.run_status[-1] == "ok"
    assert [r.getMessage() for r in caplog.records if r.name == runs_fake_script.__name__] == [
        "GTM_FAKE_RUN_FAIL_NODE='sequence-enroll' names no node of this fake run "
        "(prospect, dossier, outreach, quality, sequence) — ignored, the run is not failed"
    ]


def test_a_cancelled_run_reaching_its_fail_node_stays_canceled(ws_env, fake_env, monkeypatch):
    """Cancel lands mid-node (``prospect`` is in flight). RL-02: unlike before, the run does
    NOT carry on to ``dossier`` — the next node — let alone all the way to ``quality`` (the
    node the knob names): the between-nodes cancel check in ``fake._play`` stops it right
    after ``prospect`` finishes. Either way it must not then flip ``canceled`` to ``failed``
    with a second ``done`` frame — this pins that RL-02 didn't reintroduce THAT bug while
    fixing the "carries on" one."""
    monkeypatch.setenv("GTM_FAKE_RUN_FAIL_NODE", "quality")
    _provision(ws_env.profiles_root, packs_toml='active = ["prospecting"]\n')
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = RecordingDb(run_id, ws, PROSPECTING_PAYLOAD)
    entered, release = asyncio.Event(), asyncio.Event()
    real_observer = runs_fake._node_observer

    def _observer(*args):
        """The real observer, holding the run just after ``prospect`` reports running."""
        on_node = real_observer(*args)

        async def _on_node(node_id, node_state, entry):
            await on_node(node_id, node_state, entry)
            if (node_id, node_state) == ("prospect", "running"):
                entered.set()
                await release.wait()

        return _on_node

    async def _go():
        with (
            lifecycle_harness(db) as hz,
            forbidden_paths(),
            patch.object(runs_fake, "_node_observer", _observer),
        ):
            q = state._subscribe(run_id)
            task = await _dispatch(hz, _claimed(run_id, ws, PROSPECTING_PAYLOAD), MagicMock())
            await asyncio.wait_for(entered.wait(), timeout=10)
            await lifecycle.cancel_run(run_id, _ws_ctx(ws), FakeRequest(hz.pool))
            release.set()
            await _settle(task)
            state._unsubscribe(run_id, q)
            return _drain(q)

    frames = asyncio.run(_go())
    assert [key for key in _keys(frames) if key[0] == "done"] == [
        ("done", "canceled", "canceled by user")
    ]
    assert db.row["status"] == "canceled" and db.row["error"] == "canceled by user"
    assert "failed" not in db.run_status
    assert all(row["state"] != "failed" for row in db.nodes.values())
    # RL-02: prospect (already in flight when the cancel landed) finishes, but dossier —
    # the very next node — never starts: no on_node call for it at all, let alone reaching
    # outreach/quality (the fail node) three nodes further on.
    assert db.nodes == {"prospect": {"state": "completed", "error": None}}
    assert [key for key in _keys(frames) if key[0] == "node"] == [
        ("node", "prospect", "running"),
        ("node", "prospect", "completed"),
    ]


def test_a_valid_edit_at_a_fake_email_enroll_gate_is_carried_on(ws_env, fake_env):
    _provision(ws_env.profiles_root, packs_toml='active = ["prospecting"]\n')
    edited = _DECLARED_DRAFTS["enroll"][1]
    run = _fake_run(ws_env, PROSPECTING_PAYLOAD, ("sequence", "edit", edited))

    assert run.db.run_status == ["running", "awaiting_approval", "running", "ok"]
    assert run.db.pending_content_rewrites == [edited]
    assert run.frames[-1][1]["output"] == edited
    _assert_spent_nothing_and_published_nothing(run)


def test_an_invalid_edit_at_a_fake_email_enroll_gate_fails_the_run_as_a_real_one_does(
    ws_env, fake_env
):
    """A real run fails when the edited bytes are not an enroll-draft (promote_gate_draft →
    parse_enroll_draft); the fake applies the same parser, before recording the edit."""
    _provision(ws_env.profiles_root, packs_toml='active = ["prospecting"]\n')
    edited = json.dumps({"tool": "add_leads", "sequence_id": "seq-1", "step_id": "step-1"})
    run = _fake_run(ws_env, PROSPECTING_PAYLOAD, ("sequence", "edit", edited))
    error = (
        f"fake-run-{run.run_id[:8]}.enroll-draft.json: draft 'tool' must be one of "
        "['add_leads_to_sequence', 'import_prospects_to_sequence'], got 'add_leads'"
    )

    assert run.db.run_status == ["running", "awaiting_approval", "failed"]
    assert run.db.row["error"] == error
    assert run.db.row["error_code"] == "draft_invalid"
    assert _keys(run.frames)[-1] == ("done", "failed", error)
    assert run.db.pending_content_rewrites == []
    assert "sequence-enroll" not in run.db.nodes and run.db.artifacts == {}
    _assert_spent_nothing_and_published_nothing(run)


def test_the_fake_finds_a_plan_gate_by_the_skill_that_writes_the_draft_not_by_the_node_id():
    nodes = (
        SimpleNamespace(id="weekly", skill="content-plan", gate=True, external_effect=None),
        SimpleNamespace(id="plan", skill="case-study", gate=True, external_effect=None),
    )
    assert runs_fake_script._gate_kinds(nodes) == {"weekly": "plan", "plan": "review"}


# ── unreachable when off ──────────────────────────────────────────────────────


def test_the_stamp_is_a_merge_that_keeps_every_other_payload_key():
    """The stamp MERGES ``fake`` into the job record: mode/pack/inputs are what a reclaim or a
    restart re-reads, so a write that replaced the payload would lose the run's job."""
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    db = RecordingDb(run_id, ws, PACK_PAYLOAD)

    async def _go():
        with lifecycle_harness(db) as hz:
            await runs_queue._stamp_fake(hz.pool, ws, run_id)

    asyncio.run(_go())
    assert db.stamps == [(run_id, ws, runs_fake.STAMP_KEY)]
    assert db.row["payload"] == {**PACK_PAYLOAD, runs_fake.STAMP_KEY: True}


def test_the_stamp_emulator_refuses_a_payload_write_that_is_not_the_merge():
    """Negative control: the emulator above is what makes the merge assertion discriminate."""
    db = RecordingDb(str(uuid.uuid4()), str(uuid.uuid4()), PACK_PAYLOAD)
    replacing = STAMP_MERGE.replace("COALESCE(payload, '{}'::jsonb) || ", "")
    with pytest.raises(AssertionError):
        asyncio.run(db.execute(replacing, db.row["id"], db.workspace_id, runs_fake.STAMP_KEY))
    assert db.row["payload"] == PACK_PAYLOAD


@pytest.mark.parametrize(
    ("status", "ok"),
    [("UPDATE 1", True), ("UPDATE 0", False), ("UPDATE 2", False), (None, False), ("", False)],
)
def test_the_stamp_requires_exactly_one_updated_row(status, ok):
    if ok:
        runs_queue._require_one_row(status, "stamp")
    else:
        with pytest.raises(RuntimeError, match="exactly one row"):
            runs_queue._require_one_row(status, "stamp")


def test_dispatch_aborts_when_the_stamp_updates_no_row(monkeypatch, fake_env):
    """Not best-effort: a stamp that wrote nothing leaves an unstamped fake — exactly the run a
    later claim with the flag off would hand to a real executor — so no task is started."""
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    db = RecordingDb(run_id, ws, PACK_PAYLOAD)
    db.stamp_matches_row = False
    calls: list[str] = []

    async def _go():
        with (
            lifecycle_harness(db) as hz,
            patch.object(runs_pack_executor, "_execute_pack_run", _recorder(calls, "real")),
            patch.object(runs_fake, "_execute_fake_run", _recorder(calls, "fake")),
        ):
            known = set(state._background_tasks)
            with pytest.raises(RuntimeError, match="exactly one row"):
                await runs_queue.dispatch_claimed(
                    hz.pool, REPO, MagicMock(), _claimed(run_id, ws, PACK_PAYLOAD)
                )
            return set(state._background_tasks) - known

    started = asyncio.run(_go())
    assert started == set() and calls == []
    assert len(db.stamps) == 1 and db.row["payload"] == PACK_PAYLOAD
    assert ws not in state._workspace_runs


@pytest.mark.parametrize("payload", [PROMPT_PAYLOAD, PACK_PAYLOAD], ids=["prompt", "pack"])
@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "real"),
        ({"GTM_FAKE_RUNS": "1"}, "real"),  # ENV unset reads as production
        ({"GTM_FAKE_RUNS": "1", "ENV": "staging"}, "real"),
        ({"GTM_FAKE_RUNS": "true", "ENV": "production"}, "real"),
        ({"GTM_FAKE_RUNS": "0", "ENV": "development"}, "real"),
        ({"GTM_FAKE_RUNS": "1", "ENV": "development"}, "fake"),
        ({"GTM_FAKE_RUNS": "TRUE", "ENV": "development"}, "fake"),
    ],
)
def test_dispatch_picks_the_fake_only_with_the_flag_in_development(
    monkeypatch, payload, env, expected
):
    """…and stamps the durable payload exactly when it picks the fake."""
    monkeypatch.delenv("ENV", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    db = RecordingDb(run_id, ws, payload)
    calls: list[str] = []

    async def _go():
        with (
            lifecycle_harness(db) as hz,
            patch.object(runs_executor, "_execute_run", _recorder(calls, "real")),
            patch.object(runs_pack_executor, "_execute_pack_run", _recorder(calls, "real")),
            patch.object(runs_fake, "_execute_fake_run", _recorder(calls, "fake")),
        ):
            task = await _dispatch(hz, _claimed(run_id, ws, payload), MagicMock())
            await _settle(task)

    asyncio.run(_go())
    assert calls == [expected]
    stamped = {**payload, runs_fake.STAMP_KEY: True}
    assert db.row["payload"] == (stamped if expected == "fake" else payload)


@pytest.mark.parametrize("payload", [PROMPT_PAYLOAD, PACK_PAYLOAD], ids=["prompt", "pack"])
@pytest.mark.parametrize("prev_status", ["queued", "running"], ids=["first", "reclaim"])
def test_dispatch_fails_a_stamped_fake_run_once_the_flag_is_off(monkeypatch, payload, prev_status):
    """Turning the flag off must not turn a scripted run into a real one: no executor is
    built, and the run ends failed with a message that says why."""
    monkeypatch.setenv("ENV", "development")  # GTM_FAKE_RUNS unset by the autouse fixture
    run_id, ws = str(uuid.uuid4()), str(uuid.uuid4())
    db = RecordingDb(run_id, ws)
    row = _claimed(run_id, ws, {**payload, runs_fake.STAMP_KEY: True}, prev_status)
    calls: list[str] = []

    async def _go():
        with (
            lifecycle_harness(db) as hz,
            patch.object(runs_executor, "_execute_run", _recorder(calls, "real")),
            patch.object(runs_pack_executor, "_execute_pack_run", _recorder(calls, "real")),
            patch.object(runs_fake, "_execute_fake_run", _recorder(calls, "fake")),
        ):
            q = state._subscribe(run_id)
            dispatched = await runs_queue.dispatch_claimed(hz.pool, REPO, MagicMock(), row)
            state._unsubscribe(run_id, q)
            return dispatched, _drain(q)

    dispatched, frames = asyncio.run(_go())
    assert dispatched is False and calls == []
    assert db.row["status"] == "failed"
    assert db.row["error"] == runs_fake.FAKE_RUN_OFF_ERROR
    assert db.row["error_code"] == "fake_runs_disabled"
    assert _keys(frames) == [("done", "failed", runs_fake.FAKE_RUN_OFF_ERROR)]
    assert ws not in state._workspace_runs


_STAMPED_PACK = {**PACK_PAYLOAD, "fake": True}


@pytest.mark.parametrize(
    ("payload", "flag", "expected", "after"),
    [
        (None, "1", "fake", {"fake": True}),  # pre-V021 row: resolved from the audit line
        (None, None, "real", None),
        ({"fake": True}, None, "refused", {"fake": True}),  # pre-V021 row stamped on resume
        (_STAMPED_PACK, "1", "fake", _STAMPED_PACK),
        (_STAMPED_PACK, None, "refused", _STAMPED_PACK),
        (PACK_PAYLOAD, "1", "fake", _STAMPED_PACK),
        (PACK_PAYLOAD, None, "real", PACK_PAYLOAD),
    ],
    ids=[
        "audit-line-flag-on",
        "audit-line-flag-off",
        "audit-line-stamped-off",
        "stamped-on",
        "stamped-off",
        "unstamped-on",
        "unstamped-off",
    ],
)
def test_reconcile_redispatches_a_gated_run_through_the_same_flag(
    monkeypatch, ws_env, payload, flag, expected, after
):
    """dev runs uvicorn with reload: a restart mid-gate must resume a FAKE run as a fake,
    never hand it to the real pack runner (which would call the SDK) — and a run stamped fake
    that meets the flag off is failed, not resumed on the real runner. A run resumed as a fake
    is stamped first (merged, keeping its job record), so the NEXT restart with the flag off
    refuses it too — including a pre-V021 row, whose job is read from the audit line."""
    monkeypatch.setenv("ENV", "development")
    if flag is not None:
        monkeypatch.setenv("GTM_FAKE_RUNS", flag)
    run_id, ws = str(uuid.uuid4()), ws_env.ws_id
    db = RecordingDb(run_id, ws, payload)
    db.runs.append(
        {
            "id": run_id,
            "workspace_id": ws,
            "profile_name": PROFILE,
            "prompt": PACK_AUDIT_LINE,
            "agent_id": None,
        }
    )
    calls: list[str] = []

    async def _go():
        with (
            lifecycle_harness(db) as hz,
            patch.object(runs_reconcile, "_execute_pack_run", _recorder(calls, "real")),
            patch.object(runs_reconcile, "_execute_fake_run", _recorder(calls, "fake")),
        ):
            known = set(state._background_tasks)
            resumed = await runs_reconcile.reconcile_gates(hz.pool, REPO)
            for task in set(state._background_tasks) - known:
                await _settle(task)
            return resumed

    resumed = asyncio.run(_go())
    if expected == "refused":
        assert resumed == 0 and calls == []
        assert db.row["status"] == "failed"
        assert db.row["error"] == runs_fake.FAKE_RUN_OFF_ERROR
        assert db.row["error_code"] == "fake_runs_disabled"
    else:
        assert resumed == 1 and calls == [expected]
    assert db.row["payload"] == after
    assert len(db.stamps) == int(payload != after)


# ── structure: fake.py's imports ──────────────────────────────────────────────

_FAKE_PACKAGE = "backend.services.runs"
_FORBIDDEN_MODULES = (
    "backend.publish_dispatch",
    "backend.session",
    "agent.publish",
    "agent.publish_dispatch",
    "backend.email_dispatch",
    "agent.email_dispatch",
    "agent.packs",
    "claude_agent_sdk",
    "anthropic",
    "httpx",
    "requests",
    "urllib",
    "aiohttp",
    "importlib",
)
# Names that reach a forbidden path even when re-exported through an allowed module (fake.py
# legitimately imports pack_executor, which itself imports the publish and enrollment dispatch).
_FORBIDDEN_NAMES = frozenset(
    {
        "dispatch_backend_publish",
        "dispatch_backend_email_enroll",
        "dispatch_approved_enrollment",
        "publish_gated_node",
        "_dispatch_gate2",
        "_execute_run",
        "_execute_pack_run",
        "execute_stage",
        "make_executor_from_pack",
        "LinkedInPublisher",
        "BackendSessionStore",
    }
)


def _is_forbidden_module(module: str, modules: tuple[str, ...] = _FORBIDDEN_MODULES) -> bool:
    return any(module == m or module.startswith(m + ".") for m in modules)


def _absolute_module(node: ast.ImportFrom, package: str) -> str:
    base = package.split(".")[: len(package.split(".")) - node.level + 1] if node.level else []
    return ".".join([*base, *([node.module] if node.module else [])])


def _scan_names() -> frozenset[str]:
    return _FORBIDDEN_NAMES | _real_executor_names()


def _is_project_module(module: str) -> bool:
    return any(module == root or module.startswith(root + ".") for root in ("backend", "agent"))


def _forbidden_uses(
    source: str,
    names: frozenset[str],
    package: str = _FAKE_PACKAGE,
    modules: tuple[str, ...] = _FORBIDDEN_MODULES,
) -> set[str]:
    """Every import in ``source`` (at any depth, relative ones resolved against ``package``)
    of a forbidden module or name; a star import from any backend/agent module (it binds a
    forbidden name unseen); any ``__import__``; and every Name, Attribute or string constant
    equal to one of ``names`` — ``from . import pack_executor`` then
    ``pack_executor.dispatch_backend_publish()``, or ``getattr(mod, "_execute_pack_run")``,
    reach the same code as importing the name does."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if _is_forbidden_module(a.name, modules)}
        elif isinstance(node, ast.ImportFrom):
            found |= _forbidden_from(node, names, package, modules)
        elif (label := _forbidden_reference(node, names)) is not None:
            found.add(label)
    return found


def _forbidden_from(
    node: ast.ImportFrom, names: frozenset[str], package: str, modules: tuple[str, ...]
) -> set[str]:
    module = _absolute_module(node, package)
    found: set[str] = set()
    for alias in node.names:
        full = f"{module}.{alias.name}"
        star = alias.name == "*" and _is_project_module(module)
        if star or _is_forbidden_module(full, modules) or alias.name in names:
            found.add(full)
    return found


def _forbidden_reference(node: ast.AST, names: frozenset[str]) -> str | None:
    if isinstance(node, ast.Name) and node.id == "__import__":
        return "__import__"
    kind, ref = _ref_name(node)
    return f"<{kind}>.{ref}" if ref in names else None


def _ref_name(node: ast.AST) -> tuple[str, str | None]:
    """The identifier a node names — as a Name, an Attribute, or a string constant (which
    ``getattr`` turns into a reference just the same) — or None."""
    if isinstance(node, ast.Name):
        return "name", node.id
    if isinstance(node, ast.Attribute):
        return "attribute", node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "string", node.value
    return "", None


def test_the_import_checker_flags_every_shape_it_claims_to():
    """Negative control, on synthetic source (fake.py is never edited to prove this)."""
    synthetic = (
        "from ...publish_dispatch import dispatch_backend_publish\n"
        "from agent.publish import LinkedInPublisher\n"
        "from agent import packs\n"
        "import httpx\n"
        "import urllib.request\n"
        "from .pack_executor import dispatch_backend_publish as relayed\n"
        "mod = __import__('agent.publish')\n"
        "def later():\n"
        "    from ...session import BackendSessionStore\n"
        "from .pack_executor import *\n"
        "from backend import *\n"
        "from agent.pipeline import *\n"
        "from . import pack_executor\n"
        "pack_executor.dispatch_backend_publish()\n"
        "publish_gated_node()\n"
        "runner = getattr(pack_executor, '_execute_pack_run')\n"
    )
    assert _forbidden_uses(synthetic, _scan_names()) == {
        "backend.publish_dispatch.dispatch_backend_publish",
        "agent.publish.LinkedInPublisher",
        "agent.packs",
        "httpx",
        "urllib.request",
        "backend.services.runs.pack_executor.dispatch_backend_publish",
        "__import__",
        "backend.session.BackendSessionStore",
        "backend.services.runs.pack_executor.*",
        "backend.*",
        "agent.pipeline.*",
        "<attribute>.dispatch_backend_publish",
        "<name>.publish_gated_node",
        "<string>._execute_pack_run",
    }
    allowed = (
        "from .pack_executor import _GATE_PLAN_SENTINEL, _node_observer\n"
        "from ...pack_catalog import resolve_variant\n"
        "from gtm_core.paths import *\n"
        "from . import pack_executor\n"
        "observer = pack_executor._node_observer\n"
        "note = 'never calls dispatch_backend_publish'\n"
        "import asyncio\n"
    )
    assert _forbidden_uses(allowed, _scan_names()) == set()


@pytest.mark.parametrize("module", ["fake.py", "fake_script.py"])
def test_fake_module_imports_nothing_that_can_spend_or_publish(module):
    source = (REPO / "backend" / "services" / "runs" / module).read_text(encoding="utf-8")
    assert _forbidden_uses(source, _scan_names()) == set()


#: fake_script.py writes nothing, so it must not even TRANSITIVELY load a dispatch: on top of
#: the fake's scan, it may not import pack_executor, which imports both dispatches.
_FAKE_SCRIPT_FORBIDDEN_MODULES = (*_FORBIDDEN_MODULES, "backend.services.runs.pack_executor")


def test_fake_script_does_not_even_transitively_import_a_dispatch():
    source = (REPO / "backend" / "services" / "runs" / "fake_script.py").read_text("utf-8")
    assert set(_FAKE_SCRIPT_FORBIDDEN_MODULES) >= {
        "backend.services.runs.pack_executor",
        "backend.publish_dispatch",
        "backend.email_dispatch",
        "agent.publish",
        "agent.email_dispatch",
    }
    assert _forbidden_uses(source, _scan_names(), modules=_FAKE_SCRIPT_FORBIDDEN_MODULES) == set()
    # Negative control: the edge this removed is exactly what the stricter scan flags.
    edge = "from .pack_executor import _GATE_PLAN_SENTINEL\nfrom . import pack_executor\n"
    assert _forbidden_uses(edge, _scan_names()) == set()
    assert _forbidden_uses(edge, _scan_names(), modules=_FAKE_SCRIPT_FORBIDDEN_MODULES) == {
        "backend.services.runs.pack_executor._GATE_PLAN_SENTINEL",
        "backend.services.runs.pack_executor",
    }


def test_the_shared_gate_kind_rules_are_a_pure_leaf():
    """gate_kinds.py is called by both real executors and the fake: it may import nothing, so
    the rule cannot grow a side effect or an import cycle."""
    source = (REPO / "backend" / "services" / "runs" / "gate_kinds.py").read_text(encoding="utf-8")
    imports = [
        (getattr(node, "module", None), [alias.name for alias in node.names])
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert imports == [("__future__", ["annotations"])]
    assert _forbidden_uses(source, _scan_names()) == set()


# ── structure: where a real executor may be referenced ────────────────────────

_FLAG = "fake_runs_enabled"
_EXECUTOR_MODULES = ("backend/services/runs/executor.py", "backend/services/runs/pack_executor.py")
_GUARDED_SITES = frozenset(
    {
        ("backend/services/runs/queue.py", "dispatch_claimed"),
        ("backend/services/runs/reconcile.py", "reconcile_gates"),
    }
)


def _is_flag_test(test: ast.expr) -> bool:
    if not isinstance(test, ast.Call):
        return False
    func = test.func
    return (isinstance(func, ast.Name) and func.id == _FLAG) or (
        isinstance(func, ast.Attribute) and func.attr == _FLAG
    )


def _exits(body: list[ast.stmt]) -> bool:
    return bool(body) and isinstance(body[-1], (ast.Return, ast.Raise))


def _executor_refs(source: str, names: frozenset[str]) -> list[tuple[int, str | None, bool]]:
    """``(line, enclosing function, guarded)`` for every Name/Attribute reference — or string
    constant, e.g. ``getattr(mod, "_execute_pack_run")`` — equal to one of ``names``. Guarded
    = lexically in the else branch of ``if fake_runs_enabled():``, or after such an ``if``
    whose body returns. Import statements are not references."""
    refs: list[tuple[int, str | None, bool]] = []

    def visit(node: ast.AST, function: str | None, guarded: bool) -> None:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if _ref_name(node)[1] in names:
            refs.append((node.lineno, function, guarded))
        if isinstance(node, ast.If) and _is_flag_test(node.test):
            block(node.body, function, guarded)
            block(node.orelse, function, True)
            return
        for _field, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            if children and all(isinstance(c, ast.stmt) for c in children):
                block(children, function, guarded)
                continue
            for child in children:
                if isinstance(child, ast.AST):
                    visit(child, function, guarded)

    def block(stmts: list[ast.stmt], function: str | None, guarded: bool) -> None:
        for stmt in stmts:
            visit(stmt, function, guarded)
            if isinstance(stmt, ast.If) and _is_flag_test(stmt.test) and _exits(stmt.body):
                guarded = True

    visit(ast.parse(source), None, False)
    return refs


def _offending(rel: str, refs: list[tuple[int, str | None, bool]]) -> list[tuple]:
    return [
        (rel, line, function)
        for line, function, guarded in refs
        if not guarded or (rel, function) not in _GUARDED_SITES
    ]


def _real_executor_names() -> frozenset[str]:
    """Read from the defining modules, so a rename cannot silently empty the scan."""
    names: set[str] = set()
    for rel in _EXECUTOR_MODULES:
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        names |= {
            n.name
            for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("_execute")
        }
    return frozenset(names)


def test_the_executor_reference_scan_discriminates():
    """Negative controls on synthetic source: the scan accepts exactly the two guard shapes
    and flags everything else — the body branch, a different test, a guard that does not
    return, the right guard in the wrong function, and a bare (uncalled) reference."""
    names = frozenset({"_execute_run"})
    site = "backend/services/runs/queue.py"
    head = "async def dispatch_claimed():\n"
    accepted = {
        "else-branch": head + "    if fake_runs_enabled():\n        c = 1\n"
        "    else:\n        c = _execute_run()\n",
        "early-return": head + "    if fake.fake_runs_enabled():\n        return 1\n"
        "    return mod._execute_run()\n",
        "import-only": "from .executor import _execute_run\n",
        "getattr-else-branch": head + "    if fake_runs_enabled():\n        c = 1\n"
        "    else:\n        c = getattr(mod, '_execute_run')()\n",
        "string-mentioning-it": "note = 'see _execute_run'\n",
    }
    flagged = {
        "body-branch": head + "    if fake_runs_enabled():\n        return _execute_run()\n",
        "other-test": head + "    if other():\n        pass\n    else:\n        _execute_run()\n",
        "no-return": head + "    if fake_runs_enabled():\n        c = 1\n    _execute_run()\n",
        "wrong-function": "async def elsewhere():\n    if fake_runs_enabled():\n"
        "        return\n    _execute_run()\n",
        "bare-reference": "task = functools.partial(_execute_run)\n",
        "getattr-string": "task = getattr(mod, '_execute_run')\n",
        "string-in-body-branch": head + "    if fake_runs_enabled():\n"
        "        return getattr(mod, '_execute_run')()\n",
    }
    for label, source in accepted.items():
        assert _offending(site, _executor_refs(source, names)) == [], label
    for label, source in flagged.items():
        assert len(_offending(site, _executor_refs(source, names))) == 1, label


def test_real_executors_are_referenced_only_behind_the_flag_at_the_two_seams():
    """A new dispatch site that forgot the flag would make the fake reachable-but-bypassed: a
    scripted queue with a real SDK run slipping out of it. Every .py under backend/ is
    scanned; both seams must still exist AND still be guarded."""
    names = _real_executor_names()
    assert names == {"_execute_run", "_execute_pack_run"}
    offenders: list[tuple] = []
    sites: set[tuple[str, str | None]] = set()
    for path in sorted((REPO / "backend").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel in _EXECUTOR_MODULES:
            continue
        refs = _executor_refs(path.read_text(encoding="utf-8"), names)
        offenders += _offending(rel, refs)
        sites |= {(rel, function) for _, function, guarded in refs if guarded}
    assert offenders == []
    assert sites == _GUARDED_SITES
