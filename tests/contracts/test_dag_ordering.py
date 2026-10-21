"""Contract test for pack DAG dependency ordering and execution guarantees (PRD §5.3).

Verifies:
1. Strict dependency ordering: node C cannot run before all its upstream dependencies complete.
2. Failure containment: if an upstream dependency (step B) fails, downstream steps (step C)
   are never dispatched and remain pending.
3. Multi-dependency joins: in a fan-out/join graph (A -> [B1, B2] -> C), C strictly refuses
   to run if either branch fails or remains unfulfilled.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent.graph import Graph, Node
from agent.pipeline import (
    FAILED,
    OK,
    PENDING,
    PipelineRunner,
    StageOutcome,
    new_manifest,
    runnable_frontier,
)
from gtm_core.paths import PathConfig


def test_frontier_strictly_enforces_upstream_completion():
    """Verify runnable_frontier only returns nodes whose dependencies are all complete."""
    # Graph: A -> B -> C
    graph = Graph(
        nodes=(
            Node(id="A"),
            Node(id="B", depends_on=("A",)),
            Node(id="C", depends_on=("B",)),
        )
    )

    manifest = new_manifest("test-run", "test", "default", graph=graph)

    # 1. Initial state: only A is runnable
    assert runnable_frontier(graph, manifest) == ["A"]

    # 2. A complete: B is runnable, C is NOT runnable
    manifest["stages"][0]["status"] = OK
    frontier = runnable_frontier(graph, manifest)
    assert frontier == ["B"]
    assert "C" not in frontier

    # 3. B fails: C strictly refuses to run (only failed node B is retry-eligible)
    manifest["stages"][1]["status"] = FAILED
    frontier_after_b_failure = runnable_frontier(graph, manifest)
    assert "C" not in frontier_after_b_failure
    assert frontier_after_b_failure == ["B"]


def test_frontier_multi_branch_join_requires_all_branches():
    """Graph: A -> (B1, B2) -> C. C only runs when BOTH B1 and B2 complete."""
    graph = Graph(
        nodes=(
            Node(id="A"),
            Node(id="B1", depends_on=("A",)),
            Node(id="B2", depends_on=("A",)),
            Node(id="C", depends_on=("B1", "B2")),
        )
    )

    manifest = new_manifest("test-run-diamond", "test", "default", graph=graph)
    manifest["stages"][0]["status"] = OK

    # Both B1 and B2 are runnable
    assert sorted(runnable_frontier(graph, manifest)) == ["B1", "B2"]

    # Only B1 completes: C must NOT run
    manifest["stages"][1]["status"] = OK
    frontier = runnable_frontier(graph, manifest)
    assert "C" not in frontier
    assert frontier == ["B2"]

    # B2 fails: C strictly refuses to run
    manifest["stages"][2]["status"] = FAILED
    frontier_after_fail = runnable_frontier(graph, manifest)
    assert "C" not in frontier_after_fail
    assert frontier_after_fail == ["B2"]


def test_pipeline_runner_halts_on_failure_preventing_downstream(tmp_path: Path):
    """End-to-end runner test: step C executor must never be called when step B fails."""
    graph = Graph(
        nodes=(
            Node(id="A"),
            Node(id="B", depends_on=("A",)),
            Node(id="C", depends_on=("B",)),
        )
    )

    executed_stages: list[str] = []

    async def _mock_executor(stage_name: str, manifest: dict[str, Any]) -> StageOutcome:
        executed_stages.append(stage_name)
        if stage_name == "A":
            return StageOutcome(status=OK)
        if stage_name == "B":
            return StageOutcome(status=FAILED, error="Simulated LLM rate limit or failure")
        if stage_name == "C":
            return StageOutcome(status=OK)
        return StageOutcome(status=FAILED, error=f"Unknown stage {stage_name}")

    content_dir = tmp_path / "content"
    profiles_dir = tmp_path / "profiles"
    content_dir.mkdir(parents=True, exist_ok=True)
    profiles_dir.mkdir(parents=True, exist_ok=True)

    cfg = PathConfig(
        content_root=content_dir,
        profiles_root=profiles_dir,
        default_profile="test-profile",
    )

    runner = PipelineRunner(
        cfg=cfg,
        profile="test-profile",
        graph=graph,
    )

    async def _test():
        manifest = await runner.run(
            run_id="run-failure-containment",
            trigger="test",
            executor=_mock_executor,
        )
        # Assert A and B were called, but C was NEVER executed
        assert executed_stages == ["A", "B"]
        assert "C" not in executed_stages

        # Verify manifest state
        stages_by_name = {s["name"]: s["status"] for s in manifest["stages"]}
        assert stages_by_name["A"] == OK
        assert stages_by_name["B"] == FAILED
        assert stages_by_name["C"] == PENDING

    asyncio.run(_test())
