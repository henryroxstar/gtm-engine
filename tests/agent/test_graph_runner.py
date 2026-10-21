"""Verification for the E-1 graph-shaped runner (agent.graph + agent.pipeline).

Three layers of proof, per the validated migration plan:

1. A **differential property test**: ``resume_index`` (unmodified, kept as the oracle) vs.
   ``runnable_frontier`` over a linear graph, across ~50 seeded-random stage/status
   combinations — proving equivalence rather than asserting it by inspection. Uses stdlib
   ``random`` with a fixed seed; no ``hypothesis`` dependency added.
2. **Graph-native tests**: behavior only a real graph exposes (diamond fan-out, cycle
   rejection, dangling-dependency rejection, deterministic ordering) that a linear-only
   model could never exercise.
3. An **end-to-end diamond run** through the real ``PipelineRunner``, proving the runner
   loop (lock/persist/stop-early semantics unchanged) actually drives a branching graph,
   not just that the pure frontier function computes one correctly. Since E-4, a multi-node
   frontier is dispatched concurrently (asyncio.gather) — these tests assert the batch
   semantics (both branches run, join waits, a gate on one branch still stops the join)
   rather than a specific execution order between sibling branches.
"""

from __future__ import annotations

import asyncio
import random

import pytest

pytest.importorskip("agent.pipeline", reason="agent.pipeline not built yet")

from agent.graph import Graph, Node, linear_graph  # noqa: E402
from agent.pipeline import (  # noqa: E402
    AWAITING_APPROVAL,
    FAILED,
    OK,
    PENDING,
    STAGES,
    PipelineRunner,
    StageOutcome,
    new_manifest,
    resume_index,
    runnable_frontier,
)

PROFILE = "example"

_COMPLETE_STATUSES = [OK, "skipped"]
_CUTOFF_STATUSES = ["pending", "running", FAILED, AWAITING_APPROVAL, None]  # None = missing entry


def _random_manifest(rng: random.Random, stage_names: list[str]) -> dict:
    """A manifest reachable by real sequential execution: a random-length complete PREFIX
    (each stage independently ``ok``/``skipped``), then one stage at a random non-complete
    status (or missing), then the rest untouched/missing.

    Only prefix-complete ("monotone") manifests are in scope for this equivalence: for a
    linear chain, ``runnable_frontier`` checks a node's *immediate* predecessor, while
    ``resume_index`` requires the *entire* prefix complete. Those agree exactly when the
    manifest has no "gaps" — which is the only shape the runner itself ever produces, since
    it always executes the frontier in order and never advances past a non-complete stage.
    An adversarial manifest with a gap (e.g. a later stage ``ok`` while an earlier one is
    still ``pending`` — never producible by ``PipelineRunner.run``) is deliberately out of
    scope; see ``test_frontier_diverges_from_oracle_on_an_unreachable_gap_manifest`` below,
    which documents that divergence explicitly rather than hiding it.
    """
    cutoff = rng.randint(0, len(stage_names))  # index of the first non-complete stage
    stages = []
    for i, name in enumerate(stage_names):
        if i < cutoff:
            stages.append({"name": name, "status": rng.choice(_COMPLETE_STATUSES)})
        elif i == cutoff:
            status = rng.choice(_CUTOFF_STATUSES)
            if status is not None:
                stages.append({"name": name, "status": status})
        # i > cutoff: leave untouched (missing entry) — matches a fresh manifest's tail
    return {"run_id": "r", "trigger": "cron", "profile": PROFILE, "stages": stages}


# ── 1. Differential property test: runnable_frontier vs. resume_index oracle ──────────


def test_frontier_matches_resume_index_oracle_across_seeded_random_manifests():
    rng = random.Random(20260706)  # fixed seed — deterministic, reproducible failures
    stage_names = list(STAGES)
    graph = linear_graph(stage_names)

    for _ in range(50):
        manifest = _random_manifest(rng, stage_names)

        oracle_index = resume_index(manifest, stage_names)
        expected = [] if oracle_index == len(stage_names) else [stage_names[oracle_index]]

        actual = runnable_frontier(graph, manifest)
        assert actual == expected, (manifest, oracle_index, actual)


def test_frontier_diverges_from_oracle_on_an_unreachable_gap_manifest():
    """Documented, deliberate divergence: a manifest with a "gap" (a later stage complete
    while an earlier one is not) can never be produced by ``PipelineRunner.run`` — the
    runner only ever executes stages in frontier order. ``runnable_frontier`` treats each
    node's *immediate* dependency as sufficient (standard DAG frontier semantics); the older
    ``resume_index`` requires the *entire* prefix complete. On such an unreachable manifest
    they legitimately disagree — this test pins that fact so it's a documented design
    decision, not a silent gap in the property test above.
    """
    stage_names = list(STAGES)  # radar, plan, research, studio, publish
    graph = linear_graph(stage_names)
    gap_manifest = {
        "run_id": "r",
        "trigger": "cron",
        "profile": PROFILE,
        "stages": [
            {"name": "radar", "status": "pending"},  # NOT complete
            {"name": "research", "status": OK},  # complete, despite radar/plan not being
        ],
    }
    assert resume_index(gap_manifest, stage_names) == 0  # oracle: resume at radar
    assert runnable_frontier(graph, gap_manifest) == ["radar", "studio"]  # both unblocked


def test_frontier_matches_oracle_on_fresh_and_complete_manifests():
    """Boundary cases the random sweep might not hit reliably: fully fresh, fully complete."""
    graph = linear_graph(STAGES)

    fresh = new_manifest("r", "cron", PROFILE, graph)
    assert resume_index(fresh, STAGES) == 0
    assert runnable_frontier(graph, fresh) == [STAGES[0]]

    complete = new_manifest("r", "cron", PROFILE, graph)
    for s in complete["stages"]:
        s["status"] = OK
    assert runnable_frontier(graph, complete) == []
    assert resume_index(complete, STAGES) == len(STAGES)


# ── 2. Graph-native tests ──────────────────────────────────────────────────────────────


def test_diamond_frontier_exposes_both_branches_at_once():
    # a -> {b, c} -> d
    graph = Graph(
        nodes=(
            Node("a"),
            Node("b", depends_on=("a",)),
            Node("c", depends_on=("a",)),
            Node("d", depends_on=("b", "c")),
        )
    )
    manifest = {
        "run_id": "r",
        "trigger": "cron",
        "profile": PROFILE,
        "stages": [{"name": "a", "status": OK}],
    }
    assert runnable_frontier(graph, manifest) == ["b", "c"]  # both unblocked, declaration order

    manifest["stages"] += [{"name": "b", "status": OK}]  # c still pending
    assert runnable_frontier(graph, manifest) == ["c"]  # d still blocked on c

    manifest["stages"] += [{"name": "c", "status": OK}]
    assert runnable_frontier(graph, manifest) == ["d"]  # both branches done, join runnable


def test_graph_rejects_cycle():
    with pytest.raises(ValueError, match="cycle"):
        Graph(nodes=(Node("a", depends_on=("b",)), Node("b", depends_on=("a",))))


def test_graph_rejects_dangling_dependency():
    with pytest.raises(ValueError, match="unknown"):
        Graph(nodes=(Node("a", depends_on=("ghost",)),))


def test_graph_rejects_duplicate_node_ids():
    with pytest.raises(ValueError, match="duplicate"):
        Graph(nodes=(Node("a"), Node("a")))


def test_frontier_ordering_is_deterministic_across_repeated_calls():
    graph = Graph(nodes=(Node("a"), Node("b", depends_on=("a",)), Node("c", depends_on=("a",))))
    manifest = {
        "run_id": "r",
        "trigger": "cron",
        "profile": PROFILE,
        "stages": [{"name": "a", "status": OK}],
    }
    first = runnable_frontier(graph, manifest)
    for _ in range(10):
        assert runnable_frontier(graph, manifest) == first == ["b", "c"]


# ── 3. End-to-end diamond run through the real PipelineRunner ─────────────────────────


def test_runner_drives_a_diamond_graph_concurrently(cfg):
    """Not just the pure frontier fn — the real runner loop must execute a branching graph.
    Since E-4, a multi-node frontier (both branches unblocked at once) is dispatched
    concurrently via asyncio.gather, so the order between b/c is unconstrained — only that
    both ran before the join."""
    graph = Graph(
        nodes=(
            Node("a"),
            Node("b", depends_on=("a",)),
            Node("c", depends_on=("a",)),
            Node("d", depends_on=("b", "c")),
        )
    )
    runner = PipelineRunner(cfg, PROFILE, graph=graph)
    ran: list[str] = []

    async def _executor(stage: str, manifest: dict) -> StageOutcome:
        ran.append(stage)
        return StageOutcome(status=OK)

    manifest = asyncio.run(runner.run("r-diamond", "cron", _executor))

    assert ran[0] == "a"
    assert set(ran[1:3]) == {"b", "c"}  # both branches ran (order between them unconstrained)
    assert ran[3] == "d"  # join only after both branches
    assert all(s["status"] == OK for s in manifest["stages"])


def test_runner_diamond_stops_at_gate_on_one_branch(cfg):
    """A gate on one branch must stop the whole run — the join must never see a partial state.

    Since E-4, ``b`` and ``c`` are in the SAME frontier batch (both depend only on ``a``) and
    are dispatched concurrently via asyncio.gather — the batch always runs to completion (an
    in-flight sibling is never cancelled), so ``c`` DOES run here too. What must never happen
    is the join (``d``) running before both branches — including the gated one — resolve.
    """
    graph = Graph(
        nodes=(
            Node("a"),
            Node("b", depends_on=("a",)),
            Node("c", depends_on=("a",)),
            Node("d", depends_on=("b", "c")),
        )
    )
    runner = PipelineRunner(cfg, PROFILE, graph=graph)
    ran: list[str] = []

    async def _executor(stage: str, manifest: dict) -> StageOutcome:
        ran.append(stage)
        if stage == "b":
            return StageOutcome(status=AWAITING_APPROVAL)
        return StageOutcome(status=OK)

    manifest = asyncio.run(runner.run("r-diamond-gate", "cron", _executor))
    by_name = {s["name"]: s["status"] for s in manifest["stages"]}

    assert "d" not in ran  # join never ran
    assert set(ran) == {"a", "b", "c"}
    assert by_name["b"] == AWAITING_APPROVAL
    assert by_name["c"] == OK  # c's own branch completed — it just wasn't the blocker
    assert by_name["d"] == PENDING
