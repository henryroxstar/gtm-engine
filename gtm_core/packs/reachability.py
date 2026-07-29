"""Which skills are reachable given a tenant's active pack set (E-3, PRD §7/§12.4).

Pure computation over ``packs.toml`` + the pack graphs it activates — proves "a disabled
pack's skills are unreachable" as a testable property. **Not yet wired** into the live
permission callback (``agent/permissions.py``): that integration touches the hot path of
every skill invocation across the whole product and is deliberately deferred to its own
change (see ``docs/SECURITY-SELF-ASSESSMENT.md`` residual #11/#12) rather than landed as
a side effect of this refactor.
"""

from __future__ import annotations

from pathlib import Path

from .loader import load_pack_graph
from .tenant import load_pack_activation


def active_skills_for_profile(
    profiles_root: Path, profile: str, packs_root: Path
) -> frozenset[str]:
    """Union of skill names referenced by every pack ``profile`` has activated.

    ``packs_root`` is the repo's ``packs/`` directory (each pack's graphs live under
    ``packs_root/<pack>/graphs/*.toml``). Fail-closed default: a profile with no
    ``packs.toml`` activates nothing, so its pack-scoped skills are all unreachable —
    matching "a disabled pack's skills are unreachable" without special-casing.
    """
    activation_path = profiles_root / profile / "packs.toml"
    if not activation_path.is_file():
        return frozenset()

    activation = load_pack_activation(activation_path)
    skills: set[str] = set()
    for pack_name in activation.active:
        graphs_dir = packs_root / pack_name / "graphs"
        if not graphs_dir.is_dir():
            continue
        for graph_path in sorted(graphs_dir.glob("*.toml")):
            graph = load_pack_graph(graph_path)
            skills.update(n.skill for n in graph.nodes if n.skill is not None)
    return frozenset(skills)
