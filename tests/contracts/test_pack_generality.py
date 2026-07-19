"""E-4 verification: the generality proof.

- Two more real pack graphs (prospecting, solution-architecture) load on the unmodified
  engine, alongside a marketing fan-out variant (long-form-blog).
- A fan-out batch is genuinely dispatched concurrently: one manifest persist shows BOTH
  branch nodes ``running`` at once, not one-then-the-other.
- Parallel execution still honors exactly one ``profile_lock`` acquisition regardless of
  fan-out width, and the §R2 budget guard now fires per dispatch batch, not just once at
  run start.
- No engine module special-cases a pack/variant name in its control flow (AST-based, so
  a docstring/comment mentioning a pack by way of example — of which there are several —
  doesn't false-positive).
"""

from __future__ import annotations

import ast
import asyncio
import copy
import dataclasses
from pathlib import Path

import pytest

from agent.graph import Graph, Node
from agent.packs import load_engine_graph
from agent.pipeline import FAILED, OK, PipelineRunner, StageOutcome
from gtm_core.packs.loader import load_pack_graph

REPO = Path(__file__).resolve().parents[2]
PROFILE = "example"


@pytest.fixture()
def cfg(tmp_path):
    from agent.config import Config

    base = Config.from_env(repo_root=REPO)
    return dataclasses.replace(base, content_root=tmp_path / "content")


# ── two more real pack graphs load on the unmodified engine ──────────────────


def test_prospecting_pack_loads_and_shapes_correctly():
    pack = load_pack_graph(REPO / "packs" / "prospecting" / "graphs" / "prospect-outreach.toml")
    assert pack.ids == ("prospect", "dossier", "outreach")
    assert pack.node("outreach").gate is True
    assert pack.node("prospect").gate is False


def test_solution_architecture_pack_loads_and_shapes_correctly():
    pack = load_pack_graph(
        REPO / "packs" / "solution-architecture" / "graphs" / "solution-architecture.toml"
    )
    assert pack.ids == ("discovery", "design", "runbook", "deck")
    assert all(not n.gate for n in pack.nodes)  # produces docs, no external gate


def test_planning_pack_is_three_independent_roots_no_gate():
    """The planning pack is a BATCH, not a chain: three standalone deliverables with no
    depends_on between them and no external gate. This is the multi-root fan-out shape —
    all three nodes are in the initial frontier at once."""
    pack = load_pack_graph(REPO / "packs" / "planning" / "graphs" / "planning.toml")
    assert pack.ids == ("gtm-plan", "account-plan", "event-plan")
    assert all(n.depends_on == () for n in pack.nodes)  # independent — no false chain
    assert all(not n.gate for n in pack.nodes)  # produces docs/spreadsheets, nothing sent


def test_planning_pack_converts_to_a_three_root_engine_graph(cfg):
    """Engine-side: with no edges, every node is immediately runnable — the runner's very
    first frontier is all three, dispatched concurrently under one lock (E-4 fan-out)."""
    from agent.pipeline import DEFAULT_GRAPH, new_manifest, runnable_frontier

    _, engine_graph = load_engine_graph(REPO / "packs" / "planning" / "graphs" / "planning.toml")
    manifest = new_manifest("r-plan", "cron", PROFILE, graph=engine_graph)
    assert set(runnable_frontier(engine_graph, manifest)) == {
        "gtm-plan",
        "account-plan",
        "event-plan",
    }
    assert engine_graph is not DEFAULT_GRAPH  # sanity: it's the pack's own graph


def test_long_form_blog_pack_is_a_real_fan_out():
    _, engine_graph = load_engine_graph(
        REPO / "packs" / "marketing" / "graphs" / "long-form-blog.toml"
    )
    studio = engine_graph.node("studio")
    assert set(studio.depends_on) == {"research-evidence", "research-competitive"}


# ── fan-out: a real batch shows BOTH nodes RUNNING at once ────────────────────


def test_fan_out_batch_shows_concurrent_running_nodes_in_one_persist(cfg):
    """Prove the batch is dispatched together, not one-after-another: capture every
    persisted manifest snapshot and find one where BOTH branch nodes are 'running'."""
    graph = Graph(
        nodes=(
            Node("a"),
            Node("b", depends_on=("a",)),
            Node("c", depends_on=("a",)),
            Node("d", depends_on=("b", "c")),
        )
    )
    runner = PipelineRunner(cfg, PROFILE, graph=graph)
    snapshots: list[dict] = []
    real_write = runner.ledgers.write_run_manifest

    def _capturing_write(manifest):
        snapshots.append(copy.deepcopy(manifest))
        return real_write(manifest)

    runner.ledgers.write_run_manifest = _capturing_write

    async def _executor(stage: str, manifest: dict) -> StageOutcome:
        return StageOutcome(status=OK)

    asyncio.run(runner.run("r-concurrent", "cron", _executor))

    both_running = [
        snap
        for snap in snapshots
        if {s["name"]: s["status"] for s in snap["stages"]}.get("b") == "running"
        and {s["name"]: s["status"] for s in snap["stages"]}.get("c") == "running"
    ]
    assert both_running, "no persisted snapshot ever showed b AND c running at the same time"


def test_fan_out_still_holds_exactly_one_profile_lock(cfg, monkeypatch):
    import contextlib

    lock_calls = {"n": 0}

    @contextlib.contextmanager
    def _counting_lock(content_root, profile, *, blocking=True):
        lock_calls["n"] += 1
        yield content_root

    monkeypatch.setattr("agent.pipeline.profile_lock", _counting_lock)

    graph = Graph(nodes=(Node("a"), Node("b", depends_on=("a",)), Node("c", depends_on=("a",))))
    runner = PipelineRunner(cfg, PROFILE, graph=graph)

    async def _executor(stage: str, manifest: dict) -> StageOutcome:
        return StageOutcome(status=OK)

    asyncio.run(runner.run("r-lock-count", "cron", _executor))
    assert lock_calls["n"] == 1  # one acquisition for the whole run, fan-out included


def test_budget_guard_fires_per_batch_not_just_once(cfg, monkeypatch):
    """§R2: a profile that goes over budget mid-run (after the first batch) must still
    abort before the SECOND batch — not just get checked once at the very start."""
    calls = {"n": 0}

    def _budget_ok(cfg_arg, profile):
        calls["n"] += 1
        return calls["n"] == 1  # ok for the first batch, over-cap from the second on

    monkeypatch.setattr("agent.budget.vps_budget_ok", _budget_ok)

    graph = Graph(nodes=(Node("a"), Node("b", depends_on=("a",))))
    runner = PipelineRunner(cfg, PROFILE, graph=graph)
    ran: list[str] = []

    async def _executor(stage: str, manifest: dict) -> StageOutcome:
        ran.append(stage)
        return StageOutcome(status=OK)

    manifest = asyncio.run(runner.run("r-budget", "cron", _executor))
    by_name = {s["name"]: s["status"] for s in manifest["stages"]}
    errors_by_name = {s["name"]: s.get("error", "") for s in manifest["stages"]}

    assert ran == ["a"]  # stopped before b's batch ever dispatched
    assert by_name["a"] == OK
    assert by_name["b"] == FAILED
    assert "cost cap" in errors_by_name["b"]


# ── engine purity: no engine module special-cases a pack name ────────────────

ENGINE_MODULES = (
    "agent/pipeline.py",
    "agent/graph.py",
    "agent/pipeline_executor.py",
    "agent/packs.py",
    "agent/readiness.py",
    "gtm_core/packs/loader.py",
    "gtm_core/packs/tenant.py",
    "gtm_core/packs/reachability.py",
    "gtm_core/knowledge_index.py",
)

PACK_AND_VARIANT_NAMES = frozenset(
    {
        "marketing",
        "prospecting",
        "solution-architecture",
        "planning",
        "linkedin-reply",
        "event-planning",
        "linkedin-post",
        "prospect-outreach",
        "long-form-blog",
    }
)


def _string_constants_in_conditionals(tree: ast.AST) -> list[str]:
    """String literals appearing in a Compare (==, in, etc.) anywhere in the module —
    AST-based so a docstring/comment mentioning a pack by way of example never
    false-positives (only executable comparison code is inspected)."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for operand in operands:
                if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                    found.append(operand.value)
    return found


def _module_special_cases_a_pack(path: Path, pack_names) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [s for s in _string_constants_in_conditionals(tree) if s in pack_names]


def test_no_engine_module_special_cases_a_pack_name():
    violations = {}
    for rel in ENGINE_MODULES:
        hits = _module_special_cases_a_pack(REPO / rel, PACK_AND_VARIANT_NAMES)
        if hits:
            violations[rel] = hits
    assert violations == {}, violations


def test_engine_purity_checker_self_test_detects_a_synthetic_violation(tmp_path):
    """Prove the checker actually fires — mirrors the layering-test self-test pattern."""
    bad_module = tmp_path / "bad_engine_module.py"
    bad_module.write_text(
        'def dispatch(pack_name):\n    if pack_name == "marketing":\n        pass\n'
    )
    hits = _module_special_cases_a_pack(bad_module, PACK_AND_VARIANT_NAMES)
    assert hits == ["marketing"]
