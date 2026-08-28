"""Which skills are reachable given a tenant's active pack set (E-3, PRD §7/§12.4).

Pure computation over ``packs.toml`` + the pack graphs it activates — proves "a disabled
pack's skills are unreachable" as a testable property.

**Wired into enforcement since Track A A1 (2026-08-09)**, on backend pack-mode runs, via two
levers fed from ``backend/routers/runs.py::_execute_pack_run``:

  1. ``agent/session.py:build_agent_options`` passes this set as the SDK ``skills=`` allowlist
     — unlisted skills are hidden from the model and rejected by the Skill tool (the primary,
     context-filter lever);
  2. ``agent/permissions.py`` (``classify_tool`` / ``make_headless_can_use_tool``) takes the
     same set as an ``allowed_skills`` scope and DENIES an out-of-set ``Skill`` invocation
     (defence-in-depth — the SDK option is a context filter, not a sandbox).

Fail-closed: no ``packs.toml`` ⇒ empty set ⇒ nothing reachable. Unscoped paths (VPS
cron/Telegram, ``allowed_skills=None``) are byte-identical to before this landed.

Tests: ``tests/backend/test_reachability_enforcement.py``.
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
    from gtm_core.paths import _safe_segment

    _safe_segment(profile, "profile")  # B1 defense-in-depth: reachable via MCP/CLI, not only HTTP
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


def entitled_skills_for_profile(
    profiles_root: Path, profile: str, packs_root: Path, entitlement: str
) -> frozenset[str]:
    """``active_skills_for_profile()`` intersected with the caller's COMMERCIAL
    entitlement.

    Reachability alone answers "can this profile's active packs get here at all" — it
    says nothing about entitlement, so a `pro` workspace whose packs reach a
    `pro_plus`-priced skill was invokable through the SDK allowlist until this filter
    was added. Filters against ``gtm_core.gating.commercial_floor()`` — the skill's
    COMMERCIAL floor, not its ``capability_tier`` directly: a skill can be technically
    ``PRODUCTION`` and commercially free (``airq-scan``), and this is what keeps it
    reachable for a free workspace while ``gtm_core.capabilities.resolve_effective()``
    separately (and correctly) still degrades it to a fallback for lacking connectors.

    Fail-closed on both factors: unreachable stays unreachable, and an unentitled skill
    is dropped even when reachable.
    """
    from gtm_core import gating
    from gtm_core.capabilities import entitlement_meets

    reachable = active_skills_for_profile(profiles_root, profile, packs_root)
    return frozenset(
        name for name in reachable if entitlement_meets(entitlement, gating.commercial_floor(name))
    )
