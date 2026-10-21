#!/usr/bin/env python3
"""Keep a WITHHELD skill's `description` an interface, not a lab notebook.

`gtm_core.gating.stub_carve` withholds a private skill's `plugin/skills/<name>/` from the
OSS carve, and `strip_manifest_docstrings` withholds its manifest module docstring. One
field survives both and must: `description`. It is rendered into the stub's own SKILL.md
frontmatter by `codegen.render_frontmatter`, and it is what an agent reads to decide
whether to invoke the skill at all — so it cannot be machine-trimmed without producing
bad routing prose, and it cannot be dropped without shipping a graph node nobody can
reason about.

That makes it the one place where withheld material can accumulate unnoticed. It did: at
the 2026-09-07 audit `video-avatar`'s description had grown to 4,075 characters, ~57% of
it dated provider forensics — a measured per-second billing rate, a `<break time>` floor
with its 12-of-12 job evidence, a queue-serialisation timing, and two "this shipped twice"
post-mortems. None of that helps an agent route; all of it is expensive operating
knowledge, and all of it shipped.

THE RULE, and why it is phrased as it is. A *spec* you apply forward stays — `video-clip`'s
"centre 60% of frame, ~48-62px at 1080 width" is a constraint the skill enforces. A
*measurement* observed once goes: it belongs in `body_template.md`, which is withheld, next
to the step it bears on. The mechanical discriminator is a DATE. The two manifests that were
already right-sized when this was written (`carousel-visuals`, `video-clip`) carry zero dates
between them; the three that were not carried one, one and two. A date in a description is
almost always the tell that a paragraph is recording what happened rather than declaring what
the skill does.

Scope is deliberately the PRIVATE skills only. A public skill's body ships anyway, so
forensics in its description leaks nothing extra, and narrowing the rule keeps it honest
rather than merely loud.

Relationship to the neighbouring lint: `tests/lint/test_no_stale_provider_facts_in_bodies.py`
bans a hardcoded credit BALANCE in these same files and explicitly PERMITS a rate card
("~23 credits/min" is named as allowed) because a rate is what a cost estimate is built
from. That rule is about staleness and applies to bodies and manifests alike. This one is
about DISTRIBUTION and applies only to the field that survives the carve — which is why a
rate is allowed there and refused here, in a description, on a skill whose body is withheld.

Stdlib-only, and reads the manifests by AST rather than importing `gtm_core`, so it runs at
pre-commit time without a synced environment.

SECOND RULE IN THIS FILE (§R16). The rule above only looks at skills that are ALREADY resolved
private — it reads `stub_list()`, which is derived from `capability_tier`. So it is blind to the
defect that started this work: `video-avatar` was declared PIPELINE while rendering video on
HeyGen, which made it PUBLIC by derivation, and its body shipped in full for seventeen days
across two releases. Nothing caught it because every gate downstream trusts the tier.

So this file also asserts the tier from the other side: a skill whose body invokes a paid
GENERATION verb must resolve `oss = "private"`. The escape hatch is an explicit
`[skills.<name>] oss = "public"` in gating.toml, which already requires a `reason` — that is how
`airq-scan` (technically PRODUCTION, sold free, shipped public by founder decision) passes. The
point is that shipping a generation body becomes a decision someone wrote down, rather than a
by-product of a tier nobody checked.

Layers (docs/RULES.md §R15, §R16): pre-commit, CI, pytest, and the release export.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).with_name("manifest_prose_allow.txt")

#: A description above this is over budget whatever it contains — the blunt backstop for
#: verbose prose that dodges both patterns above.
#:
#: Following the 2026-09-11 skill manifest rightsizing initiative, all skill descriptions
#: are decoupled from interface contracts (now cleanly in body_template.md under `## Interface Contract`)
#: and capped at 500 characters to prevent context window saturation in AI agents and Antigravity.
MAX_DESCRIPTION_CHARS = 500

#: An ISO date, or a "we checked this on..." phrasing followed by a year. Both forms appear
#: in the prose this rule exists to move.
_DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\b(?:verified|measured|found live|observed|confirmed|tested)\b[^.]{0,40}?\b20\d{2}\b",
    re.IGNORECASE,
)

#: A measured quantity: an "N of N" trial count, a balance delta, or a one-off cost claim.
#: Deliberately narrower than "any number" — shot counts, pixel sizes and percentages are
#: specs a skill enforces, and banning those would delete the interface along with the notes.
#:
#: A RATE ("0.865 credits/second", "~23 credits per render") is deliberately NOT matched here.
#: It is owned by §R17 (`tests/lint/provider_rate_check.py`), which polices rates across every
#: brain-read surface rather than only a private skill's description. The two rules are
#: PARTITIONED on purpose: a string reported by both would train a reader to skim the output,
#: and then the real findings go unread with it.
_MEASUREMENT = re.compile(
    r"\b\d+\s+of\s+\d+\s+(?:jobs?|renders?|frames?|videos?|runs?|samples?)\b"
    r"|\bbalance\s+delta\b"
    r"|\b\d[\d,]*\s*credits?\b[^.]{0,30}?\b(?:cost|spent|charged|billed)\b",
    re.IGNORECASE,
)

#: Paid GENERATION verbs — a call that produces new media and bills for it. Deliberately not
#: every metered tool: `add_captions`, `transcribe` and `virality_predictor` cost money but
#: produce no asset, and the skills that call them (`video-finish`, `video-score`) are correctly
#: PIPELINE and correctly public. The question this asks is "does this skill make the product",
#: not "does this skill spend".
_GENERATION_VERBS = re.compile(
    r"\b(generate_image|generate_video|generate_audio|generate_3d|generate_image_batch|"
    r"generate_video_batch|generate_audio_batch|upscale_image|upscale_video|"
    r"create_video_from_avatar|create_video_from_image|create_lipsync|create_clips|"
    r"motion_control|outpaint_image|dubbing|voice_change|clone_voice|design_voice)\b"
)


def scan_tier_mismatch(repo: Path) -> list[str]:
    """§R16 — a skill whose body invokes a paid generation verb must resolve oss = "private".

    Checked from the BODY rather than from the tier, because the tier is the thing that was
    wrong. An explicit `oss = "public"` override in gating.toml (which must carry a `reason`)
    is the sanctioned way to ship one anyway.
    """
    import tomllib as _t

    policy = _t.loads((repo / "gtm_core" / "gating.toml").read_text(encoding="utf-8"))
    overrides = policy.get("skills", {})
    private = private_skill_names(repo)
    findings: list[str] = []
    for name, path, _tier, _desc in iter_manifests(repo):
        body = repo / "plugin" / "skills" / name / "body_template.md"
        if not body.is_file():
            continue
        verbs = sorted(set(_GENERATION_VERBS.findall(body.read_text(encoding="utf-8"))))
        if not verbs or name in private:
            continue
        if overrides.get(name, {}).get("oss") == "public":
            continue  # a written-down decision, with its reason, in gating.toml
        findings.append(
            f"  {path.relative_to(repo)}: declared tier {_tier!r} resolves oss=public, but "
            f"plugin/skills/{name}/body_template.md calls {', '.join(verbs)} — a generation "
            f"body would ship in the OSS carve. Set capability_tier=Tier.PRODUCTION, or add "
            f'[skills.{name}] oss = "public" to gating.toml with a reason.'
        )
    return findings


_RULES = (
    ("date", _DATE, "a date belongs in the withheld body, not in the shipped description"),
    ("measurement", _MEASUREMENT, "a measured rate/trial count belongs in the withheld body"),
)


def load_allowlist() -> set[str]:
    """`<skill>:<rule>` entries, each of which must carry a dated reason on the same line."""
    if not ALLOWLIST.is_file():
        return set()
    out: set[str] = set()
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line.split()[0])
    return out


def private_skill_names(repo: Path) -> set[str]:
    """Resolve `oss = "private"` from gating.toml + each manifest's declared tier.

    Mirrors `gtm_core.gating.oss_visibility` deliberately rather than importing it: this
    module runs in pre-commit, where the package may not be importable. `tests/lint/
    test_manifest_prose_check.py` pins the two against each other so the copy cannot drift.
    """
    policy = tomllib.loads((repo / "gtm_core" / "gating.toml").read_text(encoding="utf-8"))
    private_tiers = set(policy.get("defaults", {}).get("oss_private_tiers", ["production"]))
    overrides = policy.get("skills", {})
    names: set[str] = set()
    for name, _path, tier, _desc in iter_manifests(repo):
        override = overrides.get(name, {})
        if "oss" in override:
            if override["oss"] == "private":
                names.add(name)
        elif tier in private_tiers:
            names.add(name)
    return names


def iter_manifests(repo: Path):
    """Yield `(name, path, capability_tier, description)` for every skill manifest."""
    for path in sorted((repo / "gtm_core" / "skills").glob("*.py")):
        if path.name.startswith("_") or path.name in {"base.py", "registry.py", "codegen.py"}:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "SKILL" for t in node.targets):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            kw = {k.arg: k.value for k in node.value.keywords}
            name = kw.get("name")
            desc = kw.get("description")
            tier = kw.get("capability_tier")
            if not isinstance(name, ast.Constant) or not isinstance(desc, ast.Constant):
                continue
            # `Tier.PRODUCTION` -> "production". Adjacent string literals inside the
            # parenthesised description are folded into one Constant by the parser.
            tier_value = tier.attr.lower() if isinstance(tier, ast.Attribute) else ""
            yield name.value, path, tier_value, desc.value


def scan(repo: Path) -> list[str]:
    allow = load_allowlist()
    private = private_skill_names(repo)
    findings: list[str] = []
    for name, path, _tier, desc in iter_manifests(repo):
        if name not in private:
            continue
        rel = path.relative_to(repo)
        if len(desc) > MAX_DESCRIPTION_CHARS and f"{name}:budget" not in allow:
            findings.append(
                f"  {rel}: description is {len(desc):,} chars "
                f"(budget {MAX_DESCRIPTION_CHARS:,}) — move the operating notes into "
                f"plugin/skills/{name}/body_template.md"
            )
        for rule, pattern, why in _RULES:
            if f"{name}:{rule}" in allow:
                continue
            for m in pattern.finditer(desc):
                findings.append(f"  {rel}: [{rule}] {m.group(0)!r} — {why}")
    return findings


def main(argv: list[str] | None = None) -> int:
    repo = Path(argv[0]).resolve() if argv else REPO

    tier_findings = scan_tier_mismatch(repo)
    if tier_findings:
        print(
            "✗ a skill that GENERATES media would ship its body in the OSS carve.\n"
            "  The stub/strip machinery keys off capability_tier, so a mis-declared tier is\n"
            "  invisible to every gate downstream of it — which is exactly how a renderer\n"
            "  shipped public for seventeen days.\n"
        )
        for f in tier_findings:
            print(f)
        return 1

    findings = scan(repo)
    if not findings:
        return 0
    print(
        "✗ withheld-skill description carries operating notes that would ship in the OSS "
        "carve.\n  The body is stubbed and the docstring is stripped, but `description` is "
        "rendered\n  into the stub's SKILL.md frontmatter — so it ships. Move measurements "
        "and dated\n  findings into the skill's body_template.md, beside the step they bear "
        "on.\n"
    )
    for f in findings[:40]:
        print(f)
    if len(findings) > 40:
        print(f"  ... and {len(findings) - 40} more")
    print(
        f"\n  Deliberate exception? Add '<skill>:<rule>' to {ALLOWLIST.name} with a dated reason."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
