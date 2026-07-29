"""agent.packs — the engine-side adapter from the pack layer to the runnable graph.

``gtm_core.packs.loader`` returns pure data (:class:`~gtm_core.packs.loader.PackGraph`);
this module converts that into a runnable :class:`agent.graph.Graph` and builds a
:class:`~agent.pipeline.StageExecutor` driven by the pack's own per-node prompt and
``model_role``. It lives in ``agent/`` (not ``gtm_core/``) because it needs
``agent.graph.Graph`` and ``agent.pipeline_executor.execute_stage`` — ``gtm_core`` must
never import ``agent`` (tests/contracts/test_layering.py enforces this).
"""

from __future__ import annotations

from pathlib import Path

from gtm_core.packs.loader import PackGraph, load_pack_graph

from .config import Config
from .graph import Graph, Node
from .pipeline import StageExecutor, StageOutcome
from .pipeline_executor import execute_stage


def pack_graph_to_engine_graph(pack: PackGraph) -> Graph:
    """Convert a loaded, validated :class:`PackGraph` into a runnable engine :class:`Graph`.

    Carries only ``id``/``depends_on`` — E-1's engine graph is deliberately minimal and
    has no concept of ``revisable_from``/``max_visits``/``gate`` yet; those stay pack-side
    metadata until the engine grows revision-loop and gate-introspection semantics.
    """
    return Graph(nodes=tuple(Node(id=n.id, depends_on=n.depends_on) for n in pack.nodes))


def load_engine_graph(path: Path) -> tuple[PackGraph, Graph]:
    """Load a pack graph TOML file and return both the pack data and its engine graph."""
    pack = load_pack_graph(path)
    return pack, pack_graph_to_engine_graph(pack)


def make_executor_from_pack(cfg: Config, profile: str, pack: PackGraph) -> StageExecutor:
    """Build a :class:`~agent.pipeline.StageExecutor` driven by a pack's own node metadata.

    Generalizes ``pipeline_executor.STAGE_PROMPTS``/``_STAGE_ROLES`` (two hardcoded dicts)
    into per-pack data, per docs/prds/2026-07-06-engine-pack-tenant-three-layer.md §6 — the
    execution mechanics (``execute_stage``) are unchanged and shared with the news/journey
    cron path.
    """
    prompts = {n.id: n.prompt for n in pack.nodes}
    stage_roles = {n.id: n.model_role for n in pack.nodes}

    async def _executor(stage_name: str, manifest: dict) -> StageOutcome:
        return await execute_stage(
            cfg, profile, stage_name, manifest, prompts=prompts, stage_roles=stage_roles
        )

    return _executor
