"""SKILL.md codegen — the manifest is canonical; SKILL.md is generated.

Stdlib-only (gtm_core is SDK-free and dependency-free): frontmatter is emitted by
hand (no PyYAML), and the prompt body is copied verbatim from
plugin/skills/<name>/body_template.md so migration can never mangle a prompt.
Cowork keeps reading the generated SKILL.md; code runtimes import the manifest.

CLI:
  python -m gtm_core.skills.codegen migrate <name>    # SKILL.md -> body_template.md (one-time)
  python -m gtm_core.skills.codegen generate <name>   # manifest + body_template -> SKILL.md
  python -m gtm_core.skills.codegen generate-all
  python -m gtm_core.skills.codegen check             # exit 1 if any committed SKILL.md is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .base import GTMSkill

_OPEN = "---\n"
_CLOSE = "\n---\n"
_WRAP_WIDTH = 92


def _repo_root() -> Path:
    # gtm_core/skills/codegen.py -> parents[2] is the repo root in a dev/CI checkout.
    return Path(__file__).resolve().parents[2]


def default_plugin_root() -> Path:
    return _repo_root() / "plugin"


def skill_md_path(plugin_root: Path, name: str) -> Path:
    return plugin_root / "skills" / name / "SKILL.md"


def body_path(plugin_root: Path, name: str) -> Path:
    return plugin_root / "skills" / name / "body_template.md"


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_text, body_text). Raises ValueError if absent."""
    if not text.startswith(_OPEN):
        raise ValueError("file does not start with '---' frontmatter")
    end = text.find(_CLOSE, len(_OPEN))
    if end == -1:
        raise ValueError("unterminated frontmatter (no closing '---')")
    return text[len(_OPEN) : end], text[end + len(_CLOSE) :]


def _wrap(text: str, width: int = _WRAP_WIDTH) -> list[str]:
    """Deterministic greedy word-wrap for the folded description block."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_frontmatter(skill: GTMSkill) -> str:
    """Emit deterministic YAML frontmatter. `description` is a folded block scalar
    (`>-`) so newlines fold to spaces — the same value the prior hand-authored
    `>` block produced, minus the trailing newline."""
    out = ["---", f"name: {skill.name}", "description: >-"]
    out.extend(f"  {line}" for line in _wrap(skill.description))
    if skill.license:
        out.append(f"license: {skill.license}")
    out.append("metadata:")
    out.append(f'  version: "{skill.version}"')
    if skill.phase is not None:
        out.append(f'  phase: "{skill.phase}"')
    out.append(f"  capability_tier: {skill.capability_tier.value}")
    if skill.requires_capability:
        out.append(f"  requires_capability: [{', '.join(skill.requires_capability)}]")
    out.append("---")
    return "\n".join(out) + "\n"


OPERATOR_CLOSE_BLOCK = """## How to close this run (every surface)

Report, in this order and in the operator register (the `gtm-operator` output style): Lead with the outcome; what matters about it in their terms; the next decision as a choice they can answer; and what it cost, exactly as the ledger reported it, if anything metered ran.
File paths, commands, module names and raw output go in a final
<details><summary>Details</summary> … </details> block; the main reply must make sense
without it.

Markers: emit a ⟦…⟧ marker (⟦GATE:…⟧, ⟦POST⟧, ⟦FILE:…⟧) only when your system prompt carries
a `Surface:` line that says so. Otherwise show the same content as a quoted block headed
"This is exactly what would go out."

Active profile: the one in your system instructions, or, in the desktop app, the answer to
`uv run python -m gtm_core.active_profile show`.
"""


def render(skill: GTMSkill, body: str) -> str:
    text = render_frontmatter(skill) + body
    if skill.fallback_note:
        text = text.rstrip("\n") + (
            "\n\n## Degraded mode (no paid connectors)\n\n" + skill.fallback_note + "\n"
        )
    return text.rstrip("\n") + "\n\n" + OPERATOR_CLOSE_BLOCK


def generate(skill: GTMSkill, plugin_root: Path | None = None) -> Path:
    plugin_root = plugin_root or default_plugin_root()
    body = body_path(plugin_root, skill.name).read_text(encoding="utf-8")
    target = skill_md_path(plugin_root, skill.name)
    target.write_text(render(skill, body), encoding="utf-8")
    return target


# ── skills index (docs/SKILLS.md): the authoritative human-readable inventory ──
# Generated alongside SKILL.md from the same manifests so the skill list can never
# silently drift out of the prose docs. `check()` fails if the committed index is
# stale — the same gate that guards SKILL.md — so adding a skill *forces* a
# regenerated index before CI (and the OSS export) will pass. Prose docs link here.


def skills_index_path(plugin_root: Path) -> Path:
    return plugin_root.parent / "docs" / "SKILLS.md"


def say_phrases(description: str) -> tuple[str, ...]:
    """Parse trigger phrases from a skill's description.

    Finds the clause starting with 'says' and extracts all double-quoted
    strings that follow it. Returns () if no such clause exists.
    """
    import re

    match = re.search(r"\bsays\b(.*)", description, re.IGNORECASE | re.DOTALL)
    if not match:
        return ()
    tail = match.group(1)
    quotes = re.findall(r'"([^"]+)"', tail)
    return tuple(quotes)


def _say_cell(description: str) -> str:
    phrases = say_phrases(description)
    if not phrases:
        return ""
    return ", ".join(f'"{p}"' for p in phrases)


def _blurb(description: str, width: int = 116) -> str:
    """One-line table cell from a (long) description — markdown stripped, word-boundary truncation."""
    clean = description.split("Trigger when the user says")[0].strip()
    d = " ".join(clean.replace("**", "").replace("`", "").split())
    if len(d) <= width:
        return d
    return d[:width].rsplit(" ", 1)[0].rstrip(",;:—- ") + "…"


def render_index(skills: list[GTMSkill]) -> str:
    rows = [
        f"| [`{s.name}`](../plugin/skills/{s.name}/SKILL.md) | {s.capability_tier.value} "
        f"| {', '.join(s.requires_capability) if s.requires_capability else '—'} "
        f"| {_say_cell(s.description)} "
        f"| {_blurb(s.description)} |"
        for s in sorted(skills, key=lambda x: x.name)
    ]
    header = [
        "<!-- GENERATED — DO NOT EDIT. The authoritative skill inventory.",
        "     Regenerate: `python -m gtm_core.skills.codegen generate-all`.",
        "     CI (tests/skills/test_codegen.py / skill_codegen_sync) fails if this drifts. -->",
        "",
        "# Skill index",
        "",
        f"**{len(skills)} skills**, generated from the manifests in `gtm_core/skills/`. This is the "
        "single source of truth for the skill inventory — other docs link here rather than restate it.",
        "",
        "| Skill | Tier | Requires product capability | Say this | What it does |",
        "|---|---|---|---|---|",
    ]
    return "\n".join(header + rows) + "\n"


def generate_index(skills: list[GTMSkill] | None = None, plugin_root: Path | None = None) -> Path:
    plugin_root = plugin_root or default_plugin_root()
    if skills is None:
        from . import registry

        skills = registry.all_skills()
    target = skills_index_path(plugin_root)
    target.write_text(render_index(skills), encoding="utf-8")
    return target


def migrate(name: str, plugin_root: Path | None = None) -> Path:
    """One-time: split an existing SKILL.md and write its body verbatim to body_template.md."""
    plugin_root = plugin_root or default_plugin_root()
    text = skill_md_path(plugin_root, name).read_text(encoding="utf-8")
    _, body = split_frontmatter(text)
    target = body_path(plugin_root, name)
    target.write_text(body, encoding="utf-8")
    return target


def check(plugin_root: Path | None = None) -> list[str]:
    """Return the generated artifacts (SKILL.md names and/or the skills index) that
    differ from a fresh render — i.e. everything a `generate-all` would rewrite."""
    from . import registry

    plugin_root = plugin_root or default_plugin_root()
    skills = registry.all_skills()
    drift: list[str] = []
    for skill in skills:
        # A brand-new manifest has no generated SKILL.md yet (and may not have had its
        # body written). That is drift — the state a `generate-all` resolves — not an
        # error: reading it blind raised FileNotFoundError and buried the gate's
        # actionable message under a traceback.
        body_file = body_path(plugin_root, skill.name)
        md_file = skill_md_path(plugin_root, skill.name)
        if not body_file.exists():
            if md_file.exists():
                # SKILL.md exists with no source template — a private-distribution stub
                # (gtm_core.gating's OSS-carve paid-tier stub), not an ungenerated skill.
                # Nothing for codegen to check here.
                continue
            drift.append(f"{skill.name} (no body_template.md)")
            continue
        if not md_file.exists():
            drift.append(f"{skill.name} (no SKILL.md)")
            continue
        if render(skill, body_file.read_text(encoding="utf-8")) != md_file.read_text(
            encoding="utf-8"
        ):
            drift.append(skill.name)
    idx = skills_index_path(plugin_root)
    if not idx.exists() or render_index(skills) != idx.read_text(encoding="utf-8"):
        drift.append("docs/SKILLS.md")
    return drift


def generate_overlays(overlays_root: Path | None = None) -> list[Path]:
    from . import registry

    if overlays_root is None:
        overlays_root = (
            Path(__file__).resolve().parents[2] / "oss" / "overlays" / "plugin" / "skills"
        )
    if not overlays_root.is_dir():
        return []
    skills_by_name = {s.name: s for s in registry.all_skills()}
    written: list[Path] = []
    for body_file in sorted(overlays_root.glob("*/body_template.md")):
        skill_name = body_file.parent.name
        skill = skills_by_name.get(skill_name)
        if skill is None:
            continue
        target = body_file.parent / "SKILL.md"
        target.write_text(render(skill, body_file.read_text(encoding="utf-8")), encoding="utf-8")
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gtm_core.skills.codegen")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("migrate", "generate"):
        sp = sub.add_parser(name)
        sp.add_argument("name")
    sub.add_parser("generate-all")
    sub.add_parser("generate-overlays")
    sub.add_parser("check")
    args = parser.parse_args(argv)

    from . import registry

    if args.cmd == "migrate":
        print(f"wrote {migrate(args.name)}")
        return 0
    if args.cmd == "generate":
        skill = next((s for s in registry.all_skills() if s.name == args.name), None)
        if skill is None:
            print(f"no manifest registered for {args.name!r}", file=sys.stderr)
            return 2
        print(f"wrote {generate(skill)}")
        return 0
    if args.cmd == "generate-all":
        skills = registry.all_skills()
        for skill in skills:
            print(f"wrote {generate(skill)}")
        print(f"wrote {generate_index(skills)}")
        return 0
    if args.cmd == "generate-overlays":
        for path in generate_overlays():
            print(f"wrote {path}")
        return 0
    if args.cmd == "check":
        drift = check()
        if drift:
            print(
                "✗ codegen-sync: stale generated files (run: generate-all): " + ", ".join(drift),
                file=sys.stderr,
            )
            return 1
        print("✓ codegen-sync: all SKILL.md + the skills index in sync")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
