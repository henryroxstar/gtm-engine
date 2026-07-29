"""Tenant pack activation + monotone-stricter override merge (E-3, PRD §12.4).

``profiles/<tenant>/packs.toml`` names which packs are active and may declare an
**override** per pack: additions only. A tenant may add nodes or upgrade a node to a
gate; a tenant can never remove a node, weaken a gate, or drop a dependency the base
pack graph declared. Any attempt to do so is a fail-closed rejection at merge time —
CLAUDE.md's "Two human gates are permanent" invariant holds regardless of tenant config,
by construction, not by convention.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .loader import PackGraph, PackNode, PackValidationError, _assert_acyclic, _build_node


@dataclass(frozen=True)
class StrengthenEntry:
    """A tenant patch to an EXISTING base node: may only add depends_on or add a gate."""

    id: str
    add_depends_on: tuple[str, ...] = ()
    add_gate: bool = False  # True = "turn this node's gate on"; False = "leave as base declared"


@dataclass(frozen=True)
class PackOverride:
    pack: str
    variant: str
    add_nodes: tuple[PackNode, ...] = ()
    strengthen: tuple[StrengthenEntry, ...] = ()


@dataclass(frozen=True)
class TenantPackActivation:
    active: tuple[str, ...]
    overrides: dict[str, PackOverride]


def load_pack_activation(path: Path) -> TenantPackActivation:
    """Load ``profiles/<tenant>/packs.toml``. Raises :class:`PackValidationError` on a
    malformed override (unknown pack referenced, etc.) — activation itself never
    silently no-ops on bad config."""
    with Path(path).open("rb") as f:
        raw = tomllib.load(f)

    active = tuple(raw.get("active", []))
    overrides: dict[str, PackOverride] = {}
    for pack_name, raw_override in raw.get("overrides", {}).items():
        variant = raw_override.get("variant")
        if not variant:
            raise PackValidationError(
                "missing_override_variant", f"overrides.{pack_name}: 'variant' is required"
            )
        add_nodes = tuple(_build_node(n) for n in raw_override.get("add_nodes", []))
        strengthen = tuple(
            StrengthenEntry(
                id=s["id"],
                add_depends_on=tuple(s.get("add_depends_on", ())),
                add_gate=bool(s.get("add_gate", False)),
            )
            for s in raw_override.get("strengthen", [])
            if "id" in s
        )
        overrides[pack_name] = PackOverride(
            pack=pack_name, variant=variant, add_nodes=add_nodes, strengthen=strengthen
        )

    return TenantPackActivation(active=active, overrides=overrides)


def merge_pack_override(base: PackGraph, override: PackOverride) -> PackGraph:
    """Merge a tenant override into ``base``. Monotone-stricter only:

    - ``add_nodes`` may declare wholly NEW node ids (never re-declare a base id — that's
      how a tenant would sneak in a weakened replacement; use ``strengthen`` instead).
    - ``strengthen`` entries must reference an EXISTING base node id, and may only
      *append* to its ``depends_on`` (superset — more prerequisites, never fewer) and/or
      turn its ``gate`` on if it was off (never off if it was on).

    Raises :class:`PackValidationError` (rule ``override_removal`` or
    ``override_unknown_target``) on any attempt that isn't purely additive, and
    re-validates the merged graph as a whole (cycle/unknown-skill/unknown-role/etc.)
    so an override cannot introduce a new violation either.
    """
    if override.pack != base.pack or override.variant != base.variant:
        raise PackValidationError(
            "override_mismatch",
            f"override targets {override.pack}/{override.variant}, "
            f"base graph is {base.pack}/{base.variant}",
        )

    base_ids = set(base.ids)
    new_ids = {n.id for n in override.add_nodes}
    collide = new_ids & base_ids
    if collide:
        raise PackValidationError(
            "override_removal",
            f"add_nodes re-declares existing base node id(s) {sorted(collide)} — "
            "use 'strengthen' to patch an existing node, never re-add it",
        )

    strengthen_by_id = {s.id: s for s in override.strengthen}
    unknown_targets = set(strengthen_by_id) - base_ids
    if unknown_targets:
        raise PackValidationError(
            "override_unknown_target",
            f"strengthen references node(s) not in the base graph: {sorted(unknown_targets)}",
        )

    merged_nodes = []
    for n in base.nodes:
        s = strengthen_by_id.get(n.id)
        if s is None:
            merged_nodes.append(n)
            continue
        new_depends_on = tuple(dict.fromkeys((*n.depends_on, *s.add_depends_on)))
        new_gate = n.gate or s.add_gate  # OR — can only turn on, never off
        merged = PackNode(
            id=n.id,
            depends_on=new_depends_on,
            model_role=n.model_role,
            skill=n.skill,
            prompt=n.prompt,
            gate=new_gate,
            external_effect=n.external_effect,
            revisable_from=n.revisable_from,
            max_visits=n.max_visits,
        )
        # Defense in depth: the construction above cannot weaken by design, but assert
        # it explicitly so a future edit to this function fails loudly, not silently.
        if not new_gate and n.gate:
            raise PackValidationError(
                "override_removal", f"merge would remove node {n.id!r}'s gate"
            )
        if not set(n.depends_on) <= set(new_depends_on):
            raise PackValidationError(
                "override_removal", f"merge would drop a prerequisite of node {n.id!r}"
            )
        merged_nodes.append(merged)

    merged_nodes.extend(override.add_nodes)

    all_ids = [n.id for n in merged_nodes]
    if len(all_ids) != len(set(all_ids)):
        raise PackValidationError("duplicate_node", "merge produced duplicate node ids")
    known = set(all_ids)
    for n in merged_nodes:
        unknown_deps = [d for d in n.depends_on if d not in known]
        if unknown_deps:
            raise PackValidationError(
                "dangling_dependency",
                f"node {n.id!r} depends_on undeclared node(s): {unknown_deps}",
            )
    _assert_acyclic(tuple(merged_nodes))

    return PackGraph(pack=base.pack, variant=base.variant, nodes=tuple(merged_nodes))
