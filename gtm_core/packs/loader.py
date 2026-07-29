"""Pack graph + inputs loader — TOML via stdlib ``tomllib`` (consistent with
``gtm_core/models.toml``; no new dependency; PRD's YAML examples were illustrative).

Fail-closed at load, mirroring ``capability_registry``'s reject-unknown-slug posture
(docs/prds/2026-07-06-engine-pack-tenant-three-layer.md §6): an invalid pack graph never
reaches the runner — it raises :class:`PackValidationError` with a rule name, at load
time, not at first use.

Deliberately returns plain data (:class:`PackNode`/:class:`PackGraph`), never an
``agent.graph.Graph`` — this package must never import ``agent/`` (engine sits above the
pack layer; see the layering rule enforced by tests/contracts/test_layering.py). The
engine-side converter lives in ``agent/packs.py``.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from gtm_core.models import resolve_model
from gtm_core.skills.registry import all_skills

_VALID_SETTING_SOURCES = frozenset({"ask", "derive"})
_VALID_FRESHNESS = frozenset({"evergreen", "90d"})


class PackValidationError(ValueError):
    """Raised when a pack graph or inputs file fails a named validation rule."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        super().__init__(f"[{rule}] {message}")


@dataclass(frozen=True)
class PackNode:
    """One node in a pack graph — data, mirrors ``agent.graph.Node`` plus pack metadata."""

    id: str
    depends_on: tuple[str, ...] = ()
    model_role: str = "brain_plan"
    skill: str | None = None
    prompt: str = ""
    gate: bool = False
    # Declared external side effect, if any. Only "publish" may coexist with gate=True —
    # any other value marks a gate on a node that does something irreversible besides
    # pausing for review, which is not what a gate is for (§6 rule 5).
    external_effect: str | None = None
    revisable_from: tuple[str, ...] = ()
    max_visits: int | None = None


@dataclass(frozen=True)
class PackGraph:
    """A validated pack graph — pure data, loaded from ``packs/<pack>/graphs/<variant>.toml``."""

    pack: str
    variant: str
    nodes: tuple[PackNode, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(n.id for n in self.nodes)

    def node(self, node_id: str) -> PackNode:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)


def _assert_acyclic(nodes: tuple[PackNode, ...]) -> None:
    by_id = {n.id: n for n in nodes}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(by_id, WHITE)

    def visit(node_id: str, path: list[str]) -> None:
        color[node_id] = GRAY
        for dep in by_id[node_id].depends_on:
            if color[dep] == GRAY:
                cycle = [*path, node_id, dep]
                raise PackValidationError("cycle", f"depends_on cycle: {' -> '.join(cycle)}")
            if color[dep] == WHITE:
                visit(dep, [*path, node_id])
        color[node_id] = BLACK

    for node_id in by_id:
        if color[node_id] == WHITE:
            visit(node_id, [])


def _build_node(raw: dict) -> PackNode:
    node_id = raw.get("id")
    if not node_id or not isinstance(node_id, str):
        raise PackValidationError("missing_id", f"node missing a string 'id': {raw!r}")
    revisable_from = tuple(raw.get("revisable_from", ()))
    max_visits = raw.get("max_visits")
    if revisable_from and max_visits is None:
        raise PackValidationError(
            "unbounded_revision",
            f"node {node_id!r} declares revisable_from={list(revisable_from)} without "
            "max_visits — a revision back-edge must be bounded (§R2 cost cap risk)",
        )
    return PackNode(
        id=node_id,
        depends_on=tuple(raw.get("depends_on", ())),
        model_role=raw.get("model_role", "brain_plan"),
        skill=raw.get("skill"),
        prompt=raw.get("prompt", ""),
        gate=bool(raw.get("gate", False)),
        external_effect=raw.get("external_effect"),
        revisable_from=revisable_from,
        max_visits=max_visits,
    )


def load_pack_graph(path: Path) -> PackGraph:
    """Load and fail-closed-validate a pack graph TOML file.

    Raises :class:`PackValidationError` (rule name in ``.rule``) on: a missing/duplicate
    node id, a dependency on an undeclared node, a dependency cycle, an unknown
    ``model_role``, an unknown ``skill``, ``revisable_from`` without ``max_visits``, or
    ``gate=true`` combined with a non-``"publish"`` ``external_effect``.
    """
    with Path(path).open("rb") as f:
        raw = tomllib.load(f)

    pack = raw.get("pack")
    variant = raw.get("variant")
    if not pack or not variant:
        raise PackValidationError("missing_header", f"{path}: 'pack' and 'variant' are required")

    raw_nodes = raw.get("nodes", [])
    if not raw_nodes:
        raise PackValidationError("empty_graph", f"{path}: pack graph declares no nodes")

    nodes = tuple(_build_node(n) for n in raw_nodes)

    ids = [n.id for n in nodes]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise PackValidationError("duplicate_node", f"duplicate node ids: {dupes}")

    known = set(ids)
    for n in nodes:
        unknown_deps = [d for d in n.depends_on if d not in known]
        if unknown_deps:
            raise PackValidationError(
                "dangling_dependency",
                f"node {n.id!r} depends_on undeclared node(s): {unknown_deps}",
            )

    _assert_acyclic(nodes)

    known_skill_names = {s.name for s in all_skills()}
    for n in nodes:
        if n.skill is not None and n.skill not in known_skill_names:
            raise PackValidationError(
                "unknown_skill", f"node {n.id!r} references unknown skill {n.skill!r}"
            )
        try:
            resolve_model(n.model_role)
        except ValueError as exc:
            raise PackValidationError("unknown_model_role", f"node {n.id!r}: {exc}") from exc
        if n.gate and n.external_effect not in (None, "publish"):
            raise PackValidationError(
                "unsafe_gate",
                f"node {n.id!r} has gate=true with external_effect={n.external_effect!r} — "
                "a gate may only pause for review or the publish mechanism, never another "
                "irreversible side effect",
            )

    return PackGraph(pack=pack, variant=variant, nodes=nodes)


@dataclass(frozen=True)
class PackInputSetting:
    key: str
    source: str  # "ask" | "derive"
    required: bool = False


@dataclass(frozen=True)
class PackInputKnowledge:
    topic: str
    required: bool = False
    freshness: str = "evergreen"  # "evergreen" | "90d"


@dataclass(frozen=True)
class PackInputs:
    settings: tuple[PackInputSetting, ...] = ()
    knowledge: tuple[PackInputKnowledge, ...] = ()


def capability_slugs_for_pack(pack: PackGraph) -> frozenset[str]:
    """Union of ``requires_capability`` slugs declared by every skill this pack references.

    Additive alongside — deliberately NOT replacing —
    ``gtm_core.capability_registry.KNOWN_CAPABILITIES``: only one pack graph exists today
    (marketing/linkedin-post) and several capability-gated skills aren't yet referenced by
    any pack, so swapping onboarding's validation over to "union of installed packs" now
    would silently shrink the valid set and break onboarding for those skills (E-3 exit
    criteria explicitly scope this as deferred until the pack roster covers the full skill
    set — see docs/SECURITY-SELF-ASSESSMENT.md). This function exists so that swap is a
    one-line change later, not a rewrite.
    """
    by_name = {s.name: s for s in all_skills()}
    slugs: set[str] = set()
    for n in pack.nodes:
        if n.skill and n.skill in by_name:
            slugs.update(by_name[n.skill].requires_capability)
    return frozenset(slugs)


def load_pack_inputs(path: Path) -> PackInputs:
    """Load and fail-closed-validate a pack's ``inputs.toml`` (§5's two-class inputs)."""
    with Path(path).open("rb") as f:
        raw = tomllib.load(f)

    settings = []
    for raw_s in raw.get("settings", []):
        key = raw_s.get("key")
        source = raw_s.get("source")
        if not key:
            raise PackValidationError("missing_key", f"{path}: a settings entry has no 'key'")
        if source not in _VALID_SETTING_SOURCES:
            raise PackValidationError(
                "unknown_source",
                f"settings.{key}: source={source!r} not in {sorted(_VALID_SETTING_SOURCES)}",
            )
        settings.append(
            PackInputSetting(key=key, source=source, required=bool(raw_s.get("required", False)))
        )

    knowledge = []
    for raw_k in raw.get("knowledge", []):
        topic = raw_k.get("topic")
        freshness = raw_k.get("freshness", "evergreen")
        if not topic:
            raise PackValidationError("missing_topic", f"{path}: a knowledge entry has no 'topic'")
        if freshness not in _VALID_FRESHNESS:
            raise PackValidationError(
                "unknown_freshness",
                f"knowledge.{topic}: freshness={freshness!r} not in {sorted(_VALID_FRESHNESS)}",
            )
        knowledge.append(
            PackInputKnowledge(
                topic=topic, required=bool(raw_k.get("required", False)), freshness=freshness
            )
        )

    return PackInputs(settings=tuple(settings), knowledge=tuple(knowledge))
