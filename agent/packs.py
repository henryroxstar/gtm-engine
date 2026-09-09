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

    Carries ``id``/``depends_on`` plus the A10 gate metadata (``gate``,
    ``external_effect``) — previously dropped here, which forced the runtime to
    recognise the publish gate by the node's NAME. ``revisable_from``/``max_visits``
    stay pack-side until the engine grows revision-loop semantics.
    """
    return Graph(
        nodes=tuple(
            Node(
                id=n.id,
                depends_on=n.depends_on,
                gate=n.gate,
                external_effect=n.external_effect,
            )
            for n in pack.nodes
        )
    )


def load_engine_graph(path: Path) -> tuple[PackGraph, Graph]:
    """Load a pack graph TOML file and return both the pack data and its engine graph."""
    pack = load_pack_graph(path)
    return pack, pack_graph_to_engine_graph(pack)


def make_executor_from_pack(
    cfg: Config,
    profile: str,
    pack: PackGraph,
    *,
    usage_sink=None,
    allowed_skills: frozenset[str] | None = None,
    language: str | None = None,
) -> StageExecutor:
    """Build a :class:`~agent.pipeline.StageExecutor` driven by a pack's own node metadata.

    Generalizes ``pipeline_executor.STAGE_PROMPTS``/``_STAGE_ROLES`` (two hardcoded dicts) into
    per-pack data — the execution mechanics (``execute_stage``) are unchanged and shared with the
    news/journey cron path. ``usage_sink``/``allowed_skills``/``language`` are the backend's
    cost-metering, pack-reachability (A1) and per-request-language (A7) hooks; all default off,
    leaving the VPS path byte-identical.
    """
    prompts = {n.id: n.prompt for n in pack.nodes}
    stage_roles = {n.id: n.model_role for n in pack.nodes}
    # A10: which nodes declare an irreversible external effect. execute_stage
    # short-circuits those by DECLARATION rather than by the node's name.
    external_effects = {n.id: n.external_effect for n in pack.nodes if n.external_effect}

    async def _executor(stage_name: str, manifest: dict) -> StageOutcome:
        return await execute_stage(
            cfg,
            profile,
            stage_name,
            manifest,
            prompts=prompts,
            stage_roles=stage_roles,
            usage_sink=usage_sink,
            allowed_skills=allowed_skills,
            external_effects=external_effects,
            language=language,
        )

    return _executor
