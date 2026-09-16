"""``gtm_core/gating.toml`` resolver — commercial (min_entitlement) and distribution
(oss public/private) policy.

Two axes live here, deliberately separate from ``capability_tier`` (technical — what a
skill needs to run, declared on the skill's own manifest and never duplicated here):

  commercial_floor(skill)   — the minimum Entitlement a workspace needs to reach a skill.
  oss_visibility(skill)     — whether the skill's implementation ships in the public
                               gtm-engine carve ("public") or is stubbed ("private").
  resolve_graph_entitlement — the same commercial question for a whole pack graph: the
                               max floor of its nodes, raised (never lowered) by a
                               matching [graphs.*] override.

``gtm_core/gating.toml`` is the single source of truth for both axes — changing what is
paid, or what ships publicly, is a one-file edit there, not a code change here. It is a
separate file from the skill manifest because the two axes cross: a skill can be
technically PRODUCTION and commercially free (``airq-scan``), and a graph can be priced
above every one of its own nodes' individual floors (the marketing pack).

Stdlib-only (mirrors gtm_core.packs.loader's ``tomllib`` choice — no new dependency).
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .capabilities import Entitlement, entitlement_rank
from .skills import codegen
from .skills.base import GTMSkill
from .skills.registry import all_skills
from .tiers import Tier

_POLICY_PATH = Path(__file__).with_name("gating.toml")

_TIER_DEFAULT_KEY = {
    Tier.CORE: "core",
    Tier.PIPELINE: "pipeline",
    Tier.PRODUCTION: "production",
}
_VALID_ENTITLEMENTS = frozenset(e.value for e in Entitlement if e is not Entitlement.NONE)
_VALID_TIER_KEYS = frozenset(_TIER_DEFAULT_KEY.values())
_VALID_OSS = frozenset({"public", "private"})
_KNOWN_SKILL_OVERRIDE_KEYS = frozenset({"min_entitlement", "oss", "reason"})
_KNOWN_GRAPH_OVERRIDE_KEYS = frozenset({"min_entitlement", "reason"})
_KNOWN_CARVE_KEYS = frozenset({"stub_bearing_graphs", "reason"})


class GatingPolicyError(ValueError):
    """Malformed gating.toml, or a resolution that violates its own floor rule.

    Mirrors ``gtm_core.packs.loader.PackValidationError``'s shape (a named ``.rule``
    plus message) so both fail-closed families read the same way at a call site.
    """

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        super().__init__(f"[{rule}] {message}")


@dataclass(frozen=True)
class GatingPolicy:
    defaults: dict[str, str]  # Tier value -> min_entitlement
    oss_private_tiers: frozenset[str]  # Tier values that default to a private carve stub
    skill_overrides: dict[str, dict]  # skill name -> {min_entitlement?, oss?, reason}
    graph_overrides: dict[str, dict]  # "<pack>/<variant-glob>" -> {min_entitlement, reason}
    # "<pack>/<variant>" entries allowed to ship a stubbed node. Defaults to EMPTY so a
    # policy that declares nothing fails closed on the first stub-bearing graph rather
    # than grandfathering it in.
    carve_stub_bearing_graphs: frozenset[str] = frozenset()


def _validate_entitlement(value: object, where: str) -> str:
    if not isinstance(value, str) or value not in _VALID_ENTITLEMENTS:
        raise GatingPolicyError(
            "unknown_entitlement",
            f"{where}: {value!r} is not a valid entitlement ({sorted(_VALID_ENTITLEMENTS)})",
        )
    return value


def load_policy(path: Path | None = None) -> GatingPolicy:
    """Load and fail-closed-validate ``gating.toml``.

    Raises :class:`GatingPolicyError` on: a missing/unknown default tier, an unknown
    entitlement value anywhere, an unrecognised override key, or an override with no
    ``reason`` (an override without a stated rationale is the thing that goes stale —
    the same reasoning tenant.py's ``_KNOWN_OVERRIDE_KEYS`` comment gives for rejecting
    unrecognised keys outright rather than silently ignoring them).
    """
    path = path or _POLICY_PATH
    with Path(path).open("rb") as f:
        raw = tomllib.load(f)

    defaults_raw = raw.get("defaults", {})
    defaults = {}
    for tier_key in ("core", "pipeline", "production"):
        if tier_key not in defaults_raw:
            raise GatingPolicyError("missing_default", f"[defaults] is missing {tier_key!r}")
        defaults[tier_key] = _validate_entitlement(defaults_raw[tier_key], f"[defaults].{tier_key}")
    oss_private_tiers_raw = defaults_raw.get("oss_private_tiers", ["production"])
    unknown_tiers = set(oss_private_tiers_raw) - _VALID_TIER_KEYS
    if unknown_tiers:
        raise GatingPolicyError(
            "unknown_tier",
            f"[defaults].oss_private_tiers: unknown tier(s) {sorted(unknown_tiers)} — only "
            f"{sorted(_VALID_TIER_KEYS)} are recognised",
        )
    oss_private_tiers = frozenset(oss_private_tiers_raw)

    skill_overrides: dict[str, dict] = {}
    for name, entry in raw.get("skills", {}).items():
        unknown = set(entry) - _KNOWN_SKILL_OVERRIDE_KEYS
        if unknown:
            raise GatingPolicyError(
                "unknown_key",
                f"[skills.{name}]: unknown key(s) {sorted(unknown)} — only "
                f"{sorted(_KNOWN_SKILL_OVERRIDE_KEYS)} are recognised",
            )
        if not str(entry.get("reason", "")).strip():
            raise GatingPolicyError(
                "missing_reason", f"[skills.{name}]: an override must carry a 'reason'"
            )
        if "min_entitlement" in entry:
            _validate_entitlement(entry["min_entitlement"], f"[skills.{name}].min_entitlement")
        if "oss" in entry and entry["oss"] not in _VALID_OSS:
            raise GatingPolicyError(
                "unknown_oss", f"[skills.{name}].oss={entry['oss']!r} not in {sorted(_VALID_OSS)}"
            )
        skill_overrides[name] = entry

    graph_overrides: dict[str, dict] = {}
    for pattern, entry in raw.get("graphs", {}).items():
        unknown = set(entry) - _KNOWN_GRAPH_OVERRIDE_KEYS
        if unknown:
            raise GatingPolicyError(
                "unknown_key",
                f'[graphs."{pattern}"]: unknown key(s) {sorted(unknown)} — only '
                f"{sorted(_KNOWN_GRAPH_OVERRIDE_KEYS)} are recognised",
            )
        if not str(entry.get("reason", "")).strip():
            raise GatingPolicyError(
                "missing_reason", f"[graphs.\"{pattern}\"]: an override must carry a 'reason'"
            )
        if "min_entitlement" not in entry:
            raise GatingPolicyError(
                "missing_min_entitlement", f'[graphs."{pattern}"] must declare min_entitlement'
            )
        _validate_entitlement(entry["min_entitlement"], f'[graphs."{pattern}"].min_entitlement')
        graph_overrides[pattern] = entry

    carve_raw = raw.get("carve", {})
    unknown = set(carve_raw) - _KNOWN_CARVE_KEYS
    if unknown:
        raise GatingPolicyError(
            "unknown_key",
            f"[carve]: unknown key(s) {sorted(unknown)} — only "
            f"{sorted(_KNOWN_CARVE_KEYS)} are recognised",
        )
    if "stub_bearing_graphs" in carve_raw and not str(carve_raw.get("reason", "")).strip():
        raise GatingPolicyError(
            "missing_reason", "[carve]: stub_bearing_graphs must carry a 'reason'"
        )
    stub_bearing_raw = carve_raw.get("stub_bearing_graphs", [])
    if not isinstance(stub_bearing_raw, list) or not all(
        isinstance(x, str) for x in stub_bearing_raw
    ):
        raise GatingPolicyError(
            "malformed_carve",
            "[carve].stub_bearing_graphs must be a list of '<pack>/<variant>' strings",
        )

    return GatingPolicy(
        defaults=defaults,
        oss_private_tiers=oss_private_tiers,
        skill_overrides=skill_overrides,
        graph_overrides=graph_overrides,
        carve_stub_bearing_graphs=frozenset(stub_bearing_raw),
    )


_policy_cache: GatingPolicy | None = None


def _policy() -> GatingPolicy:
    global _policy_cache
    if _policy_cache is None:
        _policy_cache = load_policy()
    return _policy_cache


def reset_policy_cache() -> None:
    """Test-only: clear the module-level cache so a test can point ``load_policy()``
    at a fixture file and have every unqualified call in this module see it."""
    global _policy_cache
    _policy_cache = None


def _skills_by_name() -> dict[str, GTMSkill]:
    return {s.name: s for s in all_skills()}


def commercial_floor(skill_name: str, *, policy: GatingPolicy | None = None) -> str:
    """The minimum entitlement a workspace needs to reach ``skill_name``.

    Fail-closed on an unrecognised name: floors at the highest rung (``pro_plus``)
    rather than the lowest — an unknown skill must never resolve as more available
    than a known one.
    """
    policy = policy or _policy()
    override = policy.skill_overrides.get(skill_name)
    if override and "min_entitlement" in override:
        return override["min_entitlement"]
    skill = _skills_by_name().get(skill_name)
    if skill is None:
        return Entitlement.PRO_PLUS.value
    return policy.defaults[_TIER_DEFAULT_KEY[skill.capability_tier]]


def entitled_skills(
    entitlement: Entitlement | str, *, policy: GatingPolicy | None = None
) -> frozenset[str]:
    """Every REGISTERED skill whose :func:`commercial_floor` ``entitlement`` clears.

    The commercial axis on its own, over the whole skill registry — no pack
    reachability. This is the skill scope for the backend's **prompt mode**, where
    there is no graph to price and no pack to narrow against: the caller types free
    text, so the only question the request itself answers is "what is this workspace
    entitled to reach". ``gtm_core.packs.reachability.entitled_skills_for_profile``
    is the pack-mode peer — the same commercial filter intersected with the profile's
    activated packs.

    Deliberately NOT the pack-reachable set: 27 of the 58 registered skills sit in no
    pack graph today (``account-dossier``, ``call-prep``, ``linkedin-reply``, …), so
    scoping free-text runs to the pack union would make them unreachable — a
    functional narrowing the entitlement boundary never asked for.

    Fail-closed on both sides: an unregistered name is not in the registry so it is
    never emitted, and :func:`commercial_floor` floors an unknown skill at
    ``pro_plus`` anyway.
    """
    policy = policy or _policy()
    from .capabilities import entitlement_meets

    return frozenset(
        name
        for name in _skills_by_name()
        if entitlement_meets(entitlement, commercial_floor(name, policy=policy))
    )


def tier_floor(tier: Tier, *, policy: GatingPolicy | None = None) -> str:
    """The ``[defaults]`` commercial floor for a technical tier, with no per-skill
    override applied.

    For gating a whole *capability class* rather than one named skill — the backend's
    ``require_tier`` dependency, which guards endpoints ("can this plan reach PIPELINE
    features at all?") and has no skill to resolve. Use
    :func:`commercial_floor` whenever a skill name exists: it is the one that honours
    ``[skills.*]`` overrides, and an override is exactly what makes a tier's default
    the wrong answer for a specific skill.
    """
    policy = policy or _policy()
    return policy.defaults[_TIER_DEFAULT_KEY[tier]]


def oss_visibility(skill_name: str, *, policy: GatingPolicy | None = None) -> str:
    """``"public"`` (ships in the OSS carve) or ``"private"`` (stubbed at export).

    Defaults from the skill's TECHNICAL tier (``[defaults].oss_private_tiers``), never
    from its commercial floor — a CORE/PIPELINE skill sold at "pro_plus" (every
    marketing-pack skill) must still default public, and a PRODUCTION skill repriced
    down to "pro" (every creator render skill) must still default private. Fail-closed
    on an unrecognised skill name: private, not public.
    """
    policy = policy or _policy()
    override = policy.skill_overrides.get(skill_name)
    if override and "oss" in override:
        return override["oss"]
    skill = _skills_by_name().get(skill_name)
    if skill is None:
        return "private"
    tier_key = _TIER_DEFAULT_KEY[skill.capability_tier]
    return "private" if tier_key in policy.oss_private_tiers else "public"


def stub_list(*, policy: GatingPolicy | None = None) -> frozenset[str]:
    """Every skill name whose resolved ``oss_visibility`` is ``"private"``."""
    policy = policy or _policy()
    return frozenset(
        name for name in _skills_by_name() if oss_visibility(name, policy=policy) == "private"
    )


def _match_graph_override(pack: str, variant: str, policy: GatingPolicy) -> dict | None:
    key = f"{pack}/{variant}"
    for pattern, entry in policy.graph_overrides.items():
        if fnmatch.fnmatchcase(key, pattern):
            return entry
    return None


def derive_graph_floor(nodes, *, policy: GatingPolicy | None = None) -> str:
    """``max(commercial_floor(node.skill) for every skill-bearing node)``; ``"free"``
    when the graph has no skill-bearing node (structurally impossible today, but the
    loader's own graph contract doesn't rule it out)."""
    policy = policy or _policy()
    floor = Entitlement.FREE.value
    for n in nodes:
        skill_name = getattr(n, "skill", None)
        if skill_name is None:
            continue
        candidate = commercial_floor(skill_name, policy=policy)
        if entitlement_rank(candidate) > entitlement_rank(floor):
            floor = candidate
    return floor


def resolve_graph_entitlement(
    pack: str,
    variant: str,
    nodes,
    *,
    explicit: str | None = None,
    policy: GatingPolicy | None = None,
) -> str:
    """Effective ``min_entitlement`` for a graph: the node-derived floor, raised (never
    lowered) by a matching ``[graphs.*]`` override, raised again (never lowered) by an
    explicit literal header on the graph TOML itself, if the caller passes one.

    Raises :class:`GatingPolicyError` (rule ``override_below_floor`` /
    ``below_floor``) rather than silently ignoring an override or header that would
    *lower* the effective value below what the graph's own nodes require — a stale
    override that no longer covers its graph is a bug worth surfacing, not masking.
    """
    policy = policy or _policy()
    floor = derive_graph_floor(nodes, policy=policy)
    effective = floor
    override = _match_graph_override(pack, variant, policy)
    if override is not None:
        value = override["min_entitlement"]
        if entitlement_rank(value) < entitlement_rank(floor):
            raise GatingPolicyError(
                "override_below_floor",
                f"[graphs] override for {pack}/{variant} sets min_entitlement={value!r}, "
                f"below the floor {floor!r} derived from its node skill tiers",
            )
        effective = value
    if explicit is not None:
        if entitlement_rank(explicit) < entitlement_rank(effective):
            raise GatingPolicyError(
                "below_floor",
                f"{pack}/{variant}: declared min_entitlement={explicit!r} is below the "
                f"required floor {effective!r}",
            )
        effective = explicit
    return effective


# ── OSS carve stubbing (Gate C) ────────────────────────────────────────────────────


def _graph_refs_for_skill(skill_name: str, packs_root: Path) -> list[str]:
    """Every ``<pack>/<variant>`` whose graph references ``skill_name`` as a node —
    informational only (folded into the generated stub body); never raises on a
    malformed graph file, which is another gate's job."""
    from .packs.loader import PackValidationError, load_pack_graph

    refs: list[str] = []
    if not packs_root.is_dir():
        return refs
    for graph_path in sorted(packs_root.glob("*/graphs/*.toml")):
        try:
            graph = load_pack_graph(graph_path)
        except PackValidationError:
            continue
        if any(n.skill == skill_name for n in graph.nodes):
            refs.append(f"{graph.pack}/{graph.variant}")
    return refs


def stub_bearing_graphs(
    packs_root: Path, *, policy: GatingPolicy | None = None
) -> dict[str, list[str]]:
    """``{"<pack>/<variant>": [private skill names it runs as nodes]}`` for every graph
    under ``packs_root`` that references at least one ``oss = "private"`` skill.

    This is the OSS-distribution counterpart to
    ``test_no_private_skill_sits_in_a_free_graph``'s COMMERCIAL invariant, and the gap
    that test cannot see: a self-hoster has no entitlement at all (the unscoped
    cockpit/VPS path runs ``allowed_skills=None``), so the commercial floor that keeps a
    free workspace away from a stub does nothing for them. Such a graph is still
    shippable — the stub says plainly that the node is hosted-only — but it must be a
    DECLARED choice in ``[carve].stub_bearing_graphs``, not a silent by-product of a
    later gating edit.
    """
    from .packs.loader import PackValidationError, load_pack_graph

    policy = policy or _policy()
    private = stub_list(policy=policy)
    out: dict[str, list[str]] = {}
    if not packs_root.is_dir():
        return out
    for graph_path in sorted(packs_root.glob("*/graphs/*.toml")):
        try:
            graph = load_pack_graph(graph_path)
        except PackValidationError:
            continue
        hits = sorted({n.skill for n in graph.nodes if n.skill in private})
        if hits:
            out[f"{graph.pack}/{graph.variant}"] = hits
    return out


def undeclared_stub_bearing_graphs(
    packs_root: Path, *, policy: GatingPolicy | None = None
) -> dict[str, list[str]]:
    """The subset of :func:`stub_bearing_graphs` that ``[carve].stub_bearing_graphs``
    does not declare. Non-empty means the carve is about to ship a graph whose render
    node is a stub without anyone having decided that — the fail-closed case."""
    policy = policy or _policy()
    found = stub_bearing_graphs(packs_root, policy=policy)
    return {k: v for k, v in found.items() if k not in policy.carve_stub_bearing_graphs}


def stale_stub_bearing_declarations(
    packs_root: Path, *, policy: GatingPolicy | None = None
) -> list[str]:
    """Declared entries that no longer name a stub-bearing graph — a stale allowlist
    entry silently widens what a future edit may ship, so it is surfaced too."""
    policy = policy or _policy()
    found = stub_bearing_graphs(packs_root, policy=policy)
    return sorted(policy.carve_stub_bearing_graphs - set(found))


_INTERFACE_HEADING_RE = re.compile(
    r"^(##\s+(?:Interface(?:\s+Contract)?|Contract)\b.*?)$", re.MULTILINE | re.IGNORECASE
)
_NEXT_HEADING_RE = re.compile(r"^#{1,2}\s+", re.MULTILINE)


def extract_interface_contract(text: str) -> str:
    """Extract the interface contract section from a skill's body text if present."""
    m = _INTERFACE_HEADING_RE.search(text)
    if not m:
        return ""
    start = m.end()
    rest = text[start:]
    next_heading = _NEXT_HEADING_RE.search(rest)
    if next_heading:
        return rest[: next_heading.start()].strip()
    return rest.strip()


def strip_interface_contract(text: str) -> str:
    """Strip the interface contract section from a skill's body text so only the
    withheld implementation remains for leak checking."""
    m = _INTERFACE_HEADING_RE.search(text)
    if not m:
        return text
    start = m.start()
    rest = text[m.end() :]
    next_heading = _NEXT_HEADING_RE.search(rest)
    if next_heading:
        return text[:start] + rest[next_heading.start() :]
    return text[:start]


def render_stub_body(
    skill: GTMSkill,
    graph_refs: list[str],
    contract: str = "",
    referenced_docs: list[str] = (),
) -> str:
    lines = [
        "This skill's implementation is part of the hosted product and is not included",
        "in this distribution.",
        "",
        "**Stop here.** Do not improvise a replacement procedure, and do not fall back to",
        "another skill: report to the operator that this step is unavailable in this",
        "distribution and end the run. A graph reaching this node cannot complete, and an",
        "improvised substitute would spend budget producing something the pack does not",
        "specify.",
        "",
        f"Its declared interface is above (`{skill.name}`, tier `{skill.capability_tier.value}`).",
    ]
    if graph_refs:
        joined = ", ".join(f"`{r}`" for r in graph_refs)
        lines.append(f"Pack graph node(s) that invoke it: {joined}.")
    lines += ["", "See `docs/SKILLS.md` for the full skill roster."]
    if referenced_docs:
        joined = ", ".join(f"`{d}`" for d in referenced_docs)
        lines.append(f"Docs it draws on that ship in this distribution: {joined}.")
    if contract.strip():
        lines += ["", "## Interface Contract", "", contract.strip()]
    return "\n".join(lines) + "\n"


#: Any docs/*.md-shaped path — mirrors tests/lint/carve_surface_check.py's DOC_REF, kept
#: separate rather than imported since gtm_core must not depend on the tests/ tree.
_DOC_REF = re.compile(r"docs/[A-Za-z0-9/_.-]+\.md")


def _public_doc_refs(skill_dir: Path, carved_root: Path) -> list[str]:
    """``docs/*.md`` paths cited anywhere in a private skill's withheld files that also
    ship publicly (already carved under ``carved_root/docs``). The doc's own presence in
    the public tree means citing its filename leaks nothing new — this only lets a stub
    say which shipped doc its (withheld) recipe draws on, e.g. for
    ``tests/test_direct_response_patterns.py``'s cross-skill citation check."""
    found: list[str] = []
    seen: set[str] = set()
    for p in sorted(skill_dir.rglob("*")):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _DOC_REF.finditer(text):
            doc = m.group(0)
            if doc in seen:
                continue
            seen.add(doc)
            if (carved_root / doc).is_file():
                found.append(doc)
    return sorted(found)


def stub_carve(carved_root: Path) -> list[str]:
    """Replace every ``oss = "private"`` skill's directory under ``<carved_root>/plugin/
    skills/`` with a stub containing only a generated ``SKILL.md`` (frontmatter intact,
    body replaced) — the manifest that keeps ``gtm_core/skills/<name>.py`` unchanged.

    Wipes the WHOLE skill directory first (not just ``body_template.md``): a
    ``references/`` folder of prompt recipes is exactly as much the asset as the body
    itself, and this way a future file added to a private skill's folder is excluded
    automatically rather than needing this function updated to know about it.

    Raises :class:`GatingPolicyError` if a private skill has no manifest or no carved
    directory — both mean the carve is broken, not that the skill can be silently
    skipped.
    """
    carved_root = Path(carved_root)
    plugin_root = carved_root / "plugin"
    packs_root = carved_root / "packs"
    by_name = _skills_by_name()
    stubbed: list[str] = []
    for name in sorted(stub_list()):
        skill = by_name.get(name)
        if skill is None:
            raise GatingPolicyError(
                "unknown_skill",
                f"gating.toml resolves {name!r} as private but no manifest is registered",
            )
        skill_dir = plugin_root / "skills" / name
        if not skill_dir.is_dir():
            raise GatingPolicyError(
                "missing_skill_dir",
                f"private skill {name!r} has no carved directory at {skill_dir}",
            )
        contract = ""
        body_template_path = skill_dir / "body_template.md"
        if body_template_path.is_file():
            try:
                contract = extract_interface_contract(
                    body_template_path.read_text(encoding="utf-8")
                )
            except OSError:
                contract = ""
        referenced_docs = _public_doc_refs(skill_dir, carved_root)
        body = render_stub_body(
            skill,
            _graph_refs_for_skill(name, packs_root),
            contract=contract,
            referenced_docs=referenced_docs,
        )
        shutil.rmtree(skill_dir)
        skill_dir.mkdir(parents=True)
        # render_frontmatter() only (not codegen.render()): deliberately never emit a
        # fallback_note into a stub even though none of today's private skills set one —
        # the private axis must fail closed on future skills too.
        (skill_dir / "SKILL.md").write_text(
            codegen.render_frontmatter(skill) + body, encoding="utf-8"
        )
        stubbed.append(name)
    return stubbed


_MIN_LEAK_LINE_LEN = 40


def _content_files(skill_dir: Path) -> list[Path]:
    """Every file under a skill's plugin directory except ``SKILL.md`` — the
    generated/shared frontmatter+body is never the withheld asset; the withheld asset
    is whatever else is there (``body_template.md``, ``references/*``, ...)."""
    if not skill_dir.is_dir():
        return []
    return [p for p in skill_dir.rglob("*") if p.is_file() and p.name != "SKILL.md"]


def _collect_lines(paths: list[Path], min_len: int = _MIN_LEAK_LINE_LEN) -> set[str]:
    lines: set[str] = set()
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if p.name == "body_template.md":
            text = strip_interface_contract(text)
        for raw in text.splitlines():
            line = raw.strip()
            if len(line) >= min_len:
                lines.add(line)
    return lines


def find_leaked_content(carved_root: Path, plugin_source_root: Path | None = None) -> list[str]:
    """Verified backstop for :func:`stub_carve`: confirm none of a private skill's
    WITHHELD source content (never its ``SKILL.md`` interface, which is meant to ship)
    reached ``carved_root``.

    "Distinguishing" content only — a line is checked ONLY if it does not also appear
    in some PUBLIC skill's own (legitimately-shipped) files. Prompt authoring in this
    repo shares a lot of boilerplate prose across the whole skill library ("Resolve the
    active profile...", standard read-only warnings, ...); a plain substring/line sweep
    over that boilerplate produces false positives on every public skill that happens
    to share a sentence with a private one. Set-difference against the public corpus is
    what makes this check precise rather than merely loud.
    """
    plugin_source_root = plugin_source_root or (Path(__file__).resolve().parent.parent / "plugin")
    skills_root = plugin_source_root / "skills"
    private = stub_list()
    public_lines: set[str] = set()
    private_lines: set[str] = set()
    if skills_root.is_dir():
        for skill_dir in sorted(skills_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            lines = _collect_lines(_content_files(skill_dir))
            if skill_dir.name in private:
                private_lines |= lines
            else:
                public_lines |= lines
    distinguishing = private_lines - public_lines
    if not distinguishing:
        return []

    hits: list[str] = []
    carved_root = Path(carved_root)
    for p in carved_root.rglob("*"):
        if not p.is_file() or ".git" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if line in distinguishing:
                hits.append(f"{p}: {line[:160]}")
    return hits


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gtm_core.gating")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stub-list", help="print every private skill name, one per line")
    p_carve = sub.add_parser("stub-carve", help="rewrite private skill dirs under <carved-root>")
    p_carve.add_argument("carved_root")
    p_leak = sub.add_parser(
        "leak-check", help="fail (rc=1) if withheld private-skill content reached <carved-root>"
    )
    p_leak.add_argument("carved_root")
    p_graphs = sub.add_parser(
        "graph-check",
        help="fail (rc=1) if <carved-root> ships a graph whose node is a private stub "
        "without a [carve].stub_bearing_graphs declaration",
    )
    p_graphs.add_argument("carved_root")
    args = parser.parse_args(argv)

    if args.cmd == "stub-list":
        for name in sorted(stub_list()):
            print(name)
        return 0
    if args.cmd == "stub-carve":
        for name in stub_carve(Path(args.carved_root)):
            print(f"stubbed {name}")
        return 0
    if args.cmd == "leak-check":
        hits = find_leaked_content(Path(args.carved_root))
        for h in hits:
            print(h)
        return 1 if hits else 0
    if args.cmd == "graph-check":
        packs_root = Path(args.carved_root) / "packs"
        undeclared = undeclared_stub_bearing_graphs(packs_root)
        stale = stale_stub_bearing_declarations(packs_root)
        for ref, skills in sorted(undeclared.items()):
            print(f"undeclared stub-bearing graph: {ref} runs private {', '.join(skills)}")
        for ref in stale:
            print(f"stale [carve].stub_bearing_graphs entry: {ref} carries no private node")
        if undeclared or stale:
            print(
                "fix: add or remove the entry in gtm_core/gating.toml [carve]."
                "stub_bearing_graphs (with a reason), or stop shipping the graph"
            )
            return 1
        declared = stub_bearing_graphs(packs_root)
        for ref, skills in sorted(declared.items()):
            print(f"  declared stub-bearing: {ref} ({', '.join(skills)})")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
