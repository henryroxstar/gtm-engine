"""Pack catalog for the client API (A1/A12) — shared by the packs router and run creation.

Builds `PackDescriptor` dicts (schemas/pack-descriptor.schema.json) from the in-repo
pack roster, filtered through the workspace profile's tenant activation
(`profiles/<p>/packs.toml`, fail-closed: no file ⇒ nothing active) with the
monotone-stricter override merge applied, and annotates each variant with the
server-computed `available`/`locked_reason` (entitlement) and `readiness`
(agent/readiness.py verdict, translated green|yellow|red → ready|degraded|blocked).

Layering: backend → agent → gtm_core is the legal import direction
(tests/contracts/test_layering.py); this module deliberately holds no FastAPI types
so it is unit-testable without an app.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agent.readiness import GREEN, RED, ReadinessReport, check_readiness
from gtm_core import gating
from gtm_core.capabilities import entitlement_meets
from gtm_core.packs.loader import (
    PackGraph,
    PackInputs,
    PackValidationError,
    load_pack_graph,
    load_pack_inputs,
)
from gtm_core.packs.tenant import load_pack_activation, merge_pack_override

# Pack/variant names are directory + file segments supplied by the CLIENT on the run
# route — guard the shape before any path is built (tenant-boundary rule: bare names
# only, no traversal). Mirrors gtm_core.paths' bare-segment discipline.
_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class PackResolutionError(Exception):
    """Typed resolution failure — the routers map `code` onto the HTTP envelope."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code  # unknown_variant | pack_not_activated | pack_invalid
        super().__init__(message or code)


def safe_segment(value: str) -> bool:
    return bool(_SEGMENT_RE.match(value or ""))


@dataclass(frozen=True)
class ResolvedVariant:
    """One runnable variant: merged graph + declared inputs."""

    graph: PackGraph
    inputs: PackInputs


def _activation(profiles_root: Path, profile: str):
    """The profile's tenant activation; missing packs.toml ⇒ nothing active (fail-closed)."""
    from gtm_core.paths import _safe_segment

    _safe_segment(profile, "profile")  # B1: never join a traversal-shaped profile segment
    path = profiles_root / profile / "packs.toml"
    if not path.is_file():
        return None
    return load_pack_activation(path)


def resolve_variant(
    repo_root: Path, profiles_root: Path, profile: str, pack: str, variant: str
) -> ResolvedVariant:
    """Resolve (pack, variant) for a profile: load, activation-check, override-merge.

    Raises :class:`PackResolutionError` with code:
      - ``unknown_variant``   — bad name shape, or no such graph file (404 at the API)
      - ``pack_not_activated``— graph exists but the profile doesn't activate the pack (403)
      - ``pack_invalid``      — the graph or the tenant override fails validation (422;
                                server/tenant config problem, never client input)
    """
    if not (safe_segment(pack) and safe_segment(variant)):
        raise PackResolutionError("unknown_variant")
    graph_path = repo_root / "packs" / pack / "graphs" / f"{variant}.toml"
    if not graph_path.is_file():
        raise PackResolutionError("unknown_variant")

    activation = _activation(profiles_root, profile)
    if activation is None or pack not in activation.active:
        raise PackResolutionError("pack_not_activated")

    try:
        graph = load_pack_graph(graph_path)
        override = activation.overrides.get(pack)
        if override is not None and override.variant == variant:
            graph = merge_pack_override(graph, override)
    except PackValidationError as exc:
        raise PackResolutionError("pack_invalid", str(exc)) from exc

    inputs = load_pack_inputs(repo_root / "packs" / pack / "inputs.toml")
    return ResolvedVariant(graph=graph, inputs=inputs)


def list_variants(repo_root: Path, profiles_root: Path, profile: str) -> list[ResolvedVariant]:
    """Every variant the profile can actually run (activated packs only, merged).

    A variant that fails validation is SKIPPED from the listing (and logged by the
    caller) rather than 500ing the whole catalog — one broken pack file must not
    hide every other pack from every client.
    """
    activation = _activation(profiles_root, profile)
    if activation is None:
        return []
    out: list[ResolvedVariant] = []
    for pack in activation.active:
        graphs_dir = repo_root / "packs" / pack / "graphs"
        if not graphs_dir.is_dir():
            continue
        for graph_path in sorted(graphs_dir.glob("*.toml")):
            try:
                out.append(
                    resolve_variant(repo_root, profiles_root, profile, pack, graph_path.stem)
                )
            except PackResolutionError:
                continue
    return out


# ── readiness translation (module colors → wire vocabulary) ───────────────────
#
# Readiness is PROFILE-shaped provisioning only. An `ask` setting is collected by
# the run-launch form, so its absence from PROFILE.md can never be a launch
# blocker — it demotes to `degraded` here, and its required-ness is enforced
# request-shaped by `missing_settings` at run creation (A12 §2.3 error ordering).


def _ask_keys(resolved: ResolvedVariant) -> frozenset[str]:
    return frozenset(s.key for s in resolved.inputs.settings if s.source == "ask")


def _item_status(item, ask_keys: frozenset[str]) -> str:
    if item.status == GREEN:
        return "ready"
    if item.kind == "setting" and item.name in ask_keys:
        return "degraded"  # collectable at run launch — never blocks
    if item.status == RED and item.required:
        return "blocked"
    return "degraded"  # YELLOW, or RED on an optional input


def readiness_block(report: ReadinessReport, resolved: ResolvedVariant, *, all_items: bool) -> dict:
    """The wire `readiness` object. Listing carries only non-ready items (compact);
    the detail endpoint carries every item. Reasons are the module's path-free
    ``detail`` sentences."""
    ask = _ask_keys(resolved)
    items = []
    worst = "ready"
    for item in report.items:
        status = _item_status(item, ask)
        if status == "blocked":
            worst = "blocked"
        elif status == "degraded" and worst != "blocked":
            worst = "degraded"
        if not all_items and status == "ready":
            continue
        entry = {"kind": item.kind, "name": item.name, "status": status}
        if item.detail:
            entry["reason"] = item.detail
        items.append(entry)
    return {"status": worst, "items": items}


def blocked_items(report: ReadinessReport, resolved: ResolvedVariant) -> list[dict]:
    """The `blocked[]` payload of the 422 pack_not_ready envelope — translated
    items only (ask-settings already demoted, so this is pure profile provisioning)."""
    ask = _ask_keys(resolved)
    out = []
    for item in report.items:
        if _item_status(item, ask) == "blocked":
            entry = {"kind": item.kind, "name": item.name}
            if item.detail:
                entry["reason"] = item.detail
            out.append(entry)
    return out


def variant_readiness(
    profiles_root: Path, profile: str, resolved: ResolvedVariant
) -> ReadinessReport:
    return check_readiness(profiles_root, profile, resolved.inputs)


# ── descriptor build ──────────────────────────────────────────────────────────


def descriptor(
    resolved: ResolvedVariant,
    *,
    entitlement: str,
    readiness: ReadinessReport | None,
) -> dict:
    """One PackDescriptor dict (schemas/pack-descriptor.schema.json).

    Never exposes ``prompt`` or ``model_role`` — the API must not leak
    orchestration detail (schema's normative exclusion).
    """
    g = resolved.graph
    d: dict = {
        "pack": g.pack,
        "variant": g.variant,
        "nodes": [
            {
                "id": n.id,
                "depends_on": list(n.depends_on),
                "skill": n.skill,
                "gate": n.gate,
                "external_effect": n.external_effect,
            }
            for n in g.nodes
        ],
        "inputs": {
            "settings": [
                {"key": s.key, "source": s.source, "required": s.required}
                for s in resolved.inputs.settings
            ],
            "knowledge": [
                {"topic": k.topic, "required": k.required, "freshness": k.freshness}
                for k in resolved.inputs.knowledge
            ],
        },
    }
    if g.title:
        d["title"] = g.title
    if g.description:
        d["description"] = g.description
    # Effective min_entitlement: the derived floor of the graph's own node skill tiers
    # (gtm_core/gating.toml), raised by a matching [graphs.*] override, raised again by
    # an explicit literal header on the graph TOML itself — never just the raw header.
    # Always populated now (not only when a literal header is present): every graph has
    # a resolvable floor, "free" included, so the client-facing field is honest rather
    # than silently absent for the packs that need gating most.
    effective_min_entitlement = gating.resolve_graph_entitlement(
        g.pack, g.variant, g.nodes, explicit=g.min_entitlement
    )
    d["min_entitlement"] = effective_min_entitlement
    available = entitlement_meets(entitlement, effective_min_entitlement)
    d["available"] = available
    if not available:
        d["locked_reason"] = "entitlement_required"
    if readiness is not None:
        d["readiness"] = readiness_block(readiness, resolved, all_items=False)
    return d


def missing_required_settings(resolved: ResolvedVariant, provided: dict[str, str]) -> list[str]:
    """Required ask-settings absent from the run request's inputs map (A1 422 envelope)."""
    return [
        s.key
        for s in resolved.inputs.settings
        if s.source == "ask" and s.required and not (provided.get(s.key) or "").strip()
    ]
